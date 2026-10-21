"""`nwp500.simulate`: walk a heater's scenario on request.

The scenario is the one the entities' answer comes from, over the hours
asked for, with any setpoint and mode writes the caller wants to try. The
answer is the library's result document, in its units (°F, Wh, US
gallons), named under `units`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from nwp500 import sim
from nwp500.sim import codec
from nwp500.sim.engine import (
    HEATER_SETPOINT_MAX_F,
    HEATER_SETPOINT_MIN_F,
    SimulationError,
)
from nwp500.sim.inputs import Commands, Write

from ..const import DOMAIN, MODE_TO_DHW_ID
from .runner import WAIT_STANDBY, answer_from

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .runner import SimulatorRunner

# The units of the result document.
UNITS = {"temperature": "°F", "energy": "Wh", "volume": "gal"}

# One walk at a time across the integration: a week's walk takes a CPU
# for most of a second.
_LOCK = asyncio.Lock()


def _writes(
    hass: HomeAssistant, writes: list[dict[str, Any]]
) -> tuple[Write, ...]:
    """The caller's writes, their temperatures in Home Assistant's unit."""
    unit = hass.config.units.temperature_unit

    def shown(fahrenheit: float) -> str:
        value = TemperatureConverter.convert(
            fahrenheit, UnitOfTemperature.FAHRENHEIT, unit
        )
        return f"{value:.1f} {unit}"

    out: list[Write] = []
    for write in writes:
        raw = write.get("temperature")
        temperature = None if raw is None else float(raw)
        mode = write.get("mode")
        setpoint_f = (
            None
            if temperature is None
            else TemperatureConverter.convert(
                temperature, unit, UnitOfTemperature.FAHRENHEIT
            )
        )
        if setpoint_f is not None and not (
            HEATER_SETPOINT_MIN_F - 1e-6
            <= setpoint_f
            <= HEATER_SETPOINT_MAX_F + 1e-6
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="simulator_setpoint_out_of_range",
                translation_placeholders={
                    "temperature": f"{temperature:.1f} {unit}",
                    "min": shown(HEATER_SETPOINT_MIN_F),
                    "max": shown(HEATER_SETPOINT_MAX_F),
                },
            )
        out.append(
            Write(
                at=dt_util.as_utc(write["at"]),
                setpoint_f=setpoint_f,
                mode=None if mode is None else MODE_TO_DHW_ID[mode],
            )
        )
    return tuple(out)


def _thin(points: list[Any], minutes: int) -> list[Any]:
    """The path thinned to a point every `minutes`.

    The first point, each point at least `minutes` after the last one
    kept, and the last point.
    """
    if len(points) < 3:
        return points
    gap = timedelta(minutes=minutes)
    kept = [points[0]]
    last = datetime.fromisoformat(points[0]["at"])
    for point in points[1:-1]:
        at = datetime.fromisoformat(point["at"])
        if at - last >= gap:
            kept.append(point)
            last = at
    kept.append(points[-1])
    return kept


async def async_simulate(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    hours: int,
    writes: list[dict[str, Any]],
    include_points: bool,
    resolution_minutes: int | None,
) -> dict[str, Any]:
    """Walk the heater's scenario now for `hours`, with `writes`."""
    start = dt_util.utcnow().replace(microsecond=0)
    until = start + timedelta(hours=hours)
    try:
        commands = Commands(writes=_writes(hass, writes)) if writes else None
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    for write in commands.writes if commands else ():
        if not start <= write.at < until:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="simulator_write_outside",
                translation_placeholders={
                    "at": write.at.isoformat(),
                    "start": start.isoformat(),
                    "until": until.isoformat(),
                },
            )
    try:
        inputs = await runner.async_inputs(
            start=start, horizon=timedelta(hours=hours), commands=commands
        )
    except ValueError as err:
        # The scenario refused what the checks above let through.
        raise ServiceValidationError(str(err)) from err
    if inputs == WAIT_STANDBY:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="simulator_standby"
        )
    if isinstance(inputs, str):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="simulator_no_answer",
            translation_placeholders={"reason": inputs},
        )
    async with _LOCK:
        try:
            result = await hass.async_add_executor_job(
                sim.simulate, inputs.scenario, inputs.params
            )
        except (SimulationError, ValueError) as err:
            raise HomeAssistantError(f"The simulation failed: {err}") from err
    document = codec.result_to_document(result)
    if include_points:
        if resolution_minutes:
            document["points"] = _thin(document["points"], resolution_minutes)
            document.pop("delivery", None)
    else:
        document.pop("points", None)
        document.pop("delivery", None)
    answer = answer_from(result, inputs)
    document["summary"] = {
        "state": answer.state,
        "starts_at": None if answer.start is None else answer.start.isoformat(),
        "ready_at": None if answer.end is None else answer.end.isoformat(),
        "source": answer.source,
        "energy_kwh": answer.energy_kwh,
        "energy_total_kwh": answer.energy_total_kwh,
    }
    document["reasons"] = list(answer.reasons)
    document["notes"] = list(inputs.notes)
    document["units"] = dict(UNITS)
    document["model"] = "physics"
    return document
