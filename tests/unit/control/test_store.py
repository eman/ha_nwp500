"""The feature's storage file."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nwp500.control.store import ControlStore, storage_key

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.mark.asyncio
async def test_round_trip(hass: HomeAssistant, hass_storage: dict):
    store = ControlStore(hass, "entry1")
    await store.async_load()
    assert store.stored_intent(MAC) is None
    assert store.stored_engine(MAC) is None
    assert store.stored_owner(MAC) is None
    assert store.disabled_done(MAC) is False

    await store.async_set_intent(MAC, {"intent_id": "i-1"}, "t")
    await store.async_set_engine(MAC, {"owned": []})
    await store.async_set_owner(MAC, {"mode": "energy_saver"})
    await store.async_set_disabled_done(MAC, True)

    again = ControlStore(hass, "entry1")
    await again.async_load()
    assert again.stored_intent(MAC) == {
        "document": {"intent_id": "i-1"},
        "received_at": "t",
    }
    assert again.stored_engine(MAC) == {"owned": []}
    assert again.stored_owner(MAC) == {"mode": "energy_saver"}
    assert again.disabled_done(MAC) is True
    assert hass_storage[storage_key("entry1")]["data"]["devices"][MAC]["owner"]


@pytest.mark.asyncio
async def test_clear_and_remove(hass: HomeAssistant, hass_storage: dict):
    store = ControlStore(hass, "entry1")
    await store.async_load()
    await store.async_set_intent(MAC, {"intent_id": "i-1"}, "t")
    await store.async_clear_intent(MAC)
    assert store.stored_intent(MAC) is None
    await store.async_clear_intent(MAC)
    await store.async_remove()
    assert storage_key("entry1") not in hass_storage


@pytest.mark.asyncio
async def test_unchanged_writes_are_skipped(
    hass: HomeAssistant, hass_storage: dict
):
    store = ControlStore(hass, "entry1")
    await store.async_load()
    await store.async_set_engine(MAC, {"owned": []})
    await store.async_set_disabled_done(MAC, False)
    hass_storage.clear()
    await store.async_set_engine(MAC, {"owned": []})
    await store.async_set_disabled_done(MAC, False)
    assert storage_key("entry1") not in hass_storage


@pytest.mark.asyncio
async def test_records_are_copies(hass: HomeAssistant, hass_storage: dict):
    store = ControlStore(hass, "entry1")
    await store.async_load()
    await store.async_set_intent(MAC, {"intent_id": "i-1"}, "t")
    record = store.stored_intent(MAC)
    record["received_at"] = "changed"
    assert store.stored_intent(MAC)["received_at"] == "t"
