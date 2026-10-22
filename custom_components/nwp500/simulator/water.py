"""Hot water use: the tank's heat, booked from the water drawn.

With a water meter, the library books the tank's heat
(`nwp500.sim.book`): from the last time the tank stood full, it takes out
the heat each draw carries and what the tank loses standing, and puts back
what the compressor and the elements put in. The book is what readiness
needs to say how much hot water the tank holds, how many showers, and
whether a bath is ready (`sim.time_to_ready`).

It is read from Home Assistant's recorder: the meter's flow, the pump's
runs and the inlet probe (`sim_home_assistant.read_water_use`), and the
heater's own sensors' changes (`read_heater_history`), the last 72 hours.
That is too much to read every minute: a runner rereads it every
`REFRESH`, and walks with the book it has in between. A probe on the cold
supply gives the mains the gallons are counted at (`on_the_mains`).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import HomeAssistant

from nwp500 import sim_adapters as sa
from nwp500.sim.book import Booked
from nwp500.sim.cold_supply import on_the_mains
from nwp500.sim_home_assistant import (
    INLET_LOOKBACK,
    Entities,
    HomeAssistantError,
    read_heater_history,
    read_water_use,
    record_inputs,
)

from .calibration import heater_entities
from .ha_client import InProcessClient

if TYPE_CHECKING:
    from nwp500.sim_home_assistant import HomeAssistantClient

    from .runner import SimulatorRunner

#: How often the book is read again.
REFRESH = timedelta(minutes=10)
#: How far back it reads: the book anchors within this, the mains' long
#: draws are found within it.
LOOKBACK = INLET_LOOKBACK


@dataclass(frozen=True)
class WaterBook:
    """The book as last read, and what it rests on.

    `book` is None when there is none to read: no full tank in the
    history, the heater or the meter not heard from throughout, and so
    on; `notes` say why. `inlet_f` is the mains from the probe's long
    draws, and `inlet_measured` whether the book counts gallons at it.
    """

    at: datetime
    book: Booked | None
    inlet_f: float | None = None
    inlet_measured: bool = False
    notes: tuple[str, ...] = ()

    def as_document(self) -> dict[str, Any]:
        """For diagnostics."""
        book = self.book
        return {
            "at": self.at.isoformat(),
            "booked": book is not None,
            "heat_btu": None if book is None else round(book.heat_btu),
            "anchor_age_h": (
                None if book is None else round(book.anchor_age_h, 2)
            ),
            "inlet_f": None if self.inlet_f is None else round(self.inlet_f, 1),
            "inlet_measured": self.inlet_measured,
            "notes": list(self.notes),
        }


async def async_read_book(
    hass: HomeAssistant, runner: SimulatorRunner, now: datetime
) -> WaterBook:
    """Read the heater's water use and book its heat, as of `now`."""
    client = cast("HomeAssistantClient", InProcessClient(hass))
    entities = runner.water_entities
    use = await read_water_use(client, entities, now=now, lookback=LOOKBACK)
    notes = [f"water: {note}" for note in use.notes]
    inlet_f = None if use.cold is None else use.cold.t_cold_f

    def without(why: str) -> WaterBook:
        return WaterBook(now, None, inlet_f, False, (*notes, f"book: {why}"))

    if not use.flow:
        return without("the meter's flow is not read")
    if use.runs is None:
        # A pump whose runs are unknown may have moved water the meter
        # counted: booked as draws, it would empty the tank on paper.
        return without("the circulation pump's runs are unknown")
    if len(runner.coordinator.data or {}) > 1:
        # The last response time is the account's, on its first heater: it
        # does not say when each heater was heard from.
        return without(
            "Home Assistant's history does not say when each heater of an "
            "account with several was heard from"
        )
    heater, missing = heater_entities(hass, runner.mac_address)
    if heater is None:
        return without(
            "Home Assistant's recorder does not keep the heater's "
            + ", ".join(missing)
        )
    feature = runner.coordinator.device_features.get(runner.mac_address)
    if feature is None:
        return without("the heater's features are not read yet")
    params = runner.params if runner.params is not None else sa.params(feature)
    start = now - LOOKBACK
    try:
        history = await read_heater_history(client, heater, start, now)
        air: list[tuple[datetime, float]] = []
        if runner.entities.air is not None:
            record = await record_inputs(
                client, Entities(air=runner.entities.air), start, now
            )
            air = record.air or []
    except HomeAssistantError as err:
        return without(f"the heater's history is not read ({err})")
    # The book takes the simulator's own units, as the readers give them.
    try:
        book = await hass.async_add_executor_job(
            functools.partial(
                sa.book_from_changes,
                history.rows,
                history.observed,
                params,
                at=now,
                flow=use.flow,
                circulation=use.runs,
                inlet=use.probe,
                air=air,
            )
        )
    except ValueError as err:
        return without(str(err))
    if book is None:
        return without(
            "no full tank, quiet after heating, in the last "
            f"{LOOKBACK.total_seconds() / 3600:g} hours to count from"
        )
    anchored = now - timedelta(hours=book.anchor_age_h)
    if params.circulation is None and any(
        end > anchored for _, end in use.runs
    ):
        # Without the loop's terms a run's flow the meter reads is booked
        # as draws, and the heat the loop loses is not booked at all.
        return without(
            "the circulation pump ran since the tank was last full, and its "
            "loop's terms are not fitted"
        )
    measured = False
    if use.cold is not None:
        book, measured = on_the_mains(book, use.cold)
    return WaterBook(now, book, inlet_f, measured, tuple(notes))


__all__ = ["LOOKBACK", "REFRESH", "WaterBook", "async_read_book"]
