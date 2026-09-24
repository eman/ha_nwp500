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

    await store.async_set_intent(
        MAC, {"intent_id": "i-1"}, "2026-10-04T05:00:00+00:00"
    )

    assert store.stored_intent(MAC) == {
        "document": {"intent_id": "i-1"},
        "received_at": "2026-10-04T05:00:00+00:00",
    }
    assert hass_storage[storage_key("entry1")]["data"]["devices"][MAC][
        "intent"
    ]["document"] == {"intent_id": "i-1"}

    again = ControlStore(hass, "entry1")
    await again.async_load()
    assert again.stored_intent(MAC) == store.stored_intent(MAC)


@pytest.mark.asyncio
async def test_clear_and_remove(hass: HomeAssistant, hass_storage: dict):
    store = ControlStore(hass, "entry1")
    await store.async_load()
    await store.async_set_intent(MAC, {"intent_id": "i-1"}, "t")

    await store.async_clear_intent(MAC)
    assert store.stored_intent(MAC) is None
    assert (
        "intent"
        not in hass_storage[storage_key("entry1")]["data"]["devices"][MAC]
    )

    # Clearing again is a no-op rather than a write.
    await store.async_clear_intent(MAC)

    await store.async_remove()
    assert storage_key("entry1") not in hass_storage


@pytest.mark.asyncio
async def test_stored_intent_is_a_copy(hass: HomeAssistant, hass_storage: dict):
    store = ControlStore(hass, "entry1")
    await store.async_load()
    await store.async_set_intent(MAC, {"intent_id": "i-1"}, "t")
    record = store.stored_intent(MAC)
    assert record is not None
    record["received_at"] = "changed"
    assert store.stored_intent(MAC)["received_at"] == "t"
