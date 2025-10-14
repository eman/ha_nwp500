"""Switch platform for Navien NWP500 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    entities = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        
        # Add power switch
        entities.append(NWP500PowerSwitch(coordinator, mac_address, device))
        
        # Add freeze protection switch if available
        entities.append(NWP500FreezeProtectionSwitch(coordinator, mac_address, device))
    
    async_add_entities(entities, True)


class NWP500PowerSwitch(NWP500Entity, SwitchEntity):
    """Navien NWP500 power switch."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_power"
        self._attr_name = f"{self.device_name} Power"
        self._attr_icon = "mdi:power"

    @property
    def is_on(self) -> bool | None:
        """Return True if switch is on."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        try:
            # According to the documentation, check dhwOperationSetting for power state
            # dhwOperationSetting == 6 means POWER_OFF (device is powered off)
            # Any other value means the device is powered on (even if idle/standby)
            dhw_operation_setting = getattr(status, 'dhwOperationSetting', None)
            if dhw_operation_setting is not None:
                # Handle both enum and integer values
                dhw_value = dhw_operation_setting.value if hasattr(dhw_operation_setting, 'value') else dhw_operation_setting
                return dhw_value != 6  # 6 = POWER_OFF
            
            # Fallback: if dhwOperationSetting is not available, use operationMode
            # operationMode == 0 means STANDBY (device is on but idle)
            # Only truly "off" if we can't determine the state
            operation_mode = getattr(status, 'operationMode', None)
            if operation_mode is not None:
                # Device is "on" if it has any operation mode value (including 0 for standby)
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


class NWP500FreezeProtectionSwitch(NWP500Entity, SwitchEntity):
    """Navien NWP500 freeze protection switch."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_freeze_protection"
        self._attr_name = f"{self.device_name} Freeze Protection"
        self._attr_icon = "mdi:snowflake"

    @property
    def is_on(self) -> bool | None:
        """Return True if freeze protection is enabled."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        try:
            freeze_protection = getattr(status, 'freezeProtectionUse', None)
            return freeze_protection if freeze_protection is not None else None
        except (AttributeError, TypeError):
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Only show if freeze protection status is available
        if not super().available:
            return False
        
        status = self.device_data.get("status")
        return status is not None and hasattr(status, 'freezeProtectionUse')

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn freeze protection on."""
        _LOGGER.warning("Freeze protection control not implemented - read-only status")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn freeze protection off."""
        _LOGGER.warning("Freeze protection control not implemented - read-only status")