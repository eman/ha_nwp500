"""The in-process client's history against Home Assistant's REST API.

Kept apart from `tests/unit/simulator`, whose fixtures set Home Assistant
up before the recorder can be.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

# The simulator's packages; without them (tox -e py314-nosim), skipped.
pytest.importorskip("pandas")

from nwp500.sim_home_assistant import (  # noqa: E402
    Entities,
    HeaterEntities,
    HomeAssistantClient,
    HomeAssistantError,
    read_heater_history,
    record_inputs,
)

from custom_components.nwp500.simulator.ha_client import (  # noqa: E402
    InProcessClient,
)


class _Session:
    """aiohttp's session, as the library's client uses it.

    Onto the test client of Home Assistant's own HTTP API.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def request(self, method: str, url: str, **kw: Any) -> Any:
        kw.pop("timeout", None)
        kw.pop("allow_redirects", None)
        # The test client carries a real token; the library's is a stand-in.
        kw.pop("headers", None)
        return self._client.request(method, url.split("://ha", 1)[1], **kw)


async def _rest(hass: HomeAssistant, hass_client: Any) -> HomeAssistantClient:
    assert await async_setup_component(hass, "api", {})
    assert await async_setup_component(hass, "history", {})
    return HomeAssistantClient(
        "http://ha", "token", session=_Session(await hass_client())
    )


async def _record(hass: HomeAssistant, freezer: Any) -> tuple[Any, Any]:
    """An air sensor's changes, an outage and a unit change; and their span.

    Under half an hour, as the test client's access token lasts.
    """
    start = dt_util.utcnow().replace(microsecond=0)
    freezer.move_to(start - timedelta(minutes=5))
    hass.states.async_set("sensor.air", "60.5", {"unit_of_measurement": "°F"})
    hass.states.async_set("sensor.rh", "41", {"unit_of_measurement": "%"})
    for minutes, air, unit in (
        (4, "61.0", "°F"),
        (8, "unavailable", "°F"),
        (12, "16.5", "°C"),
        (16, "62.5", "°F"),
        (20, "62.5", "°F"),
    ):
        freezer.move_to(start + timedelta(minutes=minutes))
        hass.states.async_set("sensor.air", air, {"unit_of_measurement": unit})
    freezer.move_to(start + timedelta(minutes=22))
    hass.states.async_set("sensor.rh", "44", {"unit_of_measurement": "%"})
    await async_wait_recording_done(hass)
    return start, start + timedelta(minutes=25)


@pytest.mark.asyncio
async def test_both_clients_read_the_same_history(
    recorder_mock: Any, hass: HomeAssistant, hass_client: Any, freezer: Any
):
    rest = await _rest(hass, hass_client)
    start, end = await _record(hass, freezer)
    names = ["sensor.air", "sensor.rh", "sensor.nowhere"]

    local = await InProcessClient(hass).read_history(names, start, end)
    remote = await rest.read_history(names, start, end)

    assert local == remote
    # The value in force at the start, the outage, and each unit.
    assert local.readings["sensor.air"][0][1] == 60.5
    assert local.outages["sensor.air"]
    assert "°C" in (local.units or {})["sensor.air"]
    assert local.readings["sensor.nowhere"] == []
    # Every row as written, the outage's too.
    air_states = [state for _, state in (local.states or {})["sensor.air"]]
    assert air_states[0] == "60.5"
    assert "unavailable" in air_states
    assert (local.states or {})["sensor.nowhere"] == []


@pytest.mark.asyncio
async def test_both_clients_record_the_same_inputs(
    recorder_mock: Any, hass: HomeAssistant, hass_client: Any, freezer: Any
):
    """The environment's history, through the library's own reader."""
    rest = await _rest(hass, hass_client)
    start, end = await _record(hass, freezer)
    entities = Entities(air="sensor.air", humidity="sensor.rh")

    local = await record_inputs(InProcessClient(hass), entities, start, end)  # type: ignore[arg-type]
    remote = await record_inputs(rest, entities, start, end)

    assert local == remote
    assert local.air


@pytest.mark.asyncio
async def test_both_clients_read_the_same_heater_history(
    recorder_mock: Any, hass: HomeAssistant, hass_client: Any, freezer: Any
):
    """The heater's entities, through the library's own reader."""
    rest = await _rest(hass, hass_client)
    start = dt_util.utcnow().replace(microsecond=0)
    for minutes, upper, compressor in (
        (0, "131.0", "off"),
        (3, "130.8", "off"),
        (6, "unavailable", "unavailable"),
        (9, "130.4", "on"),
        (12, "131.2", "on"),
    ):
        freezer.move_to(start + timedelta(minutes=minutes))
        stamp = (start + timedelta(minutes=minutes)).isoformat()
        hass.states.async_set(
            "sensor.heard", stamp, {"device_class": "timestamp"}
        )
        hass.states.async_set(
            "sensor.upper", upper, {"unit_of_measurement": "°F"}
        )
        hass.states.async_set(
            "sensor.lower", "120.0", {"unit_of_measurement": "°F"}
        )
        hass.states.async_set("binary_sensor.compressor", compressor)
    await async_wait_recording_done(hass)
    end = start + timedelta(minutes=15)
    entities = HeaterEntities(
        heard="sensor.heard",
        upper="sensor.upper",
        lower="sensor.lower",
        compressor="binary_sensor.compressor",
    )

    local = await read_heater_history(
        InProcessClient(hass),  # type: ignore[arg-type]
        entities,
        start,
        end,
        window=timedelta(minutes=5),
    )
    remote = await read_heater_history(
        rest, entities, start, end, window=timedelta(minutes=5)
    )

    assert local.observed == remote.observed
    assert local.notes == remote.notes
    assert local.rows == remote.rows
    assert local.observed


@pytest.mark.asyncio
async def test_a_recorder_query_that_fails_is_a_history_not_read(
    recorder_mock: Any, hass: HomeAssistant
):
    """A failed query is a history not read, which the caller can skip."""
    now = dt_util.utcnow()
    with (
        patch(
            "custom_components.nwp500.simulator.ha_client._history",
            side_effect=RuntimeError("database is locked"),
        ),
        pytest.raises(HomeAssistantError, match="database is locked"),
    ):
        await InProcessClient(hass).read_history(
            ["sensor.air"], now - timedelta(hours=1), now
        )


@pytest.mark.asyncio
async def test_history_needs_the_recorder(hass: HomeAssistant):
    now = dt_util.utcnow()
    client = InProcessClient(hass)

    assert (await client.read_history([], now, now)).readings == {}
    with pytest.raises(HomeAssistantError, match="recorder"):
        await client.read_history(["sensor.air"], now, now)
