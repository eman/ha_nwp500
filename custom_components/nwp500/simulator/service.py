"""`nwp500.simulate`: walk a heater's scenario on request.

The scenario is the one the entities' answer comes from, over the hours
asked for, with any setpoint and mode writes the caller wants to try. The
answer is the library's result document (`nwp500.sim.codec`) under
`result`, in its units (°F, Wh, US gallons), without the tank's path unless
asked for; the integration's own notes on the inputs are beside it under
`inputs`.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from nwp500 import sim
from nwp500.sim import codec
from nwp500.sim.engine import SimulationError
from nwp500.sim.inputs import Commands, Write

from ..const import DOMAIN, MODE_TO_DHW_ID
from .runner import WAIT_STANDBY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .runner import SimulatorRunner

# One service walk at a time: a week's walk takes a CPU for a while.
_LOCK = asyncio.Lock()


def _writes(
    hass: HomeAssistant, writes: list[dict[str, Any]]
) -> tuple[Write, ...]:
    """The caller's writes, their temperatures in Home Assistant's unit."""
    unit = hass.config.units.temperature_unit
    out: list[Write] = []
    for write in writes:
        raw = write.get("temperature")
        mode = write.get("mode")
        out.append(
            Write(
                at=dt_util.as_utc(write["at"]),
                setpoint_f=(
                    None
                    if raw is None
                    else TemperatureConverter.convert(
                        float(raw), unit, UnitOfTemperature.FAHRENHEIT
                    )
                ),
                mode=None if mode is None else MODE_TO_DHW_ID[mode],
            )
        )
    return tuple(out)


async def async_simulate(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    *,
    hours: int,
    writes: list[dict[str, Any]],
    include_points: bool,
) -> dict[str, Any]:
    """Walk the heater's scenario now for `hours`, with `writes`."""
    start = dt_util.utcnow().replace(microsecond=0)
    until = start + timedelta(hours=hours)
    try:
        commands = Commands(writes=_writes(hass, writes)) if writes else None
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    # The caller's own mistake, told apart before the scenario is built:
    # the scenario refuses it too, but as it refuses the heater's data.
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
        # The library refused what the heater reported.
        raise HomeAssistantError(
            f"The simulator can't use the heater's data: {err}"
        ) from err
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
            # Such as a setpoint out of the heater's range: the library's
            # message says which.
            raise HomeAssistantError(
                f"The simulator refused the scenario: {err}"
            ) from err
    document = codec.result_to_document(result)
    if not include_points:
        document.pop("points", None)
        document.pop("delivery", None)
    return {
        "result": document,
        "inputs": {
            "reasons": list(inputs.reasons),
            "notes": list(inputs.notes),
        },
    }
