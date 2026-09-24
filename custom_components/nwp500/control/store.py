"""Persistent state of the feature, one file per config entry.

Per device: the last accepted plan, so a restart with the intent source
unavailable does not lose it (spec section 2.1); the planner's state, which
includes the entries the feature owns; the owner's program; and whether the
disabling clean-up has run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from ..const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

STORAGE_VERSION = 1


def storage_key(entry_id: str) -> str:
    """The storage key for an entry's control state."""
    return f"{DOMAIN}.control.{entry_id}"


class ControlStore:
    """Typed access to the feature's storage file."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Bind to the entry's file; nothing is read until `async_load`."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry_id)
        )
        self._data: dict[str, Any] = {"devices": {}}

    async def async_load(self) -> None:
        """Read the file, if there is one."""
        loaded = await self._store.async_load()
        if loaded:
            self._data = loaded
            self._data.setdefault("devices", {})

    def _device(self, mac_address: str) -> dict[str, Any]:
        devices: dict[str, dict[str, Any]] = self._data["devices"]
        return devices.setdefault(mac_address, {})

    def stored_intent(self, mac_address: str) -> dict[str, Any] | None:
        """The stored document and its receipt time, or None."""
        record = self._device(mac_address).get("intent")
        return dict(record) if record else None

    async def async_set_intent(
        self, mac_address: str, document: dict[str, Any], received_at: str
    ) -> None:
        """Remember the last accepted document."""
        self._device(mac_address)["intent"] = {
            "document": document,
            "received_at": received_at,
        }
        await self._store.async_save(self._data)

    async def async_clear_intent(self, mac_address: str) -> None:
        """Forget the stored document, once it is stale or withdrawn."""
        if self._device(mac_address).pop("intent", None) is not None:
            await self._store.async_save(self._data)

    def stored_engine(self, mac_address: str) -> dict[str, Any] | None:
        """The engine state kept across a restart, or None."""
        record = self._device(mac_address).get("engine")
        return dict(record) if record else None

    async def async_set_engine(
        self, mac_address: str, document: dict[str, Any]
    ) -> None:
        """Remember the engine state, if it changed."""
        if self._device(mac_address).get("engine") == document:
            return
        self._device(mac_address)["engine"] = document
        await self._store.async_save(self._data)

    def stored_owner(self, mac_address: str) -> dict[str, Any] | None:
        """The owner's program kept across a restart, or None."""
        record = self._device(mac_address).get("owner")
        return dict(record) if record else None

    async def async_set_owner(
        self, mac_address: str, document: dict[str, Any]
    ) -> None:
        """Remember the owner's program."""
        self._device(mac_address)["owner"] = document
        await self._store.async_save(self._data)

    def disabled_done(self, mac_address: str) -> bool:
        """Whether the disabling clean-up has run since `disabled` began."""
        return bool(self._device(mac_address).get("disabled_done", False))

    async def async_set_disabled_done(
        self, mac_address: str, done: bool
    ) -> None:
        """Record that the disabling clean-up has run, or reset it."""
        if self.disabled_done(mac_address) == done:
            return
        self._device(mac_address)["disabled_done"] = done
        await self._store.async_save(self._data)

    async def async_remove(self) -> None:
        """Delete the file (the feature was switched off)."""
        self._data = {"devices": {}}
        await self._store.async_remove()
