"""The hot water the simulator reports: from the heater alone, or more.

Each optional input is passed to the library when it is configured and
left out when it isn't; the answer is `nwp500.sim.time_to_ready`'s either
way.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from nwp500 import sim
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500.const import (
    CONF_SIMULATOR,
    CONF_SIMULATOR_ENABLED,
    DATA_SIMULATOR,
    DOMAIN,
)
from custom_components.nwp500.simulator import (
    async_setup_simulator,
    async_unload_simulator,
)
from custom_components.nwp500.simulator.binary_sensor import (
    SimulatorBathReadyBinarySensor,
    create_simulator_binary_sensors,
)
from custom_components.nwp500.simulator.runner import (
    Inputs,
    SimulatorRunner,
)
from custom_components.nwp500.simulator.sensor import (
    SimulatorHotWaterAvailableSensor,
    SimulatorShowersReadySensor,
    create_simulator_sensors,
)

from .conftest import MAC

ALL_INPUTS = {
    "air_entity": "sensor.air",
    "humidity_entity": "sensor.rh",
    "inlet_entity": "sensor.supply",
    "demand_entity": "binary_sensor.demand",
    "circulation_speed_entity": "sensor.pump",
    "bath_gal": 30.0,
    "shower_gal": 12.0,
}


async def started(
    hass: HomeAssistant, coordinator: MagicMock, options: dict[str, Any]
) -> SimulatorRunner:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: {MAC: options}},
    )
    entry.add_to_hass(hass)
    await async_setup_simulator(hass, entry, coordinator)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id][DATA_SIMULATOR].runners[MAC]


async def stop(hass: HomeAssistant) -> None:
    entry = hass.config_entries.async_get_entry("entry1")
    assert entry is not None
    await async_unload_simulator(hass, entry)


async def test_the_heater_alone_answers_for_its_hot_water(
    hass: HomeAssistant, hass_storage, coordinator
):
    runner = await started(hass, coordinator, {})

    inputs = await runner.async_inputs()
    assert isinstance(inputs, Inputs)
    entities = runner.entities
    assert entities.inlet is None
    assert entities.demand is None
    assert entities.circulation_speed is None
    assert inputs.drawing is False
    answer = runner.answer
    assert answer is not None
    assert answer.readiness is not None
    # The library's own answer from the heater's state.
    expected = sim.time_to_ready(inputs.scenario, inputs.params)
    assert answer.readiness.deliverable_gal == expected.deliverable_gal
    assert answer.readiness.bath == expected.bath
    assert answer.readiness.showers == expected.showers
    await stop(hass)


async def test_every_input_configured_is_passed_through(
    hass: HomeAssistant, hass_storage, coordinator
):
    hass.states.async_set("sensor.air", "60.0", {"unit_of_measurement": "°F"})
    hass.states.async_set("sensor.rh", "45", {"unit_of_measurement": "%"})
    hass.states.async_set("binary_sensor.demand", "on")
    runner = await started(hass, coordinator, ALL_INPUTS)

    entities = runner.entities
    assert entities.air == "sensor.air"
    assert entities.humidity == "sensor.rh"
    assert entities.inlet == "sensor.supply"
    assert entities.demand == "binary_sensor.demand"
    assert entities.circulation_speed == "sensor.pump"
    inputs = await runner.async_inputs()
    assert isinstance(inputs, Inputs)
    assert inputs.drawing is True
    assert inputs.scenario.environment.ambient_f == pytest.approx(60.0)
    # Without the recorder the inlet's history is not read: the library
    # says so, and answers without it.
    assert any(note.startswith("inlet:") for note in inputs.notes)
    answer = runner.answer
    assert answer is not None
    assert answer.readiness is not None
    expected = sim.time_to_ready(
        inputs.scenario,
        inputs.params,
        bath_gal=30.0,
        shower_gal=12.0,
        drawing=True,
    )
    assert answer.readiness.bath == expected.bath
    assert answer.readiness.showers == expected.showers
    assert answer.readiness.bath.volume_gal == 30.0
    assert answer.readiness.showers.volume_gal == 12.0
    await stop(hass)


async def test_the_demand_entity_off_is_no_draw(
    hass: HomeAssistant, hass_storage, coordinator
):
    hass.states.async_set("binary_sensor.demand", "off")
    runner = await started(
        hass, coordinator, {"demand_entity": "binary_sensor.demand"}
    )

    inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.drawing is False
    await stop(hass)


async def test_the_hot_water_entities_report_the_answer(
    hass: HomeAssistant, hass_storage, coordinator
):
    runner = await started(hass, coordinator, {})
    feature = hass.data[DOMAIN]["entry1"][DATA_SIMULATOR]
    by_type = {
        type(entity): entity
        for entity in (
            *create_simulator_sensors(feature),
            *create_simulator_binary_sensors(feature),
        )
    }
    answer = runner.answer
    assert answer is not None
    readiness = answer.readiness
    assert readiness is not None

    available = by_type[SimulatorHotWaterAvailableSensor]
    assert available.unique_id == f"{MAC}_simulator_hot_water_available"
    assert available.available
    # Home Assistant's test configuration measures in litres.
    assert available.native_value == round(
        readiness.deliverable_gal * 3.785411784
    )
    assert available.extra_state_attributes["reasons"] == list(
        readiness.reasons
    )

    showers = by_type[SimulatorShowersReadySensor]
    assert showers.native_value == readiness.showers.ready_for_whole
    assert (
        showers.extra_state_attributes["shower_gal"]
        == readiness.showers.volume_gal
    )

    bath = by_type[SimulatorBathReadyBinarySensor]
    assert bath.unique_id == f"{MAC}_simulator_bath_ready"
    assert bath.is_on is readiness.bath.ready
    assert bath.extra_state_attributes["bath_gal"] == readiness.bath.volume_gal
    await stop(hass)


async def test_the_hot_water_is_in_whole_litres_where_litres_are_used(
    hass: HomeAssistant, hass_storage, coordinator
):
    from dataclasses import replace

    from homeassistant.const import UnitOfVolume
    from homeassistant.util.unit_system import (
        METRIC_SYSTEM,
        US_CUSTOMARY_SYSTEM,
    )

    runner = await started(hass, coordinator, {})
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    runner.answer = replace(
        answer, readiness=replace(answer.readiness, deliverable_gal=10.49)
    )

    hass.config.units = METRIC_SYSTEM
    litres = SimulatorHotWaterAvailableSensor(runner, "hot_water_available")
    assert litres.native_unit_of_measurement == UnitOfVolume.LITERS
    # Rounded after the conversion: 39.7 L, not 10 gal's 37.9 L.
    assert litres.native_value == 40

    hass.config.units = US_CUSTOMARY_SYSTEM
    gallons = SimulatorHotWaterAvailableSensor(runner, "hot_water_available")
    assert gallons.native_unit_of_measurement == UnitOfVolume.GALLONS
    assert gallons.native_value == 10
    await stop(hass)


async def test_a_ready_bath_has_no_ready_at_to_move(
    hass: HomeAssistant, hass_storage, coordinator
):
    from dataclasses import replace

    runner = await started(hass, coordinator, {})
    feature = hass.data[DOMAIN]["entry1"][DATA_SIMULATOR]
    (bath,) = create_simulator_binary_sensors(feature)
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    readiness = answer.readiness
    ready_bath = replace(readiness.bath, ready=True, ready_at=readiness.at)
    runner.answer = replace(
        answer, readiness=replace(readiness, bath=ready_bath)
    )

    assert bath.is_on is True
    # The library dates a ready bath by the walk's start: no news.
    assert bath.extra_state_attributes["ready_at"] is None
    await stop(hass)


async def test_the_hot_water_entities_wait_with_the_others(
    hass: HomeAssistant, hass_storage, coordinator
):
    coordinator.data[MAC]["status"] = None
    await started(hass, coordinator, {})
    feature = hass.data[DOMAIN]["entry1"][DATA_SIMULATOR]

    entities = [
        *create_simulator_sensors(feature),
        *create_simulator_binary_sensors(feature),
    ]

    assert not any(entity.available for entity in entities)
    await stop(hass)


async def test_the_inlet_history_is_read_hourly_not_every_walk(
    hass: HomeAssistant, hass_storage, coordinator
):
    from datetime import timedelta
    from unittest.mock import AsyncMock, patch

    from homeassistant.util import dt as dt_util
    from nwp500.sim_home_assistant import HomeAssistantError

    from custom_components.nwp500.simulator.ha_client import InProcessClient

    hass.states.async_set(
        "sensor.supply", "55.0", {"unit_of_measurement": "°F"}
    )
    runner = await started(
        hass,
        coordinator,
        {
            "inlet_entity": "sensor.supply",
            "demand_entity": "binary_sensor.demand",
        },
    )
    read = AsyncMock(side_effect=HomeAssistantError("not recorded"))
    now = dt_util.utcnow().replace(microsecond=0)
    with patch.object(InProcessClient, "read_history", read):
        runner._inlet = None  # noqa: SLF001
        await runner.async_inputs(start=now)
        await runner.async_inputs(start=now + timedelta(minutes=59))
        assert read.await_count == 1
        await runner.async_inputs(start=now + timedelta(minutes=61))
        assert read.await_count == 2
    await stop(hass)


async def test_the_inlet_read_is_laid_on_every_walk(
    hass: HomeAssistant, hass_storage, coordinator
):
    from homeassistant.util import dt as dt_util

    runner = await started(hass, coordinator, {"inlet_entity": "sensor.supply"})
    now = dt_util.utcnow().replace(microsecond=0)
    note = "inlet: the plateau of 2 long draws"
    runner._inlet = (now, 55.0, (note,))  # noqa: SLF001

    inputs = await runner.async_inputs(start=now)

    assert isinstance(inputs, Inputs)
    assert inputs.scenario.environment.inlet_f == 55.0
    assert note in inputs.notes
    # And in the heater's part a planner's document is laid over.
    assert inputs.live is not None
    assert inputs.live.scenario.environment.inlet_f == 55.0
    assert note in inputs.live_notes
    await stop(hass)
