"""The tank's heat, booked from the household's hot water draw flow.

With a draw flow, the library keeps a book of the tank's heat from the
heater's history and the flow's (`HomeAssistantBook`), and the walks and
the actions are given it. Without one there is no book, which is not an
error: the library answers from the tank's probes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from nwp500 import sim
from nwp500.sim.book import Booked
from nwp500.sim_home_assistant import BookAnswer, HomeAssistantBook

from custom_components.nwp500.const import DOMAIN
from custom_components.nwp500.simulator import service
from custom_components.nwp500.simulator.runner import Inputs

from .conftest import MAC
from .test_hot_water import ALL_INPUTS, started, stop

FLOW = {"draw_flow_entity": "sensor.draw_flow"}
BOOKED = Booked(heat_btu=21000.0, anchor_age_h=3.0, inlet_f=55.0)
ANSWER = BookAnswer(BOOKED, True, ("book: anchored 3.0 h ago",))

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
)


def _register_heater(hass: HomeAssistant) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        key: registry.async_get_or_create(
            domain, DOMAIN, f"{MAC}_{key}"
        ).entity_id
        for domain, key in HEATER
    }


def _patched(answer: BookAnswer = ANSWER) -> tuple[Any, Any, Any]:
    read = AsyncMock()
    book = MagicMock(return_value=answer)
    return (
        patch.object(HomeAssistantBook, "read", read),
        patch.object(HomeAssistantBook, "book", book),
        (read, book),
    )


async def test_without_a_draw_flow_there_is_no_book(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    runner = await started(hass, coordinator, {})

    inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.book is None
    assert inputs.inlet_measured is False
    assert not [n for n in inputs.notes if n.startswith("book:")]
    assert runner._book is None  # noqa: SLF001
    # The library's answer from the probes alone.
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    expected = sim.time_to_ready(inputs.scenario, inputs.params)
    assert answer.readiness.deliverable_gal == expected.deliverable_gal
    await stop(hass)


async def test_every_input_is_given_to_the_books_keeper(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    ids = _register_heater(hass)
    runner = await started(hass, coordinator, {**ALL_INPUTS, **FLOW})
    with patch.object(HomeAssistantBook, "read", AsyncMock()):
        keeper = runner._keeper()  # noqa: SLF001

    assert keeper is not None
    assert keeper.flow == "sensor.draw_flow"
    assert keeper.entities.circulation_speed == "sensor.pump"
    assert keeper.entities.inlet == "sensor.supply"
    assert keeper.entities.demand == "binary_sensor.demand"
    assert keeper.entities.air == "sensor.air"
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
    # One keeper, holding its history from walk to walk.
    assert runner._keeper() is keeper  # noqa: SLF001
    await stop(hass)


async def test_only_the_heaters_own_entities_with_a_draw_flow(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    runner = await started(hass, coordinator, FLOW)
    keeper = runner._keeper()  # noqa: SLF001

    assert keeper is not None
    e = keeper.entities
    assert (e.circulation_speed, e.inlet, e.demand, e.air) == (None,) * 4
    await stop(hass)


async def test_the_book_is_given_to_the_walk(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    reading, booking, (read, book) = _patched()
    now = dt_util.utcnow().replace(microsecond=0)
    with reading, booking:
        runner = await started(hass, coordinator, FLOW)
        inputs = await runner.async_inputs(start=now)
        await runner._async_run()  # noqa: SLF001
        await hass.async_block_till_done()

    assert isinstance(inputs, Inputs)
    read.assert_any_await(now)
    book.assert_any_call(now, inputs.params)
    assert inputs.book == BOOKED
    assert inputs.inlet_measured is True
    assert "book: anchored 3.0 h ago" in inputs.notes
    assert "book: anchored 3.0 h ago" in inputs.live_notes
    # The hot water entities answer from the book.
    answer = runner.answer
    assert answer is not None and answer.readiness is not None
    walked = await runner.async_inputs()
    assert isinstance(walked, Inputs)
    expected = sim.time_to_ready(
        walked.scenario,
        walked.params,
        book=BOOKED,
        inlet_measured=True,
    )
    assert answer.readiness.deliverable_gal == expected.deliverable_gal
    assert "no_heat_book" not in answer.readiness.reasons
    await stop(hass)


async def test_a_heater_not_yet_registered_has_no_book_yet(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    runner = await started(hass, coordinator, FLOW)

    inputs = await runner.async_inputs()

    assert isinstance(inputs, Inputs)
    assert inputs.book is None
    assert any(n.startswith("book:") for n in inputs.notes)
    await stop(hass)


async def test_the_actions_use_the_heaters_book_for_its_own_scenario(
    hass: HomeAssistant, hass_storage, coordinator: MagicMock
):
    _register_heater(hass)
    reading, booking, _ = _patched()
    with reading, booking:
        runner = await started(hass, coordinator, FLOW)
        inputs = await service.async_inputs(
            hass, runner, scenario=None, use_heater=True
        )
        # Another tank: the heater's book isn't its heat.
        document = (await service.async_get_scenario(hass, runner))["result"]
        document["params"]["tank"]["volume_gal"] = 80.0
        other = await service.async_inputs(
            hass, runner, scenario=document, use_heater=True
        )
        alone = await service.async_inputs(
            hass, runner, scenario=document, use_heater=False
        )

    assert inputs.book == BOOKED
    assert inputs.inlet_measured is True
    assert other.book is None
    assert other.inlet_measured is False
    assert any("book: not used" in n for n in other.notes)
    assert alone.book is None
    await stop(hass)
