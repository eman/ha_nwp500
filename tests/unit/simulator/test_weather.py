"""The air beside the heater, projected from a weather forecast.

The library fits how that air follows the outdoor air, and projects it
from the forecast; the integration maps the entities and keeps the fit
between walks, refitting at most hourly.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from nwp500.sim.location_air import FittedLocationAir, LocationAirModel
from nwp500.sim_home_assistant import HomeAssistantError

from custom_components.nwp500.const import DOMAIN
from custom_components.nwp500.simulator.ha_client import InProcessClient
from custom_components.nwp500.simulator.runner import (
    HORIZON,
    Inputs,
    SimulatorRunner,
)

from .conftest import MAC
from .test_hot_water import started, stop

WEATHER = {"weather_entity": "weather.home"}
FIT = FittedLocationAir(
    model=LocationAirModel(damping=0.5, lag=timedelta(hours=2), offset_f=30.0),
    r=0.9,
    n=400,
)


def _register_heater(hass: HomeAssistant) -> dict[str, str]:
    """The heater's entities as this integration registers them."""
    registry = er.async_get(hass)
    return {
        key: registry.async_get_or_create(
            domain, DOMAIN, f"{MAC}_{key}"
        ).entity_id
        for domain, key in (
            ("sensor", "diagnostic_last_response"),
            ("sensor", "tank_upper_temperature"),
            ("sensor", "tank_lower_temperature"),
            ("sensor", "ambient_temperature"),
            ("binary_sensor", "comp_use"),
        )
    }


def _forecast(hass: HomeAssistant) -> list[ServiceCall]:
    calls: list[ServiceCall] = []
    at = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    hours = [
        {
            "datetime": (at + timedelta(hours=h)).isoformat(),
            "temperature": 40.0 + h,
        }
        for h in range(-3, 30)
    ]

    async def handle(call: ServiceCall) -> dict[str, Any]:
        calls.append(call)
        return {"weather.home": {"forecast": hours}}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        handle,
        supports_response=SupportsResponse.ONLY,
    )
    hass.states.async_set(
        "weather.home", "sunny", {"temperature": 41, "temperature_unit": "°F"}
    )
    return calls


async def test_the_heaters_own_air_and_compressor_are_mapped(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    runner: SimulatorRunner = await started(hass, coordinator, WEATHER)
    # Not registered yet: no heater to fit on.
    assert runner._heater_air() is None  # noqa: SLF001

    ids = _register_heater(hass)
    heater = runner._heater_air()  # noqa: SLF001

    assert heater is not None
    assert heater.heard == ids["diagnostic_last_response"]
    assert heater.upper == ids["tank_upper_temperature"]
    assert heater.lower == ids["tank_lower_temperature"]
    assert heater.ambient == ids["ambient_temperature"]
    assert heater.compressor == ids["comp_use"]
    assert runner.entities.weather == "weather.home"
    await stop(hass)


async def test_the_air_model_is_fitted_hourly_not_every_walk(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    forecasts = _forecast(hass)
    runner = await started(hass, coordinator, WEATHER)
    read = AsyncMock(side_effect=HomeAssistantError("not recorded"))
    now = dt_util.utcnow().replace(microsecond=0)
    with patch.object(InProcessClient, "read_history", read):
        runner.air_fit = None
        await runner.async_inputs(start=now)
        # The fit read the heater's air and compressor, and gave none.
        assert read.await_count == 1
        assert runner.air_fit == (now, None)

        forecasts.clear()
        inputs = await runner.async_inputs(start=now + timedelta(minutes=59))
        # No refit, and no forecast without a model to project it through.
        assert read.await_count == 1
        assert forecasts == []
        assert isinstance(inputs, Inputs)

        await runner.async_inputs(start=now + timedelta(minutes=61))
        assert read.await_count == 2
    await stop(hass)


async def test_the_kept_model_projects_the_forecast(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    forecasts = _forecast(hass)
    runner = await started(hass, coordinator, WEATHER)
    now = dt_util.utcnow().replace(microsecond=0)
    runner.air_fit = (now, FIT)
    read = AsyncMock(side_effect=AssertionError("refitted"))

    with patch.object(InProcessClient, "read_history", read):
        environment = runner._environment(now, now + HORIZON)  # noqa: SLF001
        assert environment is not None
        assert environment.location_air == FIT.model
        assert environment.heater == runner._heater_air()  # noqa: SLF001

        forecasts.clear()
        inputs = await runner.async_inputs(start=now + timedelta(minutes=5))

    assert isinstance(inputs, Inputs)
    assert forecasts
    ambient = inputs.scenario.environment.ambient_f
    # The library's projection, through the kept model.
    assert isinstance(ambient, tuple)
    assert ambient
    assert any("projected from the outdoor forecast" in n for n in inputs.notes)
    # Kept, not refitted.
    assert runner.air_fit == (now, FIT)
    await stop(hass)


async def test_without_weather_nothing_is_fitted(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    runner = await started(hass, coordinator, {})
    now = dt_util.utcnow().replace(microsecond=0)

    assert runner._environment(now, now + HORIZON) is None  # noqa: SLF001
    await runner.async_inputs(start=now)
    assert runner.air_fit is None
    await stop(hass)


async def test_a_second_heater_reads_the_shared_last_response(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    ids = _register_heater(hass)
    runner = await started(hass, coordinator, WEATHER)
    first = "aabbccddeeff"
    registry = er.async_get(hass)
    shared = registry.async_get_or_create(
        "sensor", DOMAIN, f"{first}_diagnostic_last_response"
    ).entity_id
    # Another heater came first: the integration's one last response
    # sensor is on it.
    coordinator.data = {first: {}, **coordinator.data}

    heater = runner._heater_air()  # noqa: SLF001

    assert heater is not None
    assert heater.heard == shared
    # This heater's own probes, air and compressor.
    assert heater.upper == ids["tank_upper_temperature"]
    assert heater.ambient == ids["ambient_temperature"]
    assert heater.compressor == ids["comp_use"]
    await stop(hass)


async def test_a_disabled_entity_is_not_given_to_the_library(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    ids = _register_heater(hass)
    registry = er.async_get(hass)
    # As the ambient sensor is registered by default.
    registry.async_update_entity(
        ids["ambient_temperature"],
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    runner = await started(hass, coordinator, WEATHER)

    heater = runner._heater_air()  # noqa: SLF001

    assert heater is not None
    assert heater.ambient is None
    assert heater.compressor == ids["comp_use"]
    await stop(hass)


async def test_two_callers_due_a_fit_fit_once(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    import asyncio

    _register_heater(hass)
    _forecast(hass)
    runner = await started(hass, coordinator, WEATHER)
    read = AsyncMock(side_effect=HomeAssistantError("not recorded"))
    now = dt_util.utcnow().replace(microsecond=0)
    with patch.object(InProcessClient, "read_history", read):
        runner.air_fit = None
        # Such as an action while a walk reads.
        await asyncio.gather(
            runner.async_inputs(start=now),
            runner.async_inputs(start=now + timedelta(seconds=1)),
        )
    assert read.await_count == 1
    await stop(hass)
