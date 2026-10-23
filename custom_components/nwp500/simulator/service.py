"""The simulator's actions: the library's calls on a heater's scenario.

Each action builds its scenario as `nwp-cli simulate` does: the heater's
live scenario (the one the entities' answer comes from), with the caller's
scenario document (`nwp500.sim.codec`) laid over it by
`nwp500.sim_live.overlay`, then the action's own fields. With `use_heater`
off the heater is not read: the document alone is the scenario. The
parameters are the document's when it has them, else the defaults for the
heater's tank.

Each answer is the library's document under `result`, in its units (°F,
Wh, US gallons); the integration's notes on the inputs are beside it under
`inputs`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from nwp500 import sim
from nwp500.sim import codec
from nwp500.sim.engine import SimulationError
from nwp500.sim.profile import heater_profile
from nwp500.sim_live import overlay

from ..const import DOMAIN, MAX_SIMULATE_HOURS, MODE_TO_DHW_ID
from .runner import WAIT_STANDBY, Inputs

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .runner import SimulatorRunner

# One walk at a time: a week's walk takes a CPU for a while.
_LOCK = asyncio.Lock()


def _writes(
    hass: HomeAssistant, writes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The caller's writes as the scenario takes them.

    Their temperatures are in Home Assistant's unit.
    """
    unit = hass.config.units.temperature_unit
    out: list[dict[str, Any]] = []
    for write in writes:
        doc: dict[str, Any] = {"at": dt_util.as_utc(write["at"])}
        if (raw := write.get("temperature")) is not None:
            doc["setpoint_f"] = TemperatureConverter.convert(
                float(raw), unit, UnitOfTemperature.FAHRENHEIT
            )
        if (mode := write.get("mode")) is not None:
            doc["mode"] = MODE_TO_DHW_ID[mode]
        out.append(doc)
    return out


async def async_inputs(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    scenario: dict[str, Any] | None,
    use_heater: bool,
    over: dict[str, Any] | None = None,
) -> Inputs:
    """The scenario to walk: the heater's, `scenario` over it, then `over`."""
    live: Inputs | None = None
    if use_heater:
        try:
            got = await runner.async_inputs()
        except ValueError as err:
            # The library refused what the heater reported.
            raise HomeAssistantError(
                f"The simulator can't use the heater's data: {err}"
            ) from err
        if got == WAIT_STANDBY:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="simulator_standby"
            )
        if isinstance(got, str):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="simulator_no_answer",
                translation_placeholders={"reason": got},
            )
        live = got
    try:
        built = overlay(
            None if live is None else live.live,
            scenario,
            now=dt_util.utcnow().replace(microsecond=0),
            tz=str(hass.config.time_zone),
            over=over,
        )
    except ValueError as err:
        # The heater's part is a scenario on its own: what is refused is
        # the caller's.
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="simulator_invalid_scenario",
            translation_placeholders={"error": str(err)},
        ) from err
    if built.scenario.horizon > timedelta(hours=MAX_SIMULATE_HOURS):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="simulator_horizon_too_long",
            translation_placeholders={"hours": str(MAX_SIMULATE_HOURS)},
        )
    # The heater's book is its tank's heat now: only for a scenario that
    # starts then, with the heater's parameters.
    book_notes: tuple[str, ...] = ()
    book = None
    inlet_measured = False
    if live is not None and live.book is not None:
        if (
            built.scenario.start == live.scenario.start
            and built.params == live.params
        ):
            book, inlet_measured = live.book, live.inlet_measured
        else:
            book_notes = (
                (
                    "book: not used; the scenario's start or parameters "
                    "are not the heater's"
                ),
            )
    return Inputs(
        scenario=built.scenario,
        params=built.params,
        reasons=() if live is None else live.reasons,
        notes=(
            *(() if live is None else live.live_notes),
            *built.notes,
            *book_notes,
        ),
        book=book,
        inlet_measured=inlet_measured,
    )


async def _async_call(hass: HomeAssistant, call: Callable[[], Any]) -> Any:
    """`call` in the executor, one at a time."""
    async with _LOCK:
        try:
            return await hass.async_add_executor_job(call)
        except (SimulationError, ValueError) as err:
            # Such as a setpoint out of the heater's range: the library's
            # message says which.
            raise HomeAssistantError(
                f"The simulator refused the scenario: {err}"
            ) from err


def _response(document: dict[str, Any], inputs: Inputs) -> dict[str, Any]:
    return {
        "result": document,
        "inputs": {
            "reasons": list(inputs.reasons),
            "notes": list(inputs.notes),
        },
    }


async def async_get_scenario(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    scenario: dict[str, Any] | None = None,
    use_heater: bool = True,
) -> dict[str, Any]:
    """The scenario the other actions would walk, as a document to edit."""
    inputs = await async_inputs(
        hass, runner, scenario=scenario, use_heater=use_heater
    )
    return _response(
        codec.scenario_to_document(inputs.scenario, inputs.params), inputs
    )


async def async_simulate(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    scenario: dict[str, Any] | None = None,
    use_heater: bool = True,
    hours: int | None = None,
    writes: list[dict[str, Any]] | None = None,
    include_points: bool = False,
) -> dict[str, Any]:
    """Walk the scenario (`sim.simulate`).

    `hours` and `writes` are laid over the document's: an empty `writes`
    clears its writes, an omitted one keeps them.
    """
    over: dict[str, Any] = {}
    if hours is not None:
        over["horizon"] = timedelta(hours=hours)
    if writes is not None:
        over["commands"] = {"writes": _writes(hass, writes)}
    inputs = await async_inputs(
        hass, runner, scenario=scenario, use_heater=use_heater, over=over
    )
    result = await _async_call(
        hass,
        partial(
            sim.simulate,
            inputs.scenario,
            inputs.params,
            book=inputs.book,
            inlet_measured=inputs.inlet_measured,
        ),
    )
    document = codec.result_to_document(result)
    if not include_points:
        document.pop("points", None)
        document.pop("delivery", None)
    return _response(document, inputs)


async def async_time_to_ready(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    scenario: dict[str, Any] | None = None,
    use_heater: bool = True,
    bath_gal: float | None = None,
    shower_gal: float | None = None,
) -> dict[str, Any]:
    """Hot water at the start and ahead (`sim.time_to_ready`)."""
    inputs = await async_inputs(
        hass, runner, scenario=scenario, use_heater=use_heater
    )
    ready = await _async_call(
        hass,
        partial(
            sim.time_to_ready,
            inputs.scenario,
            inputs.params,
            book=inputs.book,
            bath_gal=bath_gal,
            shower_gal=shower_gal,
            inlet_measured=inputs.inlet_measured,
        ),
    )
    return _response(codec.to_document(ready), inputs)


async def async_next_recovery(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    scenario: dict[str, Any] | None = None,
    use_heater: bool = True,
) -> dict[str, Any]:
    """The heat-pump recovery the heater runs next (`sim.next_recovery`)."""
    inputs = await async_inputs(
        hass, runner, scenario=scenario, use_heater=use_heater
    )
    recovery = await _async_call(
        hass, partial(sim.next_recovery, inputs.scenario, inputs.params)
    )
    return _response(codec.to_document(recovery), inputs)


async def async_forecast(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    target_time: datetime,
    scenario: dict[str, Any] | None = None,
    use_heater: bool = True,
    target_f: float | None = None,
    target_lower_f: float | None = None,
) -> dict[str, Any]:
    """When a recovery must start to be ready at `target_time`.

    From the scenario's start (`sim.forecast`).
    """
    inputs = await async_inputs(
        hass, runner, scenario=scenario, use_heater=use_heater
    )
    s = inputs.scenario
    forecast = await _async_call(
        hass,
        partial(
            sim.forecast,
            s.state,
            s.settings,
            s.environment,
            inputs.params,
            at=s.start,
            target_time=dt_util.as_utc(target_time),
            target_f=target_f,
            target_lower_f=target_lower_f,
            loads=s.loads,
            history=s.history,
        ),
    )
    return _response(codec.to_document(forecast), inputs)


async def async_heater_profile(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    scenario: dict[str, Any] | None = None,
    use_heater: bool = True,
) -> dict[str, Any]:
    """The modes, setpoints and tank the simulation runs by.

    For the scenario's parameters (`nwp500.sim.profile.heater_profile`).
    """
    inputs = await async_inputs(
        hass, runner, scenario=scenario, use_heater=use_heater
    )
    params = inputs.params
    return _response(
        codec.to_document(heater_profile(params.tank, params.readiness.line_f)),
        inputs,
    )
