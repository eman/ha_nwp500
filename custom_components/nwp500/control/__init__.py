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

from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_DISABLED,
    DATA_CONTROL,
    DATA_PLATFORMS,
    DOMAIN,
)
from .device import DeviceControl, live_writes
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
        without the feature (spec section 1.1.6). A heater that holds the
        feature's list is handed back first, as disabling does (section
        6.6). If that fails, the stored state is kept, so switching the
        feature on again and disabling can finish the job.
        """
        released = True
        for control in self.devices.values():
            if not await control.async_release(dt_util.utcnow()):
                released = False
        await self.async_stop()
        self._remove_entities(stale_only=False)
        if released:
            await self.store.async_remove()
            return
        _LOGGER.error(
            "External control was switched off, but the owner's reservation "
            "list could not be restored on every heater. Its state is kept: "
            "switch the feature on and press Disable to finish"
        )

    async def async_options_changed(self) -> None:
        """Before the entry reloads with new options: leave live cleanly.

        A heater that holds the feature's list, under options that no
        longer write it (shadow, or segments no longer live), is handed back
        first, as disabling does. Otherwise shadow would plan as if nothing
        were on the heater while the feature's entries kept firing. Going
        to `disabled` is left to the disabled start, which records it.
        """
        options = self.entry.options
        if options.get(CONF_CONTROL_MODE) == CONTROL_MODE_DISABLED:
            return
        for mac_address, control in self.devices.items():
            if not control.holds_device or live_writes(options, mac_address):
                continue
            if not await control.async_release(dt_util.utcnow()):
                _LOGGER.error(
                    "Leaving live on %s: the owner's reservation list could "
                    "not be restored. Press Disable to hand the heater back",
                    mac_address,
                )

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
