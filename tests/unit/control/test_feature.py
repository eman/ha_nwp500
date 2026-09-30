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
    coordinator.reservation_schedules_read_at = {}
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


def test_earlier_default_ids_move_to_the_documented_ones(
    hass: HomeAssistant, entry, coordinator
):
    """Entity ids made from the earlier names become spec section 4's.

    An id someone chose is left alone, and so is one whose target is taken.
    """
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MAC)},
        name="NWP500",
    )
    registry = er.async_get(hass)

    def register(domain: str, key: str, object_id: str):
        return registry.async_get_or_create(
            domain,
            DOMAIN,
            f"{MAC}_control_{key}",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=object_id,
        )

    ack = register("sensor", "ack", "nwp500_control_acknowledgement")
    chosen = register("sensor", "heartbeat", "my_heartbeat")
    disable = register("button", "disable", "nwp500_disable_external_control")
    registry.async_get_or_create(
        "binary_sensor",
        "other",
        "x",
        suggested_object_id="nwp500_control_in_sync",
    )
    in_sync = register("binary_sensor", "in_sync", "nwp500_control_in_sync_2")

    ControlFeature(hass, entry, coordinator)._move_to_documented_ids()

    assert registry.async_get(ack.entity_id) is None
    assert registry.async_get("sensor.nwp500_control_ack") is not None
    assert registry.async_get("button.nwp500_control_disable") is not None
    assert registry.async_get(disable.entity_id) is None
    assert registry.async_get(chosen.entity_id) is not None
    # Not the default made from the earlier name: left alone.
    assert registry.async_get(in_sync.entity_id) is not None
