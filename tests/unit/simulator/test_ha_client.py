"""The in-process client against the library's REST client.

Both read the same states: the REST client from what Home Assistant's
`/api/states/<entity>` would answer, the in-process one from the state
machine. The environment they give must be the same.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from homeassistant.util.json import json_loads
from nwp500.sim_home_assistant import (
    Entities,
    HomeAssistantClient,
    HomeAssistantEnvironment,
    HomeAssistantError,
)

from custom_components.nwp500.simulator.ha_client import InProcessClient


class _Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self) -> Any:
        return self._body

    async def text(self) -> str:
        return str(self._body)


class _Session:
    """What aiohttp would get from Home Assistant's REST API."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def request(self, method: str, url: str, **kw: Any) -> _Response:
        entity = url.rsplit("/api/states/", 1)[1]
        state = self.hass.states.get(entity)
        if state is None:
            return _Response(404, {"message": "Entity not found."})
        return _Response(200, json_loads(state.as_dict_json))


async def _environments(
    hass: HomeAssistant, entities: Entities, *, at: Any = None
) -> tuple[Any, Any, list[str], list[str]]:
    start = at or dt_util.utcnow().replace(microsecond=0)
    until = start + timedelta(hours=24)
    rest = HomeAssistantEnvironment(
        HomeAssistantClient("http://ha", "token", session=_Session(hass)),
        entities,
        start=start,
        until=until,
        now=start,
    )
    local = HomeAssistantEnvironment(
        InProcessClient(hass),  # type: ignore[arg-type]
        entities,
        start=start,
        until=until,
        now=start,
    )
    return await rest.read(), await local.read(), rest.notes, local.notes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("air", "unit"),
    [("15.5", "°C"), ("60.1", "°F"), ("unavailable", "°C"), ("x", "°F")],
)
async def test_both_clients_read_the_same_air_and_humidity(
    hass: HomeAssistant, air: str, unit: str
):
    hass.states.async_set("sensor.air", air, {"unit_of_measurement": unit})
    hass.states.async_set("sensor.rh", "41", {"unit_of_measurement": "%"})

    rest, local, rest_notes, local_notes = await _environments(
        hass, Entities(air="sensor.air", humidity="sensor.rh")
    )

    assert local == rest
    assert local_notes == rest_notes


@pytest.mark.asyncio
async def test_both_clients_treat_a_missing_entity_alike(hass: HomeAssistant):
    rest, local, rest_notes, local_notes = await _environments(
        hass, Entities(air="sensor.nowhere")
    )

    assert local == rest
    # The notes give each client's own error.
    assert [note.split(" (")[0] for note in local_notes] == [
        note.split(" (")[0] for note in rest_notes
    ]


@pytest.mark.asyncio
async def test_both_clients_treat_a_stale_reading_alike(hass: HomeAssistant):
    hass.states.async_set("sensor.air", "15.5", {"unit_of_measurement": "°C"})
    later = dt_util.utcnow() + timedelta(hours=3)

    rest, local, rest_notes, local_notes = await _environments(
        hass, Entities(air="sensor.air"), at=later
    )

    assert local == rest
    assert local_notes == rest_notes


@pytest.mark.asyncio
async def test_a_sensor_reporting_the_same_value_stays_fresh(
    hass: HomeAssistant, freezer
):
    """Dated by its last report, not its last change (nwp500-python 9.5.1)."""
    hass.states.async_set("sensor.air", "15.5", {"unit_of_measurement": "°C"})
    freezer.tick(timedelta(hours=3))
    hass.states.async_set("sensor.air", "15.5", {"unit_of_measurement": "°C"})
    state = hass.states.get("sensor.air")
    assert state is not None
    assert state.last_reported - state.last_updated >= timedelta(hours=3)

    rest, local, rest_notes, local_notes = await _environments(
        hass,
        Entities(air="sensor.air"),
        at=dt_util.utcnow().replace(microsecond=0),
    )

    assert local == rest
    assert local_notes == rest_notes
    assert not [note for note in local_notes if "stale" in note]


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
async def test_a_forecast_that_cannot_be_read_is_reported(hass: HomeAssistant):
    client = InProcessClient(hass)
    with pytest.raises(HomeAssistantError, match="does not exist"):
        await client.forecast("weather.home")

    # No service to answer: the weather integration isn't loaded.
    hass.states.async_set("weather.home", "sunny", {"temperature_unit": "°F"})
    with pytest.raises(HomeAssistantError, match="not read"):
        await client.forecast("weather.home")
