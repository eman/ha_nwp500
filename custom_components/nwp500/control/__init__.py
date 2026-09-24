"""External control of the heater by an intent entity (issue #158).

The feature is optional and additive. This package is imported only when
the option is on; `control_enabled` in the shared constants is the whole of
the check the integration makes while it is off.

Delivery step 2 (the skeleton): intake, validation and the stored intent,
the capability entity, the heartbeat, and unload without writes. Shadow
execution and live writes follow in later steps.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er

from ..const import DATA_CONTROL, DATA_PLATFORMS, DOMAIN
from .device import DeviceControl
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
        await self.store.async_load()
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

    async def async_stop(self) -> None:
        """Stop listeners and timers. Writes nothing (spec section 5.4)."""
        for control in self.devices.values():
            await control.async_stop()
        self.devices.clear()

    async def async_remove(self) -> None:
        """Turn the feature off for good: entities and stored data go.

        Called when the option is switched off, before the entry reloads
        without the feature (spec section 1.1.6). The restore that precedes
        the deletion arrives with shadow execution.
        """
        await self.async_stop()
        registry = er.async_get(self.hass)
        for entity_entry in er.async_entries_for_config_entry(
            registry, self.entry.entry_id
        ):
            if (
                entity_entry.platform == DOMAIN
                and UNIQUE_ID_MARKER in entity_entry.unique_id
            ):
                registry.async_remove(entity_entry.entity_id)
        await self.store.async_remove()


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
