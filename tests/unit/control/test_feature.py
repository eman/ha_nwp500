"""The feature object and its set-up, unload and removal."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500.const import (
    CONF_CONTROL_ENABLED,
    CONF_CONTROL_INTENT_ENTITY,
    DATA_CONTROL,
    DOMAIN,
)
from custom_components.nwp500.control import (
    ControlFeature,
    async_setup_control,
    async_unload_control,
)
from custom_components.nwp500.control.store import storage_key

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={
            CONF_CONTROL_ENABLED: True,
            CONF_CONTROL_INTENT_ENTITY: "sensor.intent",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator(mock_device) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {MAC: {"device": mock_device, "status": None}}
    coordinator.device_features = {}
    coordinator.reservation_schedules = {}
    coordinator.tou_schedules = {}
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    return coordinator


@pytest.mark.asyncio
async def test_setup_starts_a_controller_per_device(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    platforms = await async_setup_control(hass, entry, coordinator)

    assert platforms == [Platform.BUTTON]
    feature = hass.data[DOMAIN][entry.entry_id][DATA_CONTROL]
    assert isinstance(feature, ControlFeature)
    assert list(feature.devices) == [MAC]
    assert feature.devices[MAC].mode == "shadow"

    await async_unload_control(hass, entry)
    assert DATA_CONTROL not in hass.data[DOMAIN][entry.entry_id]
    assert feature.devices == {}


@pytest.mark.asyncio
async def test_unload_without_a_feature_is_a_no_op(hass: HomeAssistant, entry):
    await async_unload_control(hass, entry)


@pytest.mark.asyncio
async def test_remove_deletes_entities_and_storage(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    entity_registry: er.EntityRegistry,
):
    control_entity = entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_control_ack", config_entry=entry
    )
    other_entity = entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_tank_upper_temperature", config_entry=entry
    )
    await async_setup_control(hass, entry, coordinator)
    feature: ControlFeature = hass.data[DOMAIN][entry.entry_id][DATA_CONTROL]
    await feature.store.async_set_intent(MAC, {"intent_id": "i"}, "t")
    assert storage_key(entry.entry_id) in hass_storage

    await feature.async_remove()

    assert entity_registry.async_get(control_entity.entity_id) is None
    assert entity_registry.async_get(other_entity.entity_id) is not None
    assert storage_key(entry.entry_id) not in hass_storage
    assert feature.devices == {}


@pytest.mark.asyncio
async def test_start_removes_entities_of_an_earlier_version(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    entity_registry: er.EntityRegistry,
):
    stale = entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_control_last_restore", config_entry=entry
    )
    current = entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_control_ack", config_entry=entry
    )
    await async_setup_control(hass, entry, coordinator)

    assert entity_registry.async_get(stale.entity_id) is None
    assert entity_registry.async_get(current.entity_id) is not None
    await async_unload_control(hass, entry)


@pytest.mark.asyncio
async def test_remove_keeps_storage_if_a_heater_was_not_handed_back(
    hass: HomeAssistant, hass_storage, entry, coordinator
):
    await async_setup_control(hass, entry, coordinator)
    feature: ControlFeature = hass.data[DOMAIN][entry.entry_id][DATA_CONTROL]
    await feature.store.async_set_intent(MAC, {"intent_id": "i"}, "t")
    control = feature.devices[MAC]
    control.async_release = AsyncMock(return_value=False)  # type: ignore[method-assign]

    await feature.async_remove()

    control.async_release.assert_awaited_once()
    assert storage_key(entry.entry_id) in hass_storage
    assert feature.devices == {}
