"""The runner: from the coordinator's copy of a heater to an answer."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from nwp500 import sim
from nwp500.sim_live import DEFAULT_PARAMS_NOTE

from custom_components.nwp500.simulator.runner import (
    REASON_RESERVATIONS_UNKNOWN,
    STATE_DISABLED,
    STATE_HEATING,
    STATE_SCHEDULED,
    WAIT_STANDBY,
    WAIT_STATUS,
    Inputs,
    SimulatorRunner,
    answer_from,
    heater_schedules,
)

from .conftest import MAC, make_status

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
    return SimulatorRunner(hass, coordinator, MAC, options)


def test_schedules_not_read_yet_are_named():
    schedules, reasons = heater_schedules(make_status(), None, None)
    assert reasons == (REASON_RESERVATIONS_UNKNOWN,)
    assert schedules.reservations == ()

    # TOU on with no periods read: the walk goes on without them.
    schedules, reasons = heater_schedules(
        make_status(touStatus=True), RESERVATIONS, None
    )
    assert reasons == ()
    assert schedules.tou_enabled is True
    assert schedules.tou_periods == ()


def test_schedules_are_read_from_the_stored_copies():
    schedules, reasons = heater_schedules(
        make_status(touStatus=True), RESERVATIONS, TOU
    )
    assert reasons == ()
    assert len(schedules.reservations) == 1
    assert schedules.reservations[0].hour == 6
    assert len(schedules.tou_periods) == 1
    assert schedules.tou_enabled is True


@pytest.mark.asyncio
async def test_no_scenario_without_a_status(hass: HomeAssistant, coordinator):
    coordinator.data[MAC]["status"] = None
    assert await runner(hass, coordinator).async_inputs() == WAIT_STATUS


@pytest.mark.asyncio
async def test_without_the_feature_the_librarys_defaults_are_walked(
    hass: HomeAssistant, coordinator
):
    coordinator.device_features = {}

    inputs = await runner(hass, coordinator).async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.params == sim.default_params()
    assert DEFAULT_PARAMS_NOTE in inputs.notes
    # Not the heater's part: a document's params would replace them.
    assert DEFAULT_PARAMS_NOTE not in inputs.live_notes


@pytest.mark.asyncio
async def test_tou_periods_not_read_do_not_hold_the_walk_up(
    hass: HomeAssistant, coordinator
):
    coordinator.data[MAC]["status"] = make_status(touStatus=True)

    inputs = await runner(hass, coordinator).async_inputs()

    assert isinstance(inputs, Inputs)
    result = sim.simulate(inputs.scenario, inputs.params)
    assert "tou_schedule_unknown" in result.reasons


@pytest.mark.asyncio
async def test_the_scenario_is_kept_composed_for_a_document(
    hass: HomeAssistant, coordinator
):
    inputs = await runner(hass, coordinator).async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.live is not None
    assert inputs.live.scenario == inputs.scenario
    assert inputs.live.params == inputs.params


@pytest.mark.asyncio
async def test_no_scenario_in_standby(hass: HomeAssistant, coordinator):
    coordinator.data[MAC]["status"] = make_status(dhwOperationSetting=0)
    assert await runner(hass, coordinator).async_inputs() == WAIT_STANDBY


@pytest.mark.asyncio
async def test_the_scenario_leaves_the_history_to_the_library(
    hass: HomeAssistant, coordinator
):
    from nwp500.sim.inputs import History

    inputs = await runner(hass, coordinator).async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.scenario.history == History()


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
    # The heater's own reading, as `compose` takes it without another.
    assert inputs.scenario.environment.ambient_f == pytest.approx(71.6)


@pytest.mark.asyncio
async def test_a_missing_air_sensor_leaves_the_heaters_reading(
    hass: HomeAssistant, coordinator
):
    inputs = await runner(
        hass, coordinator, air_entity="sensor.nowhere"
    ).async_inputs()

    assert isinstance(inputs, Inputs)
    # The heater's own reading, as `compose` takes it without another.
    assert inputs.scenario.environment.ambient_f == pytest.approx(71.6)


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


@pytest.mark.asyncio
async def test_the_ready_time_is_the_end_of_the_first_heating(
    hass: HomeAssistant, coordinator
):
    from nwp500 import sim

    inputs = await runner(hass, coordinator).async_inputs()
    assert isinstance(inputs, Inputs)
    result = sim.simulate(inputs.scenario, inputs.params)
    first = result.heating[0]

    answer = answer_from(result, inputs)
    assert answer.end == first.end
    assert answer.stop == first.stop


@pytest.mark.asyncio
async def test_standby_has_no_answer(hass: HomeAssistant, coordinator):
    # The library does not model Standby.
    coordinator.data[MAC]["status"] = make_status(dhwOperationSetting=0)
    r = runner(hass, coordinator)

    await r._async_run()  # noqa: SLF001

    assert r.answer is not None and not r.answer.ok
    assert r.answer.waiting == WAIT_STANDBY


@pytest.mark.asyncio
async def test_a_scenario_the_library_refuses_is_a_failed_answer(
    hass: HomeAssistant, coordinator, caplog
):
    from custom_components.nwp500.simulator.runner import FAILED

    bad = dict(RESERVATIONS)
    bad["reservation"] = [{**RESERVATIONS["reservation"][0], "hour": 25}]
    coordinator.reservation_schedules = {MAC: bad}
    r = runner(hass, coordinator)

    await r._async_run()  # noqa: SLF001
    await r._async_run()  # noqa: SLF001

    assert r.answer is not None and r.answer.error == FAILED
    assert not r.answer.ok
    assert caplog.text.count("The hot water simulation for") == 1


@pytest.mark.asyncio
async def test_any_failure_of_the_walk_replaces_the_last_answer(
    hass: HomeAssistant, coordinator
):
    from unittest.mock import patch

    from nwp500 import sim

    from custom_components.nwp500.simulator.runner import FAILED

    r = runner(hass, coordinator)
    await r._async_run()  # noqa: SLF001
    assert r.answer is not None and r.answer.ok

    with patch.object(sim, "simulate", side_effect=ZeroDivisionError):
        await r._async_run()  # noqa: SLF001

    assert r.answer is not None and r.answer.error == FAILED
    assert not r.answer.ok
