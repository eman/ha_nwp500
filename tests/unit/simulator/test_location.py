"""The air beside the heater, projected from a weather forecast."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from nwp500.sim.location_air import LocationAirModel
from nwp500.sim_home_assistant import HomeAssistantError

from custom_components.nwp500.simulator.ha_client import (
    FORECAST_TTL,
    InProcessClient,
)
from custom_components.nwp500.simulator.location import (
    KEPT_BASE,
    KEPT_REFIT,
    averaged,
    fit,
    model_from_document,
    model_to_document,
)
from custom_components.nwp500.simulator.runner import Inputs

from .test_runner import runner

T0 = datetime(2026, 10, 1, tzinfo=UTC)
TRUE = LocationAirModel(damping=0.5, lag=timedelta(hours=2), offset_f=30.0)


def _outdoor(days: float = 6) -> list[tuple[datetime, float]]:
    """A daily swing, hourly."""
    return [
        (
            T0 + timedelta(hours=h),
            55.0 + 15.0 * math.sin(2 * math.pi * h / 24),
        )
        for h in range(int(days * 24) + 1)
    ]


def _air(
    model: LocationAirModel, outdoor: Any, days: float = 6, noise: float = 0.3
) -> list[tuple[datetime, float]]:
    """The air by `model`, every 10 minutes, with deterministic noise."""
    when = [T0 + timedelta(minutes=10 * i) for i in range(int(days * 144))]
    values = model.at(outdoor, when)
    return [
        (t, v + noise * math.sin(i * 7.0))
        for i, (t, v) in enumerate(zip(when, values, strict=True))
    ]


def test_a_fit_finds_how_the_air_follows_the_outdoor_air():
    outdoor = _outdoor()
    done = fit(_air(TRUE, outdoor), outdoor, None)

    assert done.kept == KEPT_REFIT and done.model is not None
    assert done.model.damping == pytest.approx(0.5, abs=0.03)
    assert done.model.lag == timedelta(hours=2)
    assert done.r is not None and done.r > 0.95
    assert done.error is not None and done.error < 0.5
    assert done.base_error is None


def test_a_heaters_model_is_kept_when_the_refit_does_no_better():
    outdoor = _outdoor()
    air = _air(TRUE, outdoor)
    first = fit(air, outdoor, None)
    assert first.model is not None

    again = fit(air, outdoor, first.model)

    assert again.kept == KEPT_BASE and again.model == first.model
    assert again.why is not None


def test_a_worse_model_is_replaced():
    outdoor = _outdoor()
    worse = LocationAirModel(damping=0.9, lag=timedelta(0), offset_f=5.0)

    done = fit(_air(TRUE, outdoor), outdoor, worse)

    assert done.kept == KEPT_REFIT and done.model != worse
    assert done.base_error is not None and done.error is not None
    assert done.error < done.base_error


def test_too_little_history_keeps_what_there_is():
    outdoor = _outdoor(days=1)
    done = fit(_air(TRUE, outdoor, days=1), outdoor, TRUE)

    assert done.kept == KEPT_BASE and done.model == TRUE
    assert done.why is not None and "too little history" in done.why


def test_no_model_is_fitted_where_the_air_does_not_follow_the_outdoor_air():
    outdoor = _outdoor()
    # A heated room: the air holds, whatever the weather.
    air = [
        (T0 + timedelta(minutes=10 * i), 68.0 + 2.0 * math.sin(i * 1.3))
        for i in range(6 * 144)
    ]

    done = fit(air, outdoor, None)

    assert done.model is None and done.kept is None
    assert done.why is not None and "too loosely" in done.why


def test_readings_are_averaged_per_half_hour():
    readings = [
        (T0 + timedelta(minutes=1), 60.0),
        (T0 + timedelta(minutes=2), 62.0),
        (T0 + timedelta(minutes=31), 70.0),
    ]

    assert averaged(readings) == [
        (T0 + timedelta(minutes=15), 61.0),
        (T0 + timedelta(minutes=45), 70.0),
    ]


def test_a_model_survives_storage():
    assert model_from_document(model_to_document(TRUE)) == TRUE
    assert model_from_document(None) is None
    assert model_from_document({"damping": 0.5}) is None
    assert (
        model_from_document({"damping": "nan", "lag_hours": 2, "offset_f": 30})
        is None
    )


def _forecast_service(
    hass: HomeAssistant, hours: list[dict[str, Any]]
) -> list[ServiceCall]:
    calls: list[ServiceCall] = []

    async def handle(call: ServiceCall) -> dict[str, Any]:
        # As the weather integration's entity service answers: by entity.
        calls.append(call)
        entities = call.data["entity_id"]
        if isinstance(entities, str):
            entities = [entities]
        return {entity: {"forecast": hours} for entity in entities}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        handle,
        supports_response=SupportsResponse.ONLY,
    )
    return calls


@pytest.mark.asyncio
async def test_the_forecast_is_read_in_the_entitys_unit(hass: HomeAssistant):
    hass.states.async_set(
        "weather.home", "sunny", {"temperature": 10, "temperature_unit": "°C"}
    )
    at = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    calls = _forecast_service(
        hass,
        [
            {
                "datetime": (at + timedelta(hours=1)).isoformat(),
                "temperature": 12,
            },
            {"datetime": at.isoformat(), "temperature": 11},
        ],
    )

    hours, unit = await InProcessClient(hass).forecast("weather.home")

    assert unit == "°C"
    assert [v for _, v in hours] == [11, 12]
    assert calls[0].data["type"] == "hourly"


@pytest.mark.asyncio
async def test_a_forecast_is_read_again_only_when_it_is_old(
    hass: HomeAssistant, freezer: Any
):
    hass.states.async_set("weather.home", "sunny", {"temperature_unit": "°F"})
    at = dt_util.utcnow().isoformat()
    calls = _forecast_service(hass, [{"datetime": at, "temperature": 50}])
    cache: dict[str, Any] = {}

    await InProcessClient(hass, cache).forecast("weather.home")
    await InProcessClient(hass, cache).forecast("weather.home")
    assert len(calls) == 1

    freezer.tick(FORECAST_TTL)
    await InProcessClient(hass, cache).forecast("weather.home")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_forecast_that_cannot_be_read_is_reported(hass: HomeAssistant):
    client = InProcessClient(hass)
    with pytest.raises(HomeAssistantError, match="does not exist"):
        await client.forecast("weather.home")

    # No service to answer: the weather integration isn't loaded.
    hass.states.async_set("weather.home", "sunny", {"temperature_unit": "°F"})
    with pytest.raises(HomeAssistantError, match="not read"):
        await client.forecast("weather.home")


def _projecting(
    hass: HomeAssistant, coordinator: MagicMock, **options: Any
) -> Any:
    hass.states.async_set(
        "sensor.garage", "60.0", {"unit_of_measurement": "°F"}
    )
    hass.states.async_set("weather.home", "sunny", {"temperature_unit": "°F"})
    at = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    _forecast_service(
        hass,
        [
            {
                "datetime": (at + timedelta(hours=h)).isoformat(),
                "temperature": 40 + h,
            }
            for h in range(-3, 30)
        ],
    )
    walker = runner(hass, coordinator, weather_entity="weather.home", **options)
    walker.location_air = TRUE
    return walker


@pytest.mark.asyncio
async def test_the_air_is_projected_from_the_forecast(
    hass: HomeAssistant, coordinator
):
    walker = _projecting(hass, coordinator, air_entity="sensor.garage")

    inputs = await walker.async_inputs()

    assert isinstance(inputs, Inputs)
    assert any("projected from the outdoor forecast" in n for n in inputs.notes)
    ambient = inputs.scenario.environment.ambient_f
    assert isinstance(ambient, tuple) and len(ambient) > 2
    # Anchored to the sensor's reading now; warming with the forecast.
    assert ambient[0][1] == pytest.approx(60.0, abs=0.1)
    assert ambient[-1][1] > ambient[0][1]


@pytest.mark.asyncio
async def test_without_an_air_sensor_nothing_is_projected(
    hass: HomeAssistant, coordinator
):
    walker = _projecting(hass, coordinator)

    inputs = await walker.async_inputs()

    assert isinstance(inputs, Inputs)
    assert not any("projected" in n for n in inputs.notes)
