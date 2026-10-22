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
from nwp500.sim.params import (
    BookParams,
    CirculationParams,
    HeaterParams,
    params_from_document,
    params_to_document,
)
from nwp500.sim_calibrate import METER_LAG, BookTermsFit, fit_book
from nwp500.sim_home_assistant import (
    INLET_LOOKBACK,
    Entities,
    HeaterEntities,
    HeaterHistory,
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
#: The history the book's and the loop's terms are fitted on. A pair
#: needs a quiet full tank either side, and a pump running about once a
#: day gives ten pairs with a run in about four weeks.
BOOK_HISTORY = timedelta(days=42)
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


async def _heater(
    hass: HomeAssistant, runner: SimulatorRunner
) -> tuple[HeaterEntities, HeaterParams] | str:
    """The heater's recorded entities and parameters, or why not."""
    if "recorder" not in hass.config.components:
        return "Home Assistant's recorder is not running"
    if len(runner.coordinator.data or {}) > 1:
        # The last response time is the account's, on its first heater: it
        # does not say when each heater was heard from.
        return (
            "Home Assistant's history does not say when each heater of an "
            "account with several was heard from"
        )
    heater, missing = heater_entities(hass, runner.mac_address)
    if heater is None:
        return "Home Assistant's recorder does not keep the heater's " + (
            ", ".join(missing)
        )
    feature = runner.coordinator.device_features.get(runner.mac_address)
    if feature is None:
        return "the heater's features are not read yet"
    return heater, runner.walk_params(feature)


async def _history(
    client: HomeAssistantClient,
    runner: SimulatorRunner,
    heater: HeaterEntities,
    start: datetime,
    now: datetime,
) -> tuple[HeaterHistory, list[tuple[datetime, float]]]:
    """The heater's changes and the air beside it, from `start` to `now`."""
    history = await read_heater_history(client, heater, start, now)
    air: list[tuple[datetime, float]] = []
    if runner.entities.air is not None:
        record = await record_inputs(
            client, Entities(air=runner.entities.air), start, now
        )
        air = record.air or []
    return history, air


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
    found = await _heater(hass, runner)
    if isinstance(found, str):
        return without(found)
    heater, params = found
    try:
        history, air = await _history(
            client, runner, heater, now - LOOKBACK, now
        )
    except HomeAssistantError as err:
        return without(f"the heater's history is not read ({err})")
    terms = params.circulation
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
                # A run at its own flow: the meter's excess over it is a
                # draw, not the loop's.
                circulation_flow=(
                    ()
                    if terms is None
                    else sa.run_flow(use.runs, terms.flow_gpm)
                ),
                # A run's water the meter's reports either side of it
                # carry, spread back over their intervals.
                meter_lag=METER_LAG,
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
    if terms is None and any(end > anchored for _, end in use.runs):
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


async def async_fit_book(
    hass: HomeAssistant, runner: SimulatorRunner, now: datetime
) -> BookTermsFit | None:
    """Fit, or keep, the book's terms and the loop's from the recorder.

    From the meter, the pump's runs where there is a pump, and the
    heater's own history over `BOOK_HISTORY`. None when the heater has no
    meter to fit them from.
    """
    entities = runner.water_entities
    if entities.meter_volume is None:
        return None
    feature = runner.coordinator.device_features.get(runner.mac_address)
    base = None if feature is None else runner.walk_params(feature)

    def without(why: str) -> BookTermsFit | None:
        if base is None:
            return None
        return BookTermsFit(
            book=base.book,
            circulation=base.circulation,
            kept="base",
            pairs=0,
            run_pairs=0,
            runs=0,
            error_btu=None,
            why=why,
        )

    found = await _heater(hass, runner)
    if isinstance(found, str):
        return without(found)
    heater, params = found
    client = cast("HomeAssistantClient", InProcessClient(hass))
    start = now - BOOK_HISTORY
    try:
        use = await read_water_use(
            client, entities, now=now, lookback=BOOK_HISTORY
        )
        if use.runs is None:
            # Unknown, a run's flow would be fitted as draws.
            return without("the circulation pump's runs are unknown")
        history, air = await _history(client, runner, heater, start, now)
    except HomeAssistantError as err:
        return without(f"the history is not read ({err})")
    try:
        return await hass.async_add_executor_job(
            functools.partial(
                fit_book,
                history.rows,
                history.observed,
                params,
                flow=use.flow,
                runs=use.runs,
                inlet=use.probe,
                air=air,
            )
        )
    except ValueError as err:
        return without(str(err))


def water_terms_to_document(params: HeaterParams) -> dict[str, Any]:
    """`params`, whose book and loop terms are kept, for the store."""
    return params_to_document(params)


def water_terms_from_document(
    doc: Any,
) -> tuple[BookParams, CirculationParams | None] | None:
    """The book's and loop's terms stored; None if unreadable."""
    if not isinstance(doc, dict):
        return None
    try:
        params = params_from_document(doc)
    except KeyError, TypeError, ValueError:
        return None
    return params.book, params.circulation


__all__ = [
    "BOOK_HISTORY",
    "LOOKBACK",
    "REFRESH",
    "WaterBook",
    "async_fit_book",
    "async_read_book",
    "water_terms_from_document",
    "water_terms_to_document",
]
