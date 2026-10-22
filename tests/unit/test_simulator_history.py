"""The in-process client's history against Home Assistant's REST API.

Kept apart from `tests/unit/simulator`, whose fixtures set Home Assistant
up before the recorder can be.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

# The simulator's packages; without them (tox -e py314-nosim), skipped.
pytest.importorskip("pandas")

from nwp500 import sim  # noqa: E402
from nwp500.sim_calibrate import report  # noqa: E402
from nwp500.sim_home_assistant import (  # noqa: E402
    Entities,
    HeaterEntities,
    HomeAssistantClient,
    HomeAssistantError,
    read_heater_history,
    record_inputs,
)

from custom_components.nwp500.simulator.calibration import (  # noqa: E402
    first_change,
    run_change_calibration,
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
    """What the calibration reads, through the library's own reader."""
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
    """The heater's entities, as the entity-history calibration reads them."""
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

    # Where the recorder's tank history begins: its first row, or the
    # start of a span it began before.
    first = await first_change(
        hass, "sensor.upper", start - timedelta(hours=1), end
    )
    assert first is not None
    assert abs((first - start).total_seconds()) < 1
    later = start + timedelta(minutes=7)
    assert await first_change(hass, "sensor.upper", later, end) == later
    assert await first_change(hass, "sensor.none", start, end) is None

    # And through the library's fit, which a quarter hour cannot inform.
    done = run_change_calibration(local, sim.default_params(), None)
    assert done.n_statuses > 0
    assert not any(term.fitted for term in report(done))
    # Refining a fit, every group is judged, and kept.
    refined = run_change_calibration(local, sim.default_params(), None, True)
    assert refined.judged
    assert all(j.kept == "base" for j in refined.judged)


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


@pytest.mark.asyncio
async def test_the_airs_model_is_fitted_from_the_recorders_history(
    recorder_mock: Any, hass: HomeAssistant, freezer: Any
):
    """The air follows the weather entity's outdoor air, two hours late."""
    import math

    from nwp500.sim.location_air import LocationAirModel
    from nwp500.sim_home_assistant import Entities

    from custom_components.nwp500.simulator.location import (
        KEPT_REFIT,
        async_fit_location,
    )

    true = LocationAirModel(0.5, timedelta(hours=2), 30.0)
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    outdoor = [
        (
            start + timedelta(minutes=30 * i),
            55.0 + 15.0 * math.sin(2 * math.pi * i / 48),
        )
        for i in range(4 * 48 + 1)
    ]
    for (at, value), air in zip(
        outdoor, true.at(outdoor, [at for at, _ in outdoor]), strict=True
    ):
        freezer.move_to(at)
        hass.states.async_set(
            "weather.home",
            "sunny",
            # In °C, as many weather entities report.
            {
                "temperature": round((value - 32) / 1.8, 2),
                "temperature_unit": "°C",
            },
        )
        hass.states.async_set(
            "sensor.garage",
            f"{air:.2f}",
            {"unit_of_measurement": "°F"},
        )
    await async_wait_recording_done(hass)
    runner = MagicMock()
    runner.entities = Entities(air="sensor.garage", weather="weather.home")
    runner.location_air = None

    done = await async_fit_location(hass, runner, dt_util.utcnow())

    assert done is not None and done.kept == KEPT_REFIT, done
    assert done.model is not None
    assert done.model.damping == pytest.approx(0.5, abs=0.05)
    assert done.model.lag == timedelta(hours=2)


async def _metered(
    hass: HomeAssistant, freezer: Any, spans: list[tuple[int, int]]
) -> Any:
    """A meter reporting every 30 s over each span, from 19 h ago; now.

    19 hours: the book's first window, 18, is read in full.
    """
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    start = now - timedelta(hours=19)
    for first, last in spans:
        freezer.move_to(start + timedelta(minutes=first))
        for i in range((last - first) * 2):
            freezer.move_to(start + timedelta(minutes=first, seconds=30 * i))
            # Alternating, so each report writes a row.
            hass.states.async_set(
                "sensor.meter",
                "0.0" if i % 2 else "0.001",
                {"unit_of_measurement": "gal"},
            )
        freezer.move_to(start + timedelta(minutes=last))
        hass.states.async_set(
            "sensor.meter", "unavailable", {"unit_of_measurement": "gal"}
        )
    freezer.move_to(now)
    await async_wait_recording_done(hass)
    return now


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spans",
    [[(0, 600), (720, 1140)], [(0, 1020)]],
    ids=["silent-inside", "stopped-before"],
)
async def test_a_meter_that_went_silent_books_nothing(
    recorder_mock: Any, hass: HomeAssistant, freezer: Any, spans: Any
):
    """PR #239 review: a meter silent over an hour books nothing."""
    from nwp500 import sim
    from nwp500.sim_home_assistant import Entities, HeaterHistory

    from custom_components.nwp500.simulator import water

    now = await _metered(hass, freezer, spans)
    runner = MagicMock()
    runner.mac_address = "AA"
    runner.coordinator.data = {"AA": {}}
    runner.walk_params.return_value = sim.default_params()
    runner.entities = Entities()
    runner.water_entities = Entities(meter_volume="sensor.meter")
    # The heater heard throughout: the meter is what's missing.
    heard = [now - timedelta(minutes=m) for m in range(73 * 60, -1, -1)]
    history = HeaterHistory(rows=MagicMock(), observed=heard)
    with (
        patch.object(water, "heater_entities", return_value=(MagicMock(), [])),
        patch.object(
            water, "read_heater_history", AsyncMock(return_value=history)
        ),
    ):
        got = await water.async_read_book(hass, runner, now)

    assert got.book is None
    # The two hours it was silent, not a history that starts late.
    assert any("silent for 2:00:" in note for note in got.notes), got.notes
