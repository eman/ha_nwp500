"""The tank's layers, from the household's hot water draw flow.

With a draw flow, the library replays the heater's history and the
flow's through the tank's layers (`HomeAssistantTank`), and the walks and
the actions start from them. Without one there are no layers, which is
not an error: the library walks from the tank's two probes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from nwp500 import sim
from nwp500.sim_home_assistant import HomeAssistantTank, LayersAnswer

from custom_components.nwp500.const import DOMAIN
from custom_components.nwp500.simulator import service
from custom_components.nwp500.simulator.runner import Inputs

from .conftest import MAC
from .test_hot_water import ALL_INPUTS, started, stop

FLOW = {"draw_flow_entity": "sensor.draw_flow"}
# The default tank's twelve layers, the bottom first.
LAYERS = tuple(70.0 + 5.0 * i for i in range(12))
ANSWER = LayersAnswer(LAYERS, ("layers: replayed 24 h",))
ESTIMATED = "tank_profile_estimated"

# Each of the heater's entities, as this integration registers them.
HEATER = (
    ("sensor", "last_status"),
    ("sensor", "tank_upper_temperature"),
    ("sensor", "tank_lower_temperature"),
    ("binary_sensor", "comp_use"),
    ("binary_sensor", "heat_upper_use"),
    ("binary_sensor", "heat_lower_use"),
    ("sensor", "current_heat_use"),
    ("binary_sensor", "dhw_use"),
    ("sensor", "hp_lower_off_temp_setting"),
    ("sensor", "operation_mode"),
    ("binary_sensor", "anti_legionella_operation_busy"),
    ("sensor", "error_code"),
    ("sensor", "current_inst_power"),
    ("sensor", "energy_to_setpoint"),
    ("sensor", "ambient_temperature"),
    ("sensor", "dhw_temperature_setting"),
)


def _register_heater(hass: HomeAssistant) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        key: registry.async_get_or_create(
            domain, DOMAIN, f"{MAC}_{key}"
        ).entity_id
        for domain, key in HEATER
    }


def _patched(answer: LayersAnswer = ANSWER) -> tuple[Any, Any, Any]:
    read = AsyncMock()
    layers = MagicMock(return_value=answer)
    return (
        patch.object(HomeAssistantTank, "read", read),
        patch.object(HomeAssistantTank, "layers", layers),
        (read, layers),
    )


async def test_without_a_draw_flow_there_are_no_layers(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    runner = await started(hass, coordinator, {})

    inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.layers_f is None
    assert inputs.scenario.state.layers_f is None
    assert inputs.inlet_measured is False
    assert not [n for n in inputs.notes if n.startswith("layers:")]
    assert runner._tank is None  # noqa: SLF001
    # The library's answer from the probes alone, and it says so.
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    expected = sim.time_to_ready(inputs.scenario, inputs.params)
    assert answer.readiness.deliverable_gal == expected.deliverable_gal
    assert ESTIMATED in answer.readiness.reasons
    await stop(hass)


async def test_every_input_is_given_to_the_tanks_keeper(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    ids = _register_heater(hass)
    runner = await started(hass, coordinator, {**ALL_INPUTS, **FLOW})
    keeper = runner._keeper()  # noqa: SLF001

    assert keeper is not None
    assert keeper.flow == "sensor.draw_flow"
    # The replay reads the pump and the air; the inlet and the demand are
    # the environment's, not the tank's.
    assert keeper.entities.circulation_speed == "sensor.pump"
    assert keeper.entities.air == "sensor.air"
    assert keeper.entities.inlet is None
    assert keeper.entities.demand is None
    heater = keeper.heater
    assert heater.heard == ids["last_status"]
    assert heater.upper == ids["tank_upper_temperature"]
    assert heater.lower == ids["tank_lower_temperature"]
    assert heater.compressor == ids["comp_use"]
    assert heater.upper_element == ids["heat_upper_use"]
    assert heater.lower_element == ids["heat_lower_use"]
    assert heater.heat_source == ids["current_heat_use"]
    assert heater.dhw_in_use == ids["dhw_use"]
    assert heater.target == ids["hp_lower_off_temp_setting"]
    assert heater.operation_mode == ids["operation_mode"]
    assert heater.anti_legionella == ids["anti_legionella_operation_busy"]
    assert heater.error_code == ids["error_code"]
    assert heater.power == ids["current_inst_power"]
    assert heater.energy_to_setpoint == ids["energy_to_setpoint"]
    assert heater.ambient == ids["ambient_temperature"]
    assert heater.setpoint == ids["dhw_temperature_setting"]
    # One keeper, holding its history from walk to walk.
    assert runner._keeper() is keeper  # noqa: SLF001
    await stop(hass)


async def test_a_disabled_setpoint_sensor_is_left_out(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    ids = _register_heater(hass)
    er.async_get(hass).async_update_entity(
        ids["dhw_temperature_setting"],
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    runner = await started(hass, coordinator, FLOW)
    keeper = runner._keeper()  # noqa: SLF001

    assert keeper is not None
    assert keeper.heater.setpoint is None
    await stop(hass)


async def test_only_the_heaters_own_entities_with_a_draw_flow(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    runner = await started(hass, coordinator, FLOW)
    keeper = runner._keeper()  # noqa: SLF001

    assert keeper is not None
    e = keeper.entities
    assert (e.circulation_speed, e.air) == (None, None)
    await stop(hass)


async def test_the_walk_starts_from_the_layers(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    reading, laying, (read, layers) = _patched()
    now = dt_util.utcnow().replace(microsecond=0)
    with reading, laying:
        runner = await started(hass, coordinator, FLOW)
        inputs = await runner.async_inputs(start=now)
        await runner._async_run()  # noqa: SLF001
        await hass.async_block_till_done()

    assert isinstance(inputs, Inputs)
    read.assert_any_await(now)
    # At the start, with the heater's probes laid over the replay.
    state = inputs.scenario.state
    layers.assert_any_call(now, inputs.params, (state.upper_f, state.lower_f))
    assert inputs.layers_f == LAYERS
    assert state.layers_f == LAYERS
    # The scenario an action lays its document over has none.
    assert inputs.live is not None
    assert inputs.live.scenario.state.layers_f is None
    assert "layers: replayed 24 h" in inputs.notes
    assert "layers: replayed 24 h" in inputs.live_notes
    # The hot water entities answer from the layers.
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    assert ESTIMATED not in answer.readiness.reasons
    await stop(hass)


async def test_no_layers_now_walks_from_the_probes(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    unknown = LayersAnswer(None, ("layers: sensor.draw_flow is unknown now",))
    reading, laying, _ = _patched(unknown)
    with reading, laying:
        runner = await started(hass, coordinator, FLOW)
        inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.layers_f is None
    assert inputs.scenario.state.layers_f is None
    assert "layers: sensor.draw_flow is unknown now" in inputs.notes
    await stop(hass)


async def test_a_heater_not_yet_registered_has_no_layers_yet(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    runner = await started(hass, coordinator, FLOW)

    inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.layers_f is None
    assert any(n.startswith("layers:") for n in inputs.notes)
    await stop(hass)


async def test_a_measured_inlet_is_said_to_be(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    runner = await started(hass, coordinator, {"inlet_entity": "sensor.supply"})
    with patch.object(
        type(runner),
        "_async_inlet",
        AsyncMock(return_value=(55.0, ())),
    ):
        inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.scenario.environment.inlet_f == 55.0
    assert inputs.inlet_measured is True
    await stop(hass)


async def test_the_actions_use_the_heaters_layers_for_its_own_scenario(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    reading, laying, _ = _patched()
    with reading, laying:
        runner = await started(hass, coordinator, FLOW)
        inputs = await service.async_inputs(
            hass, runner, scenario=None, use_heater=True
        )
        # Another tank: the heater's layers aren't its.
        document = (await service.async_get_scenario(hass, runner))["result"]
        document["scenario"]["state"]["layers_f"] = None
        document["params"]["tank"]["volume_gal"] = 80.0
        other = await service.async_inputs(
            hass, runner, scenario=document, use_heater=True
        )
        # Other probes: nor are they this state's.
        probes = (await service.async_get_scenario(hass, runner))["result"]
        probes["scenario"]["state"]["layers_f"] = None
        probes["scenario"]["state"]["upper_f"] = 100.0
        warmer = await service.async_inputs(
            hass,
            runner,
            scenario=probes,
            use_heater=True,
        )
        alone = await service.async_inputs(
            hass, runner, scenario=document, use_heater=False
        )
        # The heater's own document, as the scenario action gives it,
        # brings its layers back.
        again = await service.async_inputs(
            hass,
            runner,
            scenario=(await service.async_get_scenario(hass, runner))["result"],
            use_heater=True,
        )

    assert inputs.layers_f == LAYERS
    assert inputs.scenario.state.layers_f == LAYERS
    assert other.layers_f is None
    assert other.scenario.state.layers_f is None
    assert any("layers: not used" in n for n in other.notes)
    assert warmer.layers_f is None
    assert any("layers: not used" in n for n in warmer.notes)
    assert alone.layers_f is None
    assert again.layers_f == LAYERS
    assert not any("layers: not used" in n for n in again.notes)
    await stop(hass)


async def test_the_actions_keep_the_layers_with_a_mixing_valve(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    reading, laying, _ = _patched()
    with reading, laying:
        runner = await started(
            hass, coordinator, {**FLOW, "mixing_valve_f": 120.0}
        )
        inputs = await service.async_inputs(
            hass, runner, scenario=None, use_heater=True
        )

    # The heater's scenario, the valve in its parameters: its layers.
    assert inputs.params.draw.delivery_intercept_f == 120.0
    assert inputs.layers_f == LAYERS
    await stop(hass)


async def test_the_flow_counts_delivered_water_unless_told_tank(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    runner = await started(hass, coordinator, FLOW)
    keeper = runner._keeper()  # noqa: SLF001
    assert keeper is not None
    assert keeper.flow_volume == "delivered"

    # Told the flow counts tank water, a new keeper replays it so.
    runner.options = {**FLOW, "draw_flow_volume": "tank"}
    tank = runner._keeper()  # noqa: SLF001

    assert tank is not None and tank is not keeper
    assert tank.flow_volume == "tank"
    assert runner._keeper() is tank  # noqa: SLF001
    await stop(hass)


async def test_a_changed_input_makes_a_new_keeper(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    runner = await started(hass, coordinator, FLOW)
    keeper = runner._keeper()  # noqa: SLF001
    assert keeper is not None

    # Another flow entity, or a pump: a keeper reading those.
    runner.options = {"draw_flow_entity": "sensor.other_flow"}
    flowed = runner._keeper()  # noqa: SLF001
    runner.options = {
        "draw_flow_entity": "sensor.other_flow",
        "circulation_speed_entity": "sensor.pump",
    }
    pumped = runner._keeper()  # noqa: SLF001

    assert flowed is not None and flowed is not keeper
    assert flowed.flow == "sensor.other_flow"
    assert pumped is not None and pumped is not flowed
    assert pumped.entities.circulation_speed == "sensor.pump"
    await stop(hass)


async def test_a_renamed_heater_entity_makes_a_new_keeper(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    ids = _register_heater(hass)
    runner = await started(hass, coordinator, FLOW)
    keeper = runner._keeper()  # noqa: SLF001
    assert keeper is not None
    # Unchanged, the keeper and its history are kept.
    assert runner._keeper() is keeper  # noqa: SLF001

    er.async_get(hass).async_update_entity(
        ids["tank_upper_temperature"], new_entity_id="sensor.renamed_upper"
    )
    renamed = runner._keeper()  # noqa: SLF001

    assert renamed is not None and renamed is not keeper
    assert renamed.heater.upper == "sensor.renamed_upper"
    await stop(hass)
