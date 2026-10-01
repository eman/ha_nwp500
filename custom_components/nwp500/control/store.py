"""Persistent state of the feature, one file per config entry.

Per device: the last accepted plan, so a restart with the intent source
unavailable does not lose it (spec section 2.1); the planner's state, which
includes the entries the feature owns; what became of each device command,
so a restart does not apply one again; and whether the feature has written
the heater's list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from ..const import control_storage_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

STORAGE_VERSION = 1


def storage_key(entry_id: str) -> str:
    """The storage key for an entry's control state."""
    return control_storage_key(entry_id)


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

    def stored_commands(self, mac_address: str) -> dict[str, Any]:
        """What became of each device command, kept across a restart."""
        return dict(self._device(mac_address).get("commands") or {})

    async def async_set_commands(
        self, mac_address: str, document: dict[str, Any]
    ) -> None:
        """Remember what became of each device command, if it changed."""
        if self._device(mac_address).get("commands", {}) == document:
            return
        self._device(mac_address)["commands"] = document
        await self._store.async_save(self._data)

    def took_over(self, mac_address: str) -> bool:
        """Whether the feature has taken over the heater's reservation list.

        True from the first confirmed live write until disabling restores
        the owner's program (spec sections 5.1 and 6.6).
        """
        return bool(self._device(mac_address).get("took_over", False))

    async def async_set_took_over(self, mac_address: str, value: bool) -> None:
        """Record whether the feature holds the heater's reservation list."""
        if self.took_over(mac_address) == value:
            return
        self._device(mac_address)["took_over"] = value
        await self._store.async_save(self._data)

    async def async_remove(self) -> None:
        """Delete the file (the feature was switched off)."""
        self._data = {"devices": {}}
        await self._store.async_remove()
