"""The modelled path: the compressor's next start and the tank's probes.

When the compressor runs, the path shows the probes rising to the
setpoint and Simulated hot water ready says when; when it doesn't, the
path shows them falling and Simulated compressor start says when it
starts. Charts read the path over the websocket API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import METRIC_SYSTEM
from nwp500 import sim
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500 import simulator_ws
from custom_components.nwp500.const import (
    CONF_SIMULATOR,
    CONF_SIMULATOR_ENABLED,
    DATA_SIMULATOR,
    DOMAIN,
    simulator_started_signal,
)
from custom_components.nwp500.simulator import (
    SimulatorFeature,
    async_setup_simulator,
    async_unload_simulator,
    create_simulator_sensors,
)
from custom_components.nwp500.simulator.runner import (
    CHAINED,
    Answer,
    SimulatorRunner,
    answer_from,
)
from custom_components.nwp500.simulator.sensor import (
    SimulatorCompressorStartSensor,
)

from .conftest import MAC, make_status

ON = 2


@pytest.fixture
async def feature(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
) -> AsyncIterator[SimulatorFeature]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: {MAC: {}}},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = coordinator
    coordinator.entry = entry
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, MAC)}
    )
    await async_setup_simulator(hass, entry, coordinator)
    await hass.async_block_till_done()
    yield hass.data[DOMAIN][entry.entry_id][DATA_SIMULATOR]
    await async_unload_simulator(hass, entry)


def heater_device(hass: HomeAssistant) -> dr.DeviceEntry:
    registry = dr.async_get(hass)
    (device,) = dr.async_entries_for_config_entry(registry, "entry1")
    return device


def compressor_start(
    feature: SimulatorFeature,
) -> SimulatorCompressorStartSensor:
    return next(
        s
        for s in create_simulator_sensors(feature)
        if isinstance(s, SimulatorCompressorStartSensor)
    )


async def walked(runner: SimulatorRunner) -> tuple[Any, Any]:
    """The runner's inputs now and the library's walk of them."""
    inputs = await runner.async_inputs()
    assert not isinstance(inputs, str)
    return inputs, sim.simulate(inputs.scenario, inputs.params)


async def test_idle_the_sensor_says_when_the_compressor_starts(
    hass: HomeAssistant, feature: SimulatorFeature
):
    runner = feature.runners[MAC]
    inputs, result = await walked(runner)
    first = next(h for h in result.heating if h.source == "compressor")
    answer = answer_from(result, inputs)
    runner.answer = answer
    sensor = compressor_start(feature)

    assert sensor.unique_id == f"{MAC}_simulator_compressor_start"
    assert sensor.translation_key == "simulator_compressor_start"
    assert sensor.device_class is SensorDeviceClass.TIMESTAMP
    assert answer.compressor_start == first.start.at
    assert sensor.native_value == first.start.at.replace(
        second=0, microsecond=0
    )
    attributes = sensor.extra_state_attributes
    assert attributes["running"] is False
    assert attributes["cause"] == first.start.cause
    assert attributes["trigger"] == first.start.trigger


async def test_an_element_heating_first_is_not_the_compressors_start(
    hass: HomeAssistant, feature: SimulatorFeature
):
    runner = feature.runners[MAC]
    inputs, result = await walked(runner)
    compressor = result.heating[0]
    element = replace(
        compressor,
        start=replace(compressor.start, at=result.start, source="element"),
        source="element",
    )
    result.simulation.heating.insert(0, element)

    answer = answer_from(result, inputs)

    assert answer.start == result.start
    assert answer.compressor_start == compressor.start.at


async def test_running_the_compressor_has_no_next_start(
    hass: HomeAssistant, coordinator: MagicMock, feature: SimulatorFeature
):
    coordinator.data[MAC]["status"] = make_status(compUse=ON)
    runner = feature.runners[MAC]
    inputs, result = await walked(runner)
    runner.answer = answer_from(result, inputs)
    sensor = compressor_start(feature)

    assert sensor.available
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["running"] is True
    # The heating it is in ends when the tank comes to temperature.
    assert runner.answer.end == result.heating[0].end


async def test_the_path_is_the_walks_probes(
    hass: HomeAssistant, feature: SimulatorFeature
):
    runner = feature.runners[MAC]
    inputs, result = await walked(runner)

    answer = answer_from(result, inputs)

    assert len(answer.path) == len(result.points)
    for sample, point in zip(answer.path, result.points, strict=True):
        assert sample.at == point.at
        assert sample.upper_f == round(point.upper_f, 1)
        assert sample.lower_f == round(point.lower_f, 1)
        assert sample.heating is point.heating
    # Heating, the lower probe rises toward the setpoint.
    heating = [s for s in answer.path if s.heating]
    assert heating and heating[-1].lower_f > answer.path[0].lower_f


async def test_the_path_document_is_in_home_assistants_unit(
    hass: HomeAssistant, feature: SimulatorFeature
):
    runner = feature.runners[MAC]
    inputs, result = await walked(runner)
    answer = answer_from(result, inputs)

    fahrenheit = answer.path_document(UnitOfTemperature.FAHRENHEIT)
    celsius = answer.path_document(UnitOfTemperature.CELSIUS)

    first = answer.path[0]
    assert fahrenheit["points"][0]["upper"] == first.upper_f
    assert celsius["unit"] == UnitOfTemperature.CELSIUS
    assert celsius["points"][0]["upper"] == round((first.upper_f - 32) / 1.8, 1)
    assert celsius["points"][0]["at"] == first.at.isoformat()
    assert answer.stop == "target"
    assert celsius["ready_at"] == answer.end.isoformat()
    assert celsius["heating_start"] == answer.start.isoformat()
    assert celsius["compressor_start"] == answer.compressor_start.isoformat()
    # Cut by the horizon, the heating has no ready time.
    cut = replace(answer, stop="horizon").path_document("°F")
    assert cut["ready_at"] is None


async def test_the_websocket_answers_with_the_path(
    hass: HomeAssistant, hass_ws_client, feature: SimulatorFeature
):
    assert await async_setup_component(hass, "websocket_api", {})
    hass.config.units = METRIC_SYSTEM
    simulator_ws.async_register(hass)
    runner = feature.runners[MAC]
    client = await hass_ws_client(hass)
    device = heater_device(hass)
    assert runner.answer is not None

    await client.send_json(
        {"id": 1, "type": "nwp500/simulator/path", "device_id": device.id}
    )
    reply = await client.receive_json()

    assert reply["success"]
    assert reply["result"] == runner.answer.path_document(
        UnitOfTemperature.CELSIUS
    )
    assert reply["result"]["points"]


async def test_the_websocket_names_what_it_cannot_answer(
    hass: HomeAssistant,
    hass_ws_client,
    coordinator: MagicMock,
    feature: SimulatorFeature,
):
    assert await async_setup_component(hass, "websocket_api", {})
    simulator_ws.async_register(hass)
    client = await hass_ws_client(hass)

    await client.send_json(
        {"id": 1, "type": "nwp500/simulator/path", "device_id": "nowhere"}
    )
    reply = await client.receive_json()
    assert reply["error"]["code"] == "not_found"

    feature.runners.pop(MAC).async_stop()
    device = heater_device(hass)
    await client.send_json(
        {"id": 2, "type": "nwp500/simulator/path", "device_id": device.id}
    )
    reply = await client.receive_json()
    assert reply["error"]["code"] == "simulator_not_enabled"


async def test_the_subscription_follows_each_walk_and_a_reload(
    hass: HomeAssistant, hass_ws_client, feature: SimulatorFeature
):
    assert await async_setup_component(hass, "websocket_api", {})
    simulator_ws.async_register(hass)
    runner = feature.runners[MAC]
    client = await hass_ws_client(hass)
    device = heater_device(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "nwp500/simulator/subscribe_path",
            "device_id": device.id,
        }
    )
    assert (await client.receive_json())["success"]
    now = await client.receive_json()
    assert now["type"] == "event"
    assert now["event"]["points"]

    # A new walk is sent.
    assert runner.answer is not None
    runner._publish(replace(runner.answer, stop="horizon"))  # noqa: SLF001
    walk = await client.receive_json()
    assert walk["event"]["ready_at"] is None

    # A reload's new runner is followed; the old one is not.
    reloaded = SimulatorRunner(hass, runner.coordinator, MAC, {})
    reloaded.answer = Answer(computed_at=runner.answer.computed_at, error="x")
    feature.runners[MAC] = reloaded
    runner.async_stop()
    async_dispatcher_send(hass, simulator_started_signal("entry1"))
    await hass.async_block_till_done()
    assert (await client.receive_json())["event"]["error"] == "x"
    assert runner._listeners == []  # noqa: SLF001
    reloaded._publish(runner.answer)  # noqa: SLF001
    assert (await client.receive_json())["event"]["points"]

    await client.send_json(
        {"id": 2, "type": "unsubscribe_events", "subscription": 1}
    )
    assert (await client.receive_json())["success"]
    assert reloaded._listeners == []  # noqa: SLF001


async def test_a_user_who_cannot_read_the_heater_gets_no_path(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    feature: SimulatorFeature,
):
    assert await async_setup_component(hass, "websocket_api", {})
    simulator_ws.async_register(hass)
    device = heater_device(hass)
    heater = (
        er.async_get(hass)
        .async_get_or_create(
            "water_heater", DOMAIN, f"{MAC}_water_heater", device_id=device.id
        )
        .entity_id
    )
    client = await hass_ws_client(hass, hass_read_only_access_token)

    # Another entity readable, the heater not.
    hass_read_only_user.mock_policy(
        {"entities": {"entity_ids": {"sensor.elsewhere": True}}}
    )
    for i, ask in enumerate(
        ({"device_id": device.id}, {"entity_id": heater}), start=1
    ):
        await client.send_json(
            {"id": i, "type": "nwp500/simulator/subscribe_path", **ask}
        )
        reply = await client.receive_json()
        assert reply["error"]["code"] == "unauthorized"

    hass_read_only_user.mock_policy(
        {"entities": {"entity_ids": {heater: True}}}
    )
    await client.send_json(
        {"id": 3, "type": "nwp500/simulator/path", "device_id": device.id}
    )
    assert (await client.receive_json())["success"]


async def test_a_subscription_hears_the_simulator_stop(
    hass: HomeAssistant, hass_ws_client, feature: SimulatorFeature
):
    assert await async_setup_component(hass, "websocket_api", {})
    simulator_ws.async_register(hass)
    runner = feature.runners[MAC]
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "nwp500/simulator/subscribe_path",
            "device_id": heater_device(hass).id,
        }
    )
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["event"]["points"]

    await feature.async_stop()
    await hass.async_block_till_done()

    off = (await client.receive_json())["event"]
    assert off["waiting"] == "simulator_off"
    assert off["points"] == []
    assert runner._listeners == []  # noqa: SLF001


@pytest.mark.parametrize("stop", ["cap", "mode", "write"])
async def test_a_heating_stopped_short_is_not_at_temperature(
    hass: HomeAssistant, feature: SimulatorFeature, stop: str
):
    runner = feature.runners[MAC]
    inputs, result = await walked(runner)
    answer = replace(answer_from(result, inputs), stop=stop)

    document = answer.path_document("°F")

    assert document["ready_at"] is None
    assert document["heating_end"] == answer.end.isoformat()
    assert document["stop"] == stop
    at_target = replace(answer, stop="target").path_document("°F")
    assert at_target["ready_at"] == answer.end.isoformat()


async def test_electrics_upper_then_lower_element_is_one_heating(
    hass: HomeAssistant, coordinator: MagicMock, feature: SimulatorFeature
):
    # Electric, its upper element on: the library ends the upper element
    # at its target and starts the lower at once.
    coordinator.data[MAC]["status"] = make_status(
        dhwOperationSetting=2, heatUpperUse=ON, compUse=1, heatLowerUse=1
    )
    runner = feature.runners[MAC]
    inputs, result = await walked(runner)
    first, second = result.heating[:2]
    assert first.source == second.source == "element"
    assert second.start.at <= first.end + CHAINED

    answer = answer_from(result, inputs)

    chain = [first, second]
    for interval in result.heating[2:]:
        if interval.start.at > chain[-1].end + CHAINED:
            break
        chain.append(interval)
    assert answer.start == first.start.at
    assert answer.end == chain[-1].end
    assert answer.stop == chain[-1].stop
    assert answer.end > first.end


async def test_a_subscription_waits_for_the_simulator_to_start(
    hass: HomeAssistant, hass_ws_client, feature: SimulatorFeature
):
    assert await async_setup_component(hass, "websocket_api", {})
    simulator_ws.async_register(hass)
    runner = feature.runners.pop(MAC)
    runner.async_stop()
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "nwp500/simulator/subscribe_path",
            "device_id": heater_device(hass).id,
        }
    )
    # Taken, and empty until the simulator runs.
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["event"]["waiting"] == "simulator_off"

    started = SimulatorRunner(hass, runner.coordinator, MAC, {})
    assert runner.answer is not None
    started.answer = runner.answer
    feature.runners[MAC] = started
    async_dispatcher_send(hass, simulator_started_signal("entry1"))
    await hass.async_block_till_done()

    assert (await client.receive_json())["event"]["points"]


async def test_the_path_says_what_the_hot_water_is_enough_for(
    hass: HomeAssistant, feature: SimulatorFeature
):
    answer = feature.runners[MAC].answer
    assert answer is not None
    assert answer.capacity is not None and answer.readiness is not None
    bath = answer.readiness.bath

    hot_water = answer.path_document("°F")["hot_water"]

    assert hot_water["summary"] == answer.capacity.summary
    assert hot_water["baths"] == answer.capacity.baths
    assert hot_water["showers"] == answer.capacity.showers
    assert hot_water["bath_ready"] is bath.ready
    if bath.ready:
        assert hot_water["bath_ready_at"] is None
    not_ready = replace(
        answer,
        readiness=replace(
            answer.readiness,
            bath=replace(bath, ready=False, ready_at=answer.computed_at),
        ),
    )
    assert not_ready.path_document("°F")["hot_water"]["bath_ready_at"] == (
        answer.computed_at.isoformat()
    )


async def test_an_element_inside_a_longer_compressor_run_ends_nothing(
    hass: HomeAssistant, feature: SimulatorFeature
):
    from datetime import timedelta

    runner = feature.runners[MAC]
    inputs, result = await walked(runner)
    (template,) = result.heating[:1]
    t0 = template.start.at
    compressor = replace(
        template,
        source="compressor",
        end=t0 + timedelta(hours=2),
        stop="target",
        input_wh=1000.0,
    )
    # A short element run within it, ending first and for another reason.
    element = replace(
        template,
        source="element",
        start=replace(template.start, at=t0 + timedelta(minutes=30)),
        end=t0 + timedelta(minutes=50),
        stop="mode",
        input_wh=500.0,
    )
    later = replace(
        template,
        start=replace(template.start, at=t0 + timedelta(hours=1, minutes=59)),
        end=t0 + timedelta(hours=2, minutes=10),
        stop="cap",
        input_wh=100.0,
    )

    walk = replace(
        result,
        simulation=replace(
            result.simulation, heating=[compressor, element, later]
        ),
    )

    answer = answer_from(walk, inputs)

    # The element's end is not the heating's: the compressor runs on,
    # and the run that starts within a minute of its end joins it.
    assert answer.end == later.end
    assert answer.stop == "cap"
    assert answer.energy_kwh == 1.6
