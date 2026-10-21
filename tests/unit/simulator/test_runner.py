"""The runner: from the coordinator's copy of a heater to an answer."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.nwp500.simulator.runner import (
    REASON_RESERVATIONS_UNKNOWN,
    REASON_TOU_UNKNOWN,
    STATE_DISABLED,
    STATE_HEATING,
    STATE_SCHEDULED,
    WAIT_FEATURE,
    WAIT_STANDBY,
    WAIT_STATUS,
    HeatingHistory,
    Inputs,
    SimulatorRunner,
    answer_from,
    heater_reading,
    heater_schedules,
)

from .conftest import MAC, make_feature, make_status

# Device booleans: 2 on, 1 off.
ON, OFF = 2, 1

RESERVATIONS = {
    "reservation_use": 2,
    "reservation": [
        {
            "enable": 2,
            "week": 254,
            "hour": 6,
            "min": 30,
            "mode": 4,
            "param": 124,
        }
    ],
}
TOU = {
    "reservation": [
        {
            "season": 4095,
            "week": 254,
            "start_hour": 16,
            "start_min": 0,
            "end_hour": 20,
            "end_min": 59,
            "price_min": 50000,
            "price_max": 50000,
            "decimal_point": 5,
        }
    ]
}


def runner(
    hass: HomeAssistant, coordinator: MagicMock, **options: Any
) -> SimulatorRunner:
    return SimulatorRunner(
        hass, coordinator, MAC, options, HeatingHistory(), MagicMock()
    )


def test_the_heaters_air_is_used_only_while_the_compressor_runs():
    idle = heater_reading(make_status(compUse=OFF), make_feature())
    assert math.isnan(idle.environment.ambient_f)

    running = heater_reading(make_status(compUse=ON), make_feature())
    # ambientTemperature 220 deci-°C is 22 °C.
    assert running.environment.ambient_f == pytest.approx(71.6)


def test_the_tank_size_comes_from_the_feature():
    reading = heater_reading(make_status(), make_feature())
    assert reading.params is not None


def test_schedules_not_read_yet_are_named():
    schedules, reasons = heater_schedules(make_status(), None, None)
    assert reasons == (REASON_RESERVATIONS_UNKNOWN,)
    assert schedules.reservations == ()

    _, reasons = heater_schedules(
        make_status(touStatus=True), RESERVATIONS, None
    )
    assert reasons == (REASON_TOU_UNKNOWN,)


def test_schedules_are_read_from_the_stored_copies():
    schedules, reasons = heater_schedules(
        make_status(touStatus=True), RESERVATIONS, TOU
    )
    assert reasons == ()
    assert len(schedules.reservations) == 1
    assert schedules.reservations[0].hour == 6
    assert len(schedules.tou_periods) == 1
    assert schedules.tou_enabled is True


def test_history_records_the_compressors_stop_and_the_last_heat():
    history = HeatingHistory()
    t0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)

    assert history.observe(make_status(compUse=ON), t0) is True
    assert history.heated_at == t0
    assert history.compressor_stopped_at is None

    assert history.observe(make_status(compUse=OFF), t1) is True
    assert history.compressor_stopped_at == t1
    assert history.heated_at == t0

    assert history.observe(make_status(compUse=OFF), t1) is False


def test_history_survives_storage():
    t0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
    history = HeatingHistory(compressor_stopped_at=t0, heated_at=t0)

    again = HeatingHistory.from_document(history.as_document())

    assert again.compressor_stopped_at == t0
    assert again.heated_at == t0
    assert (
        HeatingHistory.from_document({"heated_at": "nonsense"}).heated_at
        is None
    )
    assert HeatingHistory.from_document(None).heated_at is None


@pytest.mark.asyncio
async def test_no_scenario_without_a_status(hass: HomeAssistant, coordinator):
    coordinator.data[MAC]["status"] = None
    assert await runner(hass, coordinator).async_inputs() == WAIT_STATUS


@pytest.mark.asyncio
async def test_no_scenario_without_the_feature(
    hass: HomeAssistant, coordinator
):
    coordinator.device_features = {}
    assert await runner(hass, coordinator).async_inputs() == WAIT_FEATURE


@pytest.mark.asyncio
async def test_no_scenario_in_standby(hass: HomeAssistant, coordinator):
    coordinator.data[MAC]["status"] = make_status(dhwOperationSetting=0)
    assert await runner(hass, coordinator).async_inputs() == WAIT_STANDBY


@pytest.mark.asyncio
async def test_the_air_sensor_is_read(hass: HomeAssistant, coordinator):
    hass.states.async_set(
        "sensor.garage", "15.0", {"unit_of_measurement": "°C"}
    )
    inputs = await runner(
        hass, coordinator, air_entity="sensor.garage"
    ).async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.scenario.environment.ambient_f == pytest.approx(59.0)
    assert any("beside the heater" in note for note in inputs.notes)


@pytest.mark.asyncio
async def test_a_stale_air_sensor_is_not_used(hass: HomeAssistant, coordinator):
    hass.states.async_set(
        "sensor.garage", "15.0", {"unit_of_measurement": "°C"}
    )
    later = dt_util.utcnow() + timedelta(hours=2)
    inputs = await runner(
        hass, coordinator, air_entity="sensor.garage"
    ).async_inputs(start=later)

    assert isinstance(inputs, Inputs)
    assert math.isnan(inputs.scenario.environment.ambient_f)


@pytest.mark.asyncio
async def test_a_missing_air_sensor_leaves_the_default(
    hass: HomeAssistant, coordinator
):
    inputs = await runner(
        hass, coordinator, air_entity="sensor.nowhere"
    ).async_inputs()

    assert isinstance(inputs, Inputs)
    assert math.isnan(inputs.scenario.environment.ambient_f)


@pytest.mark.asyncio
async def test_a_run_answers_when_the_tank_next_heats(
    hass: HomeAssistant, coordinator
):
    r = runner(hass, coordinator)
    seen: list[None] = []
    r.async_add_listener(lambda: seen.append(None))

    r.async_start()
    await hass.async_block_till_done()

    answer = r.answer
    assert answer is not None and answer.ok
    # The tank's lower zone is below the heater's turn-on point.
    assert answer.state == STATE_SCHEDULED
    assert answer.start is not None and answer.end is not None
    assert answer.end > answer.start
    assert answer.source == "compressor"
    assert answer.energy_kwh is not None and answer.energy_kwh > 0
    assert REASON_RESERVATIONS_UNKNOWN in answer.reasons
    assert "no_heat_book" in answer.reasons
    assert seen
    assert r.last_duration_s is not None
    r.async_stop()


@pytest.mark.asyncio
async def test_a_report_seen_already_does_not_rerun(
    hass: HomeAssistant, coordinator
):
    r = runner(hass, coordinator)
    r.async_start()
    await hass.async_block_till_done()
    first = r.answer

    for listener in list(coordinator.listeners):
        listener()
    await hass.async_block_till_done()

    assert r.answer is first
    r.async_stop()


@pytest.mark.asyncio
async def test_a_stopped_runner_publishes_nothing(
    hass: HomeAssistant, coordinator
):
    r = runner(hass, coordinator)
    r.async_stop()
    await r._async_run()  # noqa: SLF001
    assert r.answer is None
    assert coordinator.listeners == []


@pytest.mark.asyncio
async def test_answers_say_what_the_heater_is_doing(
    hass: HomeAssistant, coordinator
):
    from nwp500 import sim

    async def answer(**changes: Any) -> Any:
        coordinator.data[MAC]["status"] = make_status(**changes)
        inputs = await runner(hass, coordinator).async_inputs()
        assert isinstance(inputs, Inputs)
        result = sim.simulate(inputs.scenario, inputs.params)
        return answer_from(result, inputs)

    assert (await answer(compUse=ON)).state == STATE_HEATING
    # Vacation: nothing heats.
    assert (await answer(dhwOperationSetting=5)).state == STATE_DISABLED


@pytest.mark.asyncio
async def test_a_failing_walk_is_logged_once_and_recovers(
    hass: HomeAssistant, coordinator, caplog
):
    from unittest.mock import patch

    from nwp500 import sim
    from nwp500.sim.engine import SimulationError

    from custom_components.nwp500.simulator.runner import FAILED

    r = runner(hass, coordinator)
    real = sim.simulate
    with patch.object(sim, "simulate", side_effect=SimulationError("bad")):
        await r._async_run()  # noqa: SLF001
        await r._async_run()  # noqa: SLF001
    assert r.answer is not None and r.answer.error == FAILED
    assert not r.answer.ok
    assert caplog.text.count("failed: bad") == 1

    with patch.object(sim, "simulate", side_effect=real):
        await r._async_run()  # noqa: SLF001
    assert r.answer is not None and r.answer.ok
    assert "runs again" in caplog.text
