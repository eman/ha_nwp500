"""The simulator feature: set-up, removal, its sensors and service."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500.const import (
    CONF_SIMULATOR,
    CONF_SIMULATOR_ENABLED,
    DATA_SIMULATOR,
    DOMAIN,
)
from custom_components.nwp500.simulator import (
    SimulatorFeature,
    async_setup_simulator,
    async_unload_simulator,
    create_simulator_sensors,
)
from custom_components.nwp500.simulator.runner import Answer
from custom_components.nwp500.simulator.sensor import (
    SimulatorEnergySensor,
    SimulatorHeatingSensor,
    SimulatorHotWaterAvailableSensor,
    SimulatorHotWaterReadySensor,
    SimulatorShowersReadySensor,
)
from custom_components.nwp500.simulator.service import async_simulate

from .conftest import MAC, make_status

ON, OFF = 2, 1


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={
            CONF_SIMULATOR_ENABLED: True,
            CONF_SIMULATOR: {MAC: {"air_entity": "sensor.garage"}},
        },
    )
    entry.add_to_hass(hass)
    return entry


async def started(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: MagicMock
) -> SimulatorFeature:
    await async_setup_simulator(hass, entry, coordinator)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id][DATA_SIMULATOR]


@pytest.mark.asyncio
async def test_setup_starts_a_runner_per_heater(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)

    assert list(feature.runners) == [MAC]
    runner = feature.runners[MAC]
    assert runner.options == {"air_entity": "sensor.garage"}
    assert runner.answer is not None and runner.answer.ok

    await async_unload_simulator(hass, entry)
    assert DATA_SIMULATOR not in hass.data[DOMAIN][entry.entry_id]
    assert coordinator.listeners == []


@pytest.mark.asyncio
async def test_unload_without_a_feature_is_a_no_op(hass: HomeAssistant, entry):
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_simulator_stores_nothing(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    await started(hass, entry, coordinator)
    await async_unload_simulator(hass, entry)
    await hass.async_block_till_done()

    assert not [key for key in hass_storage if "simulator" in key]


@pytest.mark.asyncio
async def test_sensors_report_the_answer(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    sensors = create_simulator_sensors(feature)
    by_type = {type(sensor): sensor for sensor in sensors}
    answer = feature.runners[MAC].answer
    assert answer is not None

    heating = by_type[SimulatorHeatingSensor]
    assert heating.unique_id == f"{MAC}_simulator_heating"
    assert heating.translation_key == "simulator_heating"
    assert heating.available
    assert heating.native_value == "scheduled"
    attributes = heating.extra_state_attributes
    assert "model" not in attributes
    assert attributes["source"] == "compressor"
    assert "no_heat_book" in attributes["reasons"]
    assert {"reasons", "defaults_used", "notes"} <= set(
        heating._unrecorded_attributes  # noqa: SLF001
    )

    ready = by_type[SimulatorHotWaterReadySensor]
    assert ready.device_class is SensorDeviceClass.TIMESTAMP
    assert answer.end is not None
    assert ready.native_value == answer.end.replace(second=0, microsecond=0)
    assert ready.extra_state_attributes["stop"] == answer.stop

    energy = by_type[SimulatorEnergySensor]
    assert answer.energy_total_kwh is not None
    assert energy.native_value == round(answer.energy_total_kwh, 1)
    assert energy.state_class is None

    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_sensors_are_unavailable_without_an_answer(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    sensors = create_simulator_sensors(feature)

    runner.answer = Answer(computed_at=dt_util.utcnow(), waiting="no_status")

    assert not any(sensor.available for sensor in sensors)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_walks_the_scenario(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    from nwp500 import sim
    from nwp500.sim import codec

    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    walked = []
    real = sim.simulate

    def spy(*args, **kwargs):
        walked.append(real(*args, **kwargs))
        return walked[-1]

    with patch.object(sim, "simulate", side_effect=spy):
        response = await async_simulate(
            hass,
            runner,
            hours=6,
            writes=[],
            include_points=False,
        )

    # The library's document as it gives it, less the path; the
    # integration's notes on its inputs beside it.
    expected = codec.result_to_document(walked[-1])
    del expected["points"], expected["delivery"]
    assert response["result"] == expected
    assert set(response) == {"result", "inputs"}
    assert response["inputs"]["reasons"] == ["reservations_unknown"]
    assert isinstance(response["inputs"]["notes"], list)
    start = datetime.fromisoformat(expected["start"])
    until = datetime.fromisoformat(expected["until"])
    assert until - start == timedelta(hours=6)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_returns_the_path_when_asked(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)

    document = await async_simulate(
        hass,
        feature.runners[MAC],
        hours=2,
        writes=[],
        include_points=True,
    )

    assert document["result"]["points"]
    assert "delivery" in document["result"]
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_tries_a_write(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    at = dt_util.utcnow().replace(microsecond=0) + timedelta(minutes=30)

    document = await async_simulate(
        hass,
        runner,
        hours=4,
        # In Home Assistant's unit, Celsius here: 122 °F.
        writes=[{"at": at, "temperature": 50.0, "mode": "heat_pump"}],
        include_points=False,
    )

    landed = document["result"]["writes"] + document["result"]["mode_writes"]
    assert landed
    setpoints = [w["setpoint_f"] for w in document["result"]["writes"]]
    assert any(abs(value - 122.0) < 0.01 for value in setpoints)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_says_when_there_is_no_scenario(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    coordinator.data[MAC]["status"] = None

    with pytest.raises(ServiceValidationError) as err:
        await async_simulate(
            hass,
            runner,
            hours=4,
            writes=[],
            include_points=False,
        )
    assert err.value.translation_key == "simulator_no_answer"
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_diagnostics_show_the_simulator_without_macs(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    from custom_components.nwp500.diagnostics import _simulator

    feature = await started(hass, entry, coordinator)

    section = _simulator(feature)

    (device,) = section["devices"]
    assert device["options"] == {"air_entity": "sensor.garage"}
    assert device["answer"]["state"] == "scheduled"
    assert isinstance(device["answer"]["end"], str)
    assert MAC not in str(section)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_a_heating_the_walk_cuts_off_stops_at_the_horizon(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    """It stops at the walk's end, not at its target."""
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]

    document = await async_simulate(
        hass,
        runner,
        hours=1,
        writes=[],
        include_points=False,
    )

    (heating,) = document["result"]["heating"]
    assert heating["stop"] == "horizon"
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [timedelta(hours=-1), timedelta(hours=5)])
async def test_a_write_outside_the_walk_is_refused(
    hass: HomeAssistant, hass_storage, entry, coordinator, offset
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]

    with pytest.raises(ServiceValidationError) as err:
        await async_simulate(
            hass,
            runner,
            hours=1,
            writes=[{"at": dt_util.utcnow() + offset, "temperature": 50.0}],
            include_points=False,
        )
    # The library's refusal, with its message: the caller's mistake.
    assert err.value.translation_key == "simulator_invalid_scenario"
    assert "outside" in err.value.translation_placeholders["error"]
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_an_unchanged_answer_leaves_the_attributes_alone(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    """A walk a minute must not write a state a minute."""
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    sensors = create_simulator_sensors(feature)
    before = [(s.native_value, s.extra_state_attributes) for s in sensors]

    await runner._async_run()  # noqa: SLF001

    after = [(s.native_value, s.extra_state_attributes) for s in sensors]
    assert after == before
    assert all("computed_at" not in a for _, a in after)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_heating_now_has_no_start(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    coordinator.data[MAC]["status"] = make_status(compUse=ON)
    feature = await started(hass, entry, coordinator)
    heating = next(
        s
        for s in create_simulator_sensors(feature)
        if isinstance(s, SimulatorHeatingSensor)
    )

    assert heating.native_value == "heating"
    assert heating.extra_state_attributes["starts_at"] is None
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_refuses_a_setpoint_it_cannot_run(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    at = dt_util.utcnow() + timedelta(minutes=10)

    # The library's refusal, with its message.
    with pytest.raises(HomeAssistantError, match="°F") as err:
        await async_simulate(
            hass,
            feature.runners[MAC],
            hours=1,
            writes=[{"at": at, "temperature": 200.0}],
            include_points=False,
        )
    assert not isinstance(err.value, ServiceValidationError)

    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_walks_standby_as_power_off(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    coordinator.data[MAC]["status"] = make_status(dhwOperationSetting=0)

    response = await async_simulate(
        hass,
        feature.runners[MAC],
        hours=1,
        writes=[],
        include_points=False,
    )

    assert response["result"]["heating"] == []
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_sensor_platform_adds_the_sensors_once_it_starts(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.nwp500 import sensor as sensor_platform
    from custom_components.nwp500.const import simulator_started_signal

    entry.runtime_data = coordinator
    coordinator.data[MAC]["device"].device_info.mac_address = MAC
    added: list[list] = []
    with (
        patch.object(sensor_platform, "SENSOR_CONFIGS", {}),
        patch.object(sensor_platform, "INSTALLER_DIAGNOSTICS_SENSORS", {}),
    ):
        coordinator.supports_recirculation_schedule = MagicMock(
            return_value=False
        )
        await sensor_platform.async_setup_entry(
            hass, entry, lambda entities, *a: added.append(list(entities))
        )
    before = len(added)

    await started(hass, entry, coordinator)
    async_dispatcher_send(hass, simulator_started_signal(entry.entry_id))

    assert len(added) == before + 1
    assert {type(s) for s in added[-1]} == {
        SimulatorHeatingSensor,
        SimulatorHotWaterReadySensor,
        SimulatorEnergySensor,
        SimulatorHotWaterAvailableSensor,
        SimulatorShowersReadySensor,
    }
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_heater_data_the_library_refuses_is_not_the_callers_error(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    bad = {
        "reservation_use": 2,
        "reservation": [
            {
                "enable": 2,
                "week": 254,
                "hour": 25,
                "min": 0,
                "mode": 4,
                "param": 124,
            }
        ],
    }
    coordinator.reservation_schedules = {MAC: bad}

    with pytest.raises(HomeAssistantError, match="heater's data") as err:
        await async_simulate(
            hass,
            feature.runners[MAC],
            hours=1,
            writes=[],
            include_points=False,
        )
    assert not isinstance(err.value, ServiceValidationError)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_a_write_into_vacation_is_left_to_the_library(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    at = dt_util.utcnow().replace(microsecond=0) + timedelta(minutes=10)

    document = await async_simulate(
        hass,
        feature.runners[MAC],
        hours=1,
        writes=[{"at": at, "mode": "vacation"}],
        include_points=False,
    )

    assert "write_mode_not_modelled" in document["result"]["reasons"]
    await async_unload_simulator(hass, entry)
