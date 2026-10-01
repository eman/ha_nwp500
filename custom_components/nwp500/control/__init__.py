"""External control of the heater by an intent entity (issue #158).

The feature is optional and additive. This package is imported only when
the option is on; `control_enabled` in the shared constants is the whole of
the check the integration makes while it is off.

The specification is issue #158, kept in `docs/external-control-spec.md`.
Delivery steps 2 (the skeleton), 3 (shadow programming) and 5 (live list
writes) are here. Shadow, the default, reads the device, plans the
reservation list it would program, and reports it, writing nothing. Live
writes the list; `CONTROL_LIVE_AVAILABLE` is its kill switch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import attr
from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from ..const import (
    DATA_CONTROL,
    DATA_PLATFORMS,
    DOMAIN,
)
from .device import DeviceControl
from .entity import control_entity_id
from .store import ControlStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator import NWP500ConfigEntry, NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Platforms the feature adds to the entry. The sensor platform is shared
# with the rest of the integration, which asks the feature for its sensors
# when it is running.
CONTROL_PLATFORMS: tuple[Platform, ...] = (Platform.BUTTON,)

# Unique ids of the feature's entities carry this marker after the MAC.
UNIQUE_ID_MARKER = "_control_"

# The key of every entity the feature creates. An entity whose key is not
# here belongs to an earlier version of the feature and is removed.
ENTITY_KEYS = frozenset(
    {
        "capabilities",
        "intent",
        "ack",
        "program_hash",
        "programmed_until",
        "next_entry",
        "wanted_mode",
        "wanted_setpoint",
        "last_write",
        "heartbeat",
        "in_sync",
        "grant_raised",
        "override",
        "disable",
    }
)


# The English names the entities had before their ids were fixed to the
# documented ones (spec section 4). An entity whose id is still the default
# Home Assistant made from one of these is moved to its documented id once;
# an id someone chose is left alone.
_EARLIER_NAMES: dict[str, str] = {
    "capabilities": "Control Capabilities",
    "intent": "Control Plan",
    "ack": "Control Acknowledgement",
    "heartbeat": "Control Heartbeat",
    "wanted_mode": "Control Wanted Mode",
    "wanted_setpoint": "Control Wanted Setpoint",
    "program_hash": "Control Program Hash",
    "programmed_until": "Control Programmed Until",
    "next_entry": "Control Next Entry",
    "last_write": "Control Last Write",
    "override": "Control Override",
    "in_sync": "Control In Sync",
    "grant_raised": "Control Surplus Raise",
    "disable": "Disable External Control",
}


class ControlFeature:
    """The external control feature for one config entry.

    One `DeviceControl` per heater the entry knows. They share the intent
    entity and the storage file.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NWP500ConfigEntry,
        coordinator: NWP500DataUpdateCoordinator,
    ) -> None:
        """Build the feature; nothing runs until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.store = ControlStore(hass, entry.entry_id)
        self.devices: dict[str, DeviceControl] = {}

    async def async_start(self) -> None:
        """Load the stored state and start a controller per device."""
        self._remove_entities(stale_only=True)
        self._move_to_documented_ids()
        await self.store.async_load()
        try:
            for mac_address, device_data in self.coordinator.data.items():
                control = DeviceControl(
                    self.hass,
                    self.entry,
                    self.coordinator,
                    mac_address,
                    device_data["device"],
                    self.store,
                )
                self.devices[mac_address] = control
                await control.async_start()
        except Exception:
            # Nothing would be left to stop the controllers already started:
            # stop them before the failure goes up.
            await self.async_stop()
            raise

    async def async_stop(self) -> None:
        """Stop listeners and timers. Writes nothing (spec section 5.4)."""
        for control in self.devices.values():
            await control.async_stop()
        self.devices.clear()

    async def async_remove(self) -> None:
        """Turn the feature off for good: entities and stored data go.

        Called when the option is switched off, before the entry reloads
        without the feature (spec section 1.1.6). Nothing is written to the
        heater: what the plans put there stays, and is the scheduler's.
        """
        await self.async_stop()
        self._remove_entities(stale_only=False)
        await self.store.async_remove()

    def _move_to_documented_ids(self) -> None:
        """Give entities the documented ids they lacked before (section 4).

        An id is moved only if it is still a default Home Assistant made
        from an earlier name: its name part is that name's, whatever the
        device was called then. It moves to an id that is free. The
        registry's records of removed entities are moved too, since it
        gives a removed entity its old id back when it comes again.
        """
        registry = er.async_get(self.hass)
        for entity_entry in er.async_entries_for_config_entry(
            registry, self.entry.entry_id
        ):
            if entity_entry.platform != DOMAIN:
                continue
            documented = self._documented_id(
                entity_entry.domain,
                entity_entry.unique_id,
                entity_entry.entity_id,
            )
            if documented is None or registry.async_get(documented) is not None:
                continue
            registry.async_update_entity(
                entity_entry.entity_id, new_entity_id=documented
            )
            _LOGGER.info(
                "External control: %s is now %s, its documented id",
                entity_entry.entity_id,
                documented,
            )
        moved = False
        for key, deleted in list(registry.deleted_entities.items()):
            if (
                deleted.platform != DOMAIN
                or deleted.config_entry_id != self.entry.entry_id
            ):
                continue
            documented = self._documented_id(
                deleted.domain, deleted.unique_id, deleted.entity_id
            )
            if documented is None or registry.async_get(documented) is not None:
                continue
            registry.deleted_entities[key] = attr.evolve(
                deleted, entity_id=documented
            )
            moved = True
        if moved:
            registry.async_schedule_save()

    def _documented_id(
        self, domain: str, unique_id: str, entity_id: str
    ) -> str | None:
        """The documented id to move an earlier default id to, if any."""
        if UNIQUE_ID_MARKER not in unique_id:
            return None
        mac_address, key = unique_id.split(UNIQUE_ID_MARKER, 1)
        earlier = _EARLIER_NAMES.get(key)
        if earlier is None:
            return None
        object_id = entity_id.split(".", 1)[1]
        name_part = slugify(earlier)
        if object_id != name_part and not object_id.endswith(f"_{name_part}"):
            # Chosen by someone, or already the documented id.
            return None
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, mac_address)}
        )
        if device is None:
            return None
        documented = control_entity_id(
            domain, device.name_by_user or device.name or "", key
        )
        return None if documented == entity_id else documented

    def _remove_entities(self, *, stale_only: bool) -> None:
        """Remove the feature's entities: all of them, or only stale ones."""
        registry = er.async_get(self.hass)
        for entity_entry in er.async_entries_for_config_entry(
            registry, self.entry.entry_id
        ):
            if (
                entity_entry.platform != DOMAIN
                or UNIQUE_ID_MARKER not in entity_entry.unique_id
            ):
                continue
            key = entity_entry.unique_id.split(UNIQUE_ID_MARKER, 1)[1]
            if stale_only and key in ENTITY_KEYS:
                continue
            registry.async_remove(entity_entry.entity_id)


async def async_setup_control(
    hass: HomeAssistant,
    entry: NWP500ConfigEntry,
    coordinator: NWP500DataUpdateCoordinator,
) -> list[Platform]:
    """Start the feature and return the platforms it needs forwarded.

    The set-up hook. Registers the feature in `hass.data` so the entity
    platforms can find it without importing this package themselves.
    """
    feature = ControlFeature(hass, entry, coordinator)
    await feature.async_start()
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})[
        DATA_CONTROL
    ] = feature
    _LOGGER.info(
        "External control is on in %s mode for %d device(s)",
        feature.devices[next(iter(feature.devices))].mode
        if feature.devices
        else "no",
        len(feature.devices),
    )
    return list(CONTROL_PLATFORMS)


async def async_unload_control(
    hass: HomeAssistant, entry: NWP500ConfigEntry
) -> None:
    """Stop the feature on entry unload."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    feature: ControlFeature | None = entry_data.pop(DATA_CONTROL, None)
    if feature is not None:
        await feature.async_stop()


__all__ = [
    "CONTROL_PLATFORMS",
    "DATA_PLATFORMS",
    "ControlFeature",
    "async_setup_control",
    "async_unload_control",
]
