"""Switch platform for Navien NWP500 integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, get_enum_value
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities: list[SwitchEntity] = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]

        # Add power switch
        entities.append(NWP500PowerSwitch(coordinator, mac_address, device))

        # Add TOU override switch
        entities.append(NWP500TOUOverrideSwitch(coordinator, mac_address, device))

    async_add_entities(entities, True)


class NWP500PowerSwitch(NWP500Entity, SwitchEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 power switch."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Any,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_power"
        self._attr_name = f"{self.device_name} Power"
        self._attr_icon = "mdi:power"

    @property
    def is_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return True if switch is on."""
        if not (status := self._status):
            return None
        try:
            dhw_operation_setting = getattr(
                status, "dhw_operation_setting", None
            )
            if dhw_operation_setting is not None:
                dhw_value = get_enum_value(dhw_operation_setting)
                return bool(dhw_value != 6)
            operation_mode = getattr(status, "operation_mode", None)
            if operation_mode is not None:
                return True
        except (AttributeError, TypeError):
            pass
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_power", power_on=True
        )

        if success:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_power", power_on=False
        )

        if success:
            await self.coordinator.async_request_refresh()


class NWP500TOUOverrideSwitch(NWP500Entity, SwitchEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 TOU override switch."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Any,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_tou_override"
        self._attr_name = f"{self.device_name} TOU Override"
        self._attr_icon = "mdi:clock-time-four-outline"

    @property
    def is_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return True if TOU override is enabled."""
        if not (status := self._status):
            return None
        try:
            return getattr(status, "tou_override_status", None)
        except (AttributeError, TypeError):
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable TOU override."""
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_tou_enabled", enabled=True
        )

        if success:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable TOU override."""
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_tou_enabled", enabled=False
        )

        if success:
            await self.coordinator.async_request_refresh()
