"""Water heater platform for Navien NWP500 integration."""
from __future__ import annotations

import logging
from typing import Any, List

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_OFF,
    STATE_ON,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_TEMPERATURE, MIN_TEMPERATURE
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up water heater entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    entities = []
    for mac_address, device_data in coordinator.data.items():
        entities.append(NWP500WaterHeater(coordinator, mac_address, device_data["device"]))
    
    async_add_entities(entities, True)


class NWP500WaterHeater(NWP500Entity, WaterHeaterEntity):
    """Navien NWP500 water heater entity."""

    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_min_temp = MIN_TEMPERATURE
    _attr_max_temp = MAX_TEMPERATURE
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device,
    ) -> None:
        """Initialize the water heater."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_water_heater"
        self._attr_name = f"{self.device_name} Water Heater"

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        # Get average tank temperature from upper and lower sensors
        try:
            upper_temp = getattr(status, 'tankUpperTemperature', None)
            lower_temp = getattr(status, 'tankLowerTemperature', None)
            
            if upper_temp is not None and lower_temp is not None:
                return (upper_temp + lower_temp) / 2
            elif upper_temp is not None:
                return upper_temp
            elif lower_temp is not None:
                return lower_temp
        except (AttributeError, TypeError):
            pass
            
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        # Get target DHW temperature from status
        try:
            target_temp = getattr(status, 'dhwTargetTemperatureSetting', None)
            if target_temp is None:
                target_temp = getattr(status, 'dhwTemperatureSetting', None)
            return target_temp
        except (AttributeError, TypeError):
            return None

    @property
    def current_operation(self) -> str | None:
        """Return current operation mode."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        try:
            operation_mode = getattr(status, 'operationMode', None)
            if operation_mode is not None:
                # Convert enum to string if it's an enum
                if hasattr(operation_mode, 'name'):
                    return operation_mode.name
                elif hasattr(operation_mode, 'value'):
                    return str(operation_mode.value)
                else:
                    return str(operation_mode)
        except (AttributeError, TypeError):
            pass
        
        return None

    @property
    def operation_list(self) -> List[str]:
        """Return the list of available operation modes."""
        # Based on the nwp500-python DeviceStatus.operationMode enum
        return ["heat_pump", "energy_saver", "high_demand", "electric", "vacation"]

    @property
    def is_on(self) -> bool | None:
        """Return True if the water heater is on."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        try:
            # Check if DHW (Domestic Hot Water) is being used/heated
            dhw_use = getattr(status, 'dhwUse', None)
            if dhw_use is not None:
                return dhw_use
            
            # Fallback: check if any heating elements or compressor is running
            comp_use = getattr(status, 'compUse', None)
            heat_upper = getattr(status, 'heatUpperUse', None)
            heat_lower = getattr(status, 'heatLowerUse', None)
            
            if any([comp_use, heat_upper, heat_lower]):
                return True
            
            # Fallback to operation mode - device is "on" if in any active mode
            operation_mode = getattr(status, 'operationMode', None)
            if operation_mode is not None:
                # Convert to string or number for comparison
                if hasattr(operation_mode, 'value'):
                    return operation_mode.value != 0
                else:
                    return str(operation_mode).lower() not in ['off', 'none', '0']
        
        except (AttributeError, TypeError):
            pass
            
        return None

    @property
    def state(self) -> str:
        """Return the current state."""
        if self.is_on is None:
            return "unknown"
        return STATE_ON if self.is_on else STATE_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = super().extra_state_attributes
        
        if not self.device_data:
            return attrs
        
        status = self.device_data.get("status")
        if not status:
            return attrs
        
        # Add useful status information
        try:
            attrs.update({
                "outside_temperature": getattr(status, 'outsideTemperature', None),
                "operation_busy": getattr(status, 'operationBusy', None),
                "error_code": getattr(status, 'errorCode', None),
                "sub_error_code": getattr(status, 'subErrorCode', None),
                "discharge_temperature": getattr(status, 'dischargeTemperature', None),
                "suction_temperature": getattr(status, 'suctionTemperature', None),
                "freeze_protection_active": getattr(status, 'freezeProtectionUse', None),
                "current_power": getattr(status, 'currentInstPower', None),
                "dhw_charge_percentage": getattr(status, 'dhwChargePer', None),
                "wifi_rssi": getattr(status, 'wifiRssi', None),
                "compressor_running": getattr(status, 'compUse', None),
                "upper_element_on": getattr(status, 'heatUpperUse', None),
                "lower_element_on": getattr(status, 'heatLowerUse', None),
            })
        except (AttributeError, TypeError):
            pass
        
        return attrs

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        
        # Validate temperature range
        if not (self.min_temp <= temperature <= self.max_temp):
            _LOGGER.error(
                "Temperature %s out of range (%s-%s)",
                temperature,
                self.min_temp,
                self.max_temp,
            )
            return
        
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_temperature", temperature=int(temperature)
        )
        
        if success:
            # Trigger immediate data refresh
            await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set new operation mode."""
        # Find the numeric mode value
        mode_value = None
        for value, mode_name in OPERATION_MODES.items():
            if mode_name == operation_mode:
                mode_value = value
                break
        
        if mode_value is None:
            _LOGGER.error("Invalid operation mode: %s", operation_mode)
            return
        
        # Note: The nwp500-python library doesn't explicitly document 
        # operation mode control commands in the MQTT documentation.
        # This may require using DHW mode control or other available commands.
        # For now, we'll log this limitation.
        _LOGGER.warning(
            "Operation mode control not fully implemented in nwp500-python library. "
            "Requested mode: %s (value: %d)", operation_mode, mode_value
        )
        
        # If trying to turn off (not a valid mode in NWP500), turn off power instead
        if operation_mode.lower() in ["off", "none"]:
            await self.async_turn_off()
            return
        
        # For other modes, ensure device is on
        await self.async_turn_on()

    async def async_turn_on(self) -> None:
        """Turn the water heater on."""
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_power", power_on=True
        )
        
        if success:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn the water heater off."""
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_power", power_on=False
        )
        
        if success:
            await self.coordinator.async_request_refresh()