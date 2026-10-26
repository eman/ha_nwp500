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
    answer = runner.answer
    assert answer is not None
    # The walk's own start: a second's tick between the two moves the
    # bath's ready time.
    inputs = await runner.async_inputs(start=answer.computed_at)
    assert isinstance(inputs, Inputs)
    assert inputs.drawing is True
    assert inputs.scenario.environment.ambient_f == pytest.approx(60.0)
    # Without the recorder the inlet's history is not read: the library
    # says so, and answers without it.
    assert any(note.startswith("inlet:") for note in inputs.notes)
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


async def test_without_a_mixing_valve_the_parameters_are_the_librarys(
    hass: HomeAssistant, hass_storage, coordinator
):
    from nwp500.sim_adapters import default_params

    runner = await started(hass, coordinator, {})

    inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    # No valve: the line is the tank's own.
    assert inputs.params.draw == default_params().draw
    await stop(hass)


async def test_a_mixing_valve_is_given_to_the_library(
    hass: HomeAssistant, hass_storage, coordinator
):
    runner = await started(hass, coordinator, {"mixing_valve_f": 120.0})

    inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    draw = inputs.params.draw
    assert (draw.delivery_intercept_f, draw.delivery_slope) == (120.0, 0.0)
    # The book's terms take the same valve.
    book = inputs.params.book_terms()
    assert (book.delivery_intercept_f, book.delivery_slope) == (120.0, 0.0)
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    expected = sim.time_to_ready(inputs.scenario, inputs.params)
    assert answer.readiness.deliverable_gal == expected.deliverable_gal
    await stop(hass)


def _readiness(deliverable: float, *, bath: float = 28.7, shower: float = 12.0):
    """A readiness answer with `deliverable` gallons, as the library's."""
    from types import SimpleNamespace

    from nwp500.sim.ready import showers_ready_for, whole_showers

    return SimpleNamespace(
        deliverable_gal=deliverable,
        bath=SimpleNamespace(volume_gal=bath, ready=deliverable >= bath),
        showers=SimpleNamespace(
            volume_gal=shower,
            ready_for_whole=whole_showers(
                showers_ready_for(deliverable, shower)
            ),
        ),
    )


@pytest.mark.parametrize(
    ("deliverable", "summary"),
    [
        (5.0, "Less than a shower"),
        (12.0, "1 shower"),
        (28.6, "2 showers"),
        (28.7, "1 bath"),
        (38.6, "1 bath"),
        # A top-up of a filled bath is a fill like any other: what is
        # left after a bath, short of another, is no "refill".
        (38.7, "1 bath"),
        (57.4, "2 baths"),
        (67.4, "2 baths"),
        # 86.1 // 28.7 is 2.0 in binary: still 3 baths.
        (86.1, "3 baths"),
    ],
)
def test_the_hot_water_is_counted_in_baths_or_showers(
    deliverable: float, summary: str
):
    from custom_components.nwp500.simulator.runner import capacity_from

    capacity = capacity_from(_readiness(deliverable))  # type: ignore[arg-type]

    assert capacity.summary == summary


def test_the_first_bath_is_the_librarys_answer():
    from custom_components.nwp500.simulator.runner import capacity_from

    # The library says not ready within its probe resolution: no bath,
    # whatever the gallons.
    readiness = _readiness(30.0)
    readiness.bath.ready = False

    capacity = capacity_from(readiness)  # type: ignore[arg-type]

    assert capacity.baths == 0
    assert capacity.summary == "2 showers"


async def test_the_capacity_sensor_says_it_in_words_and_sizes(
    hass: HomeAssistant, hass_storage, coordinator
):
    from homeassistant.util.unit_system import METRIC_SYSTEM

    from custom_components.nwp500.simulator.sensor import (
        SimulatorHotWaterCapacitySensor,
    )

    hass.config.units = METRIC_SYSTEM
    runner = await started(hass, coordinator, {})
    answer = runner.answer
    assert answer is not None and answer.capacity is not None
    capacity = answer.capacity
    sensor = SimulatorHotWaterCapacitySensor(runner, "hot_water_capacity")

    assert sensor.unique_id == f"{MAC}_simulator_hot_water_capacity"
    assert sensor.native_value == capacity.summary
    attributes = sensor.extra_state_attributes
    assert attributes["baths"] == capacity.baths
    assert attributes["showers"] == capacity.showers
    # Each size in litres where litres are used; no refill of its own.
    assert attributes["bath_volume"] == round(
        capacity.bath_gal * 3.785411784, 1
    )
    assert "refill" not in attributes
    assert "refill_volume" not in attributes
    assert attributes["volume_unit"] == "L"
    await stop(hass)


async def test_the_bath_ready_at_is_ahead_or_held_since_ready(
    hass: HomeAssistant, hass_storage, coordinator
):
    from dataclasses import replace
    from datetime import timedelta

    from custom_components.nwp500.simulator.sensor import (
        SimulatorBathReadyAtSensor,
    )

    runner = await started(hass, coordinator, {})
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    readiness = answer.readiness
    sensor = SimulatorBathReadyAtSensor(runner, "bath_ready_at")
    sensor.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    later = answer.computed_at + timedelta(hours=2, seconds=31)

    # Not ready: when the heater on its own has it, to the minute.
    runner.answer = replace(
        answer,
        readiness=replace(
            readiness,
            bath=replace(readiness.bath, ready=False, ready_at=later),
        ),
    )
    sensor._handle_answer()  # noqa: SLF001
    assert sensor.native_value == later.replace(second=0, microsecond=0)
    assert sensor.extra_state_attributes["ready"] is False

    # Ready: since the first walk that found it, held while it stays.
    ready = replace(readiness.bath, ready=True, ready_at=answer.computed_at)
    first = replace(
        answer,
        computed_at=later,
        readiness=replace(readiness, bath=ready),
    )
    runner.answer = first
    sensor._handle_answer()  # noqa: SLF001
    since = later.replace(second=0, microsecond=0)
    assert sensor.native_value == since
    runner.answer = replace(first, computed_at=later + timedelta(minutes=5))
    sensor._handle_answer()  # noqa: SLF001
    assert sensor.native_value == since
    await stop(hass)


async def test_time_to_ready_uses_the_heaters_bath_and_shower(
    hass: HomeAssistant, hass_storage, coordinator
):
    from custom_components.nwp500.simulator.service import (
        async_time_to_ready,
    )

    runner = await started(
        hass, coordinator, {"bath_gal": 40.0, "shower_gal": 15.0}
    )

    answer = await async_time_to_ready(hass, runner)
    asked = await async_time_to_ready(hass, runner, bath_gal=20.0)

    assert answer["result"]["bath"]["volume_gal"] == 40.0
    assert answer["result"]["showers"]["volume_gal"] == 15.0
    assert asked["result"]["bath"]["volume_gal"] == 20.0
    await stop(hass)


async def test_the_bath_ready_since_is_held_across_a_restart_and_a_gap(
    hass: HomeAssistant, hass_storage, coordinator
):
    from dataclasses import replace
    from datetime import timedelta

    from custom_components.nwp500.simulator.sensor import (
        SimulatorBathReadyAtSensor,
    )

    runner = await started(hass, coordinator, {})
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    ready = replace(
        answer,
        readiness=replace(
            answer.readiness,
            bath=replace(answer.readiness.bath, ready=True),
        ),
    )
    runner.answer = ready
    earlier = (answer.computed_at - timedelta(hours=5)).replace(
        second=0, microsecond=0
    )
    sensor = SimulatorBathReadyAtSensor(runner, "bath_ready_at")
    sensor.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    sensor._restored = earlier  # noqa: SLF001 - as async_added_to_hass sets it

    sensor._hold()  # noqa: SLF001
    assert sensor.native_value == earlier

    # A walk with no answer changes nothing held.
    runner.answer = replace(ready, readiness=None)
    sensor._handle_answer()  # noqa: SLF001
    runner.answer = replace(ready, computed_at=answer.computed_at)
    sensor._handle_answer()  # noqa: SLF001
    assert sensor.native_value == earlier
    await stop(hass)


async def test_a_documents_own_sizes_are_not_the_heaters(
    hass: HomeAssistant, hass_storage, coordinator
):
    from custom_components.nwp500.simulator.service import (
        async_get_scenario,
        async_time_to_ready,
    )

    runner = await started(hass, coordinator, {"bath_gal": 40.0})
    scenario = (await async_get_scenario(hass, runner))["result"]
    scenario["params"]["readiness"]["bath_gal"] = 33.0

    answer = await async_time_to_ready(hass, runner, scenario=scenario)

    assert answer["result"]["bath"]["volume_gal"] == 33.0
    await stop(hass)


async def test_only_a_ready_since_time_is_stored_and_restored(
    hass: HomeAssistant, hass_storage, coordinator
):
    from dataclasses import replace
    from datetime import timedelta
    from unittest.mock import AsyncMock, patch

    from homeassistant.components.sensor import SensorExtraStoredData

    from custom_components.nwp500.simulator import sensor as sensor_module
    from custom_components.nwp500.simulator.sensor import (
        SimulatorBathReadyAtSensor,
    )

    runner = await started(hass, coordinator, {})
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    not_ready = replace(
        answer,
        readiness=replace(
            answer.readiness,
            bath=replace(
                answer.readiness.bath,
                ready=False,
                ready_at=answer.computed_at + timedelta(hours=2),
            ),
        ),
    )
    ready = replace(
        answer,
        readiness=replace(
            answer.readiness,
            bath=replace(answer.readiness.bath, ready=True),
        ),
    )
    sensor = SimulatorBathReadyAtSensor(runner, "bath_ready_at")
    sensor.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

    # A forecast is shown, never stored.
    runner.answer = not_ready
    sensor._handle_answer()  # noqa: SLF001
    assert sensor.native_value is not None
    assert sensor.extra_restore_state_data.native_value is None

    # Ready since, stored through a walk with no answer.
    runner.answer = ready
    sensor._handle_answer()  # noqa: SLF001
    since = sensor.native_value
    assert since is not None
    runner.answer = replace(ready, readiness=None)
    sensor._handle_answer()  # noqa: SLF001
    stored = sensor.extra_restore_state_data
    assert stored.native_value == since

    # A new entity, after the restart, takes it back.
    runner.answer = replace(ready, computed_at=since + timedelta(hours=1))
    restarted = SimulatorBathReadyAtSensor(runner, "bath_ready_at")
    restarted.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    restarted.async_get_last_sensor_data = AsyncMock(  # type: ignore[method-assign]
        return_value=SensorExtraStoredData.from_dict(stored.as_dict())
    )
    # The base classes' own setup (the coordinator's listener) aside.
    with patch.object(
        sensor_module.NWP500HotWaterEntity,
        "async_added_to_hass",
        AsyncMock(),
    ):
        await restarted.async_added_to_hass()
    assert restarted.native_value == since
    await stop(hass)


async def test_a_documents_own_tank_carries_no_live_draw(
    hass: HomeAssistant, hass_storage, coordinator
):
    from custom_components.nwp500.simulator.service import async_inputs

    hass.states.async_set("binary_sensor.demand", "on")
    runner = await started(
        hass, coordinator, {"demand_entity": "binary_sensor.demand"}
    )

    live = await async_inputs(hass, runner, scenario=None, use_heater=True)
    tank = await async_inputs(
        hass,
        runner,
        scenario={
            "schema_version": 1,
            "scenario": {"state": {"upper_f": 140.0, "lower_f": 130.0}},
        },
        use_heater=True,
    )

    assert live.drawing is True
    assert tank.drawing is False
    await stop(hass)


async def test_a_heater_not_used_lends_no_sizes(
    hass: HomeAssistant, hass_storage, coordinator
):
    from custom_components.nwp500.simulator.service import (
        async_get_scenario,
        async_time_to_ready,
    )

    runner = await started(hass, coordinator, {"bath_gal": 40.0})
    document = (await async_get_scenario(hass, runner))["result"]
    del document["params"]

    answer = await async_time_to_ready(
        hass, runner, scenario=document, use_heater=False
    )

    assert answer["result"]["bath"]["volume_gal"] == 28.7
    await stop(hass)
