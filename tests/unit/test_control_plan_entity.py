"""External control's plan entity: followed when renamed, missed when gone."""

from __future__ import annotations

import pytest
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500.const import DOMAIN
from custom_components.nwp500.control.plan_entity import (
    PlanEntityWatch,
    plan_entity_issue_id,
)


def _entry(hass: HomeAssistant, entity_id: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={"control_enabled": True, "control_intent_entity": entity_id},
    )
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, plan_entity_issue_id("entry1")
    )


@pytest.mark.asyncio
async def test_a_renamed_plan_entity_is_followed(hass: HomeAssistant):
    registry = er.async_get(hass)
    plan = registry.async_get_or_create("sensor", "mqtt", "plan")
    entry = _entry(hass, plan.entity_id)
    watch = PlanEntityWatch(hass, entry)
    watch.async_start()

    registry.async_update_entity(plan.entity_id, new_entity_id="sensor.renamed")
    await hass.async_block_till_done()

    assert entry.options["control_intent_entity"] == "sensor.renamed"
    assert _issue(hass) is None
    watch.async_stop()


@pytest.mark.asyncio
async def test_a_removed_plan_entity_is_reported_until_it_is_back(
    hass: HomeAssistant,
):
    registry = er.async_get(hass)
    plan = registry.async_get_or_create("sensor", "mqtt", "plan")
    entry = _entry(hass, plan.entity_id)
    watch = PlanEntityWatch(hass, entry)
    watch.async_start()

    registry.async_remove(plan.entity_id)
    await hass.async_block_till_done()
    issue = _issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"entity_id": plan.entity_id}
    # Nothing is moved: the plan in force stays (spec section 6.1).
    assert entry.options["control_intent_entity"] == plan.entity_id

    hass.states.async_set(plan.entity_id, "plan-2")
    await hass.async_block_till_done()
    assert _issue(hass) is None
    watch.async_stop()


@pytest.mark.asyncio
async def test_a_plan_entity_home_assistant_never_had_is_reported_once_started(
    hass: HomeAssistant,
):
    hass.set_state(CoreState.starting)
    entry = _entry(hass, "sensor.nowhere")
    watch = PlanEntityWatch(hass, entry)
    watch.async_start()
    await hass.async_block_till_done()
    # Still starting: an entity may not have its state yet.
    assert _issue(hass) is None

    await hass.async_start()
    await hass.async_block_till_done()

    assert _issue(hass) is not None
    watch.async_stop()


@pytest.mark.asyncio
async def test_a_plan_entity_with_a_state_but_no_registry_entry_is_there(
    hass: HomeAssistant,
):
    hass.states.async_set("sensor.yaml_plan", "plan-1")
    entry = _entry(hass, "sensor.yaml_plan")
    watch = PlanEntityWatch(hass, entry)

    watch.async_start()
    await hass.async_block_till_done()

    assert _issue(hass) is None
    watch.async_stop()


@pytest.mark.asyncio
async def test_a_yaml_plan_entity_removed_at_runtime_is_reported(
    hass: HomeAssistant,
):
    hass.states.async_set("sensor.yaml_plan", "plan-1")
    entry = _entry(hass, "sensor.yaml_plan")
    watch = PlanEntityWatch(hass, entry)
    watch.async_start()
    await hass.async_block_till_done()

    hass.states.async_remove("sensor.yaml_plan")
    await hass.async_block_till_done()

    assert _issue(hass) is not None
    # Stopped, the issue goes: the next start judges it again at once.
    watch.async_stop()
    assert _issue(hass) is None
