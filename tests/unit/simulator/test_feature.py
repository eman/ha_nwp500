"""The simulator feature: set-up, storage, removal, its sensors and service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500.const import (
    CONF_SIMULATOR,
    CONF_SIMULATOR_ENABLED,
    DATA_SIMULATOR,
    DOMAIN,
    simulator_storage_key,
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
    SimulatorHotWaterReadySensor,
)
from custom_components.nwp500.simulator.service import UNITS, async_simulate

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
async def test_the_heating_history_is_kept_across_restarts(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    coordinator.data[MAC]["status"] = make_status(compUse=ON)
    feature = await started(hass, entry, coordinator)
    coordinator.data[MAC]["status"] = make_status(compUse=OFF)
    coordinator.data[MAC]["last_update"] = 2.0
    for listener in list(coordinator.listeners):
        listener()
    stopped = feature.runners[MAC].history.compressor_stopped_at
    assert stopped is not None

    await async_unload_simulator(hass, entry)
    assert simulator_storage_key(entry.entry_id) in hass_storage

    feature = await started(hass, entry, coordinator)
    assert feature.runners[MAC].history.compressor_stopped_at == stopped
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_removal_deletes_the_storage_and_nothing_saves_it_again(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    await async_unload_simulator(hass, entry)
    assert simulator_storage_key(entry.entry_id) in hass_storage

    feature = await started(hass, entry, coordinator)
    await feature.async_remove()
    # The entry's reload then unloads it, which must not save it back.
    await async_unload_simulator(hass, entry)
    await hass.async_block_till_done()

    assert simulator_storage_key(entry.entry_id) not in hass_storage


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
    assert attributes["model"] == "physics"
    assert attributes["source"] == "compressor"
    assert "no_heat_book" in attributes["reasons"]
    assert {"reasons", "defaults_used", "notes"} <= set(
        heating._unrecorded_attributes  # noqa: SLF001
    )

    ready = by_type[SimulatorHotWaterReadySensor]
    assert ready.device_class is SensorDeviceClass.TIMESTAMP
    assert answer.end is not None
    assert ready.native_value == answer.end.replace(second=0, microsecond=0)

    energy = by_type[SimulatorEnergySensor]
    assert energy.native_value == answer.energy_total_kwh
    assert energy.state_class is None

    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_sensors_are_unavailable_without_an_answer(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    sensors = create_simulator_sensors(feature)

    runner.answer = Answer(computed_at=dt_util.utcnow(), waiting="standby")

    assert not any(sensor.available for sensor in sensors)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_walks_the_scenario(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]

    document = await async_simulate(
        hass,
        runner,
        hours=6,
        writes=[],
        include_points=False,
        resolution_minutes=None,
    )

    assert "points" not in document
    assert document["units"] == UNITS
    assert document["model"] == "physics"
    assert document["summary"]["state"] == "scheduled"
    assert document["heating"]
    start = datetime.fromisoformat(document["start"])
    until = datetime.fromisoformat(document["until"])
    assert until - start == timedelta(hours=6)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_thins_the_path(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]

    full = await async_simulate(
        hass,
        runner,
        hours=2,
        writes=[],
        include_points=True,
        resolution_minutes=None,
    )
    thin = await async_simulate(
        hass,
        runner,
        hours=2,
        writes=[],
        include_points=True,
        resolution_minutes=15,
    )

    assert len(full["points"]) > len(thin["points"])
    assert thin["points"][-1]["at"] == full["points"][-1]["at"]
    instants = [datetime.fromisoformat(p["at"]) for p in thin["points"]]
    gaps = [b - a for a, b in zip(instants[:-2], instants[1:-1], strict=True)]
    assert gaps and min(gaps) >= timedelta(minutes=15)
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
        resolution_minutes=None,
    )

    landed = document["writes"] + document["mode_writes"]
    assert landed
    setpoints = [w["setpoint_f"] for w in document["writes"]]
    assert any(abs(value - 122.0) < 0.01 for value in setpoints)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_service_says_when_there_is_no_scenario(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    feature = await started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    coordinator.device_features = {}

    with pytest.raises(ServiceValidationError) as err:
        await async_simulate(
            hass,
            runner,
            hours=4,
            writes=[],
            include_points=False,
            resolution_minutes=None,
        )
    assert err.value.translation_key == "simulator_no_answer"
    await async_unload_simulator(hass, entry)


def test_timestamps_are_utc():
    assert datetime.fromisoformat("2026-10-20T00:00:00+00:00").tzinfo == UTC


@pytest.mark.asyncio
async def test_diagnostics_show_the_simulator_without_macs(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    from custom_components.nwp500.diagnostics import _simulator

    feature = await started(hass, entry, coordinator)

    section = _simulator(feature)

    (device,) = section["devices"]
    assert device["options"] == {"air_entity": "sensor.garage"}
    assert device["params"] == "default"
    assert device["answer"]["state"] == "scheduled"
    assert isinstance(device["answer"]["end"], str)
    assert MAC not in str(section)
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_a_heating_the_walk_cuts_off_has_no_ready_time(
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
        resolution_minutes=None,
    )

    (heating,) = document["heating"]
    assert heating["stop"] == "horizon"
    assert document["summary"]["state"] == "scheduled"
    assert document["summary"]["ready_at"] is None
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
            resolution_minutes=None,
        )
    assert err.value.translation_key == "simulator_write_outside"
    await async_unload_simulator(hass, entry)
