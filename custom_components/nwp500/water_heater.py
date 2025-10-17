"""Water heater platform for Navien NWP500 integration."""
from __future__ import annotations

import logging
from typing import Any, List

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    STATE_ELECTRIC,
)
from homeassistant.const import STATE_OFF
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MAX_TEMPERATURE,
    MIN_TEMPERATURE,
    DHW_OPERATION_SETTING_TO_HA,
    HA_TO_DHW_MODE,
    CURRENT_OPERATION_MODE_TO_HA,
)
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
        | WaterHeaterEntityFeature.AWAY_MODE
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
        """Return current operation mode based on dhwOperationSetting."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        try:
            # Use dhwOperationSetting as the primary source for water heater state
            operation_setting = getattr(status, 'dhwOperationSetting', None)
            if operation_setting is not None:
                # Convert enum to value if it's an enum
                if hasattr(operation_setting, 'value'):
                    mode_value = operation_setting.value
                else:
                    mode_value = operation_setting
                
                # Handle vacation mode (5) - it's managed by away_mode, not operation_mode
                # When in vacation mode, return eco as the underlying operational state
                # The vacation state is properly exposed via is_away_mode_on property
                if mode_value == 5:  # VACATION mode
                    return STATE_ECO
                
                # Handle power off mode (6) - return explicit "off" state 
                # for better UI clarity and semantic consistency with ON_OFF feature
                if mode_value == 6:  # POWER_OFF mode  
                    return STATE_OFF
                
                # Map normal operation modes to Home Assistant states
                return DHW_OPERATION_SETTING_TO_HA.get(mode_value, "unknown")
                
        except (AttributeError, TypeError):
            pass
        
        return "unknown"

    @property
    def operation_list(self) -> List[str]:
        """Return the list of available operation modes.
        
        Note: This excludes vacation and power-off modes because:
        - Vacation mode is handled by the AWAY_MODE feature
        - Power-off mode is handled by the ON_OFF feature
        
        This follows Home Assistant's design where operation modes represent
        active heating modes, while special states are handled by dedicated features.
        """
        return [STATE_ECO, STATE_HEAT_PUMP, STATE_HIGH_DEMAND, STATE_ELECTRIC]

    @property
    def is_on(self) -> bool | None:
        """Return True if the water heater is on."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        try:
            # Check dhwOperationSetting to see if device is set to a valid operating mode
            operation_setting = getattr(status, 'dhwOperationSetting', None)
            if operation_setting is not None:
                if hasattr(operation_setting, 'value'):
                    mode_value = operation_setting.value
                else:
                    mode_value = operation_setting
                
                # Device is "on" if not in POWER_OFF (6) or STANDBY (0) modes
                return mode_value not in [0, 6]
            
            # Fallback: check if any heating elements or compressor is running
            dhw_use = getattr(status, 'dhwUse', None)
            comp_use = getattr(status, 'compUse', None)
            heat_upper = getattr(status, 'heatUpperUse', None)
            heat_lower = getattr(status, 'heatLowerUse', None)
            
            if any([dhw_use, comp_use, heat_upper, heat_lower]):
                return True
                
            # Final fallback to operationMode
            operation_mode = getattr(status, 'operationMode', None)
            if operation_mode is not None:
                if hasattr(operation_mode, 'value'):
                    return operation_mode.value not in [0, 6]  # Not STANDBY or POWER_OFF
                else:
                    return operation_mode not in [0, 6]
        
        except (AttributeError, TypeError):
            pass
            
        return None

    @property
    def is_away_mode_on(self) -> bool | None:
        """Return true if away mode (vacation mode) is on."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        try:
            operation_setting = getattr(status, 'dhwOperationSetting', None)
            if operation_setting is not None:
                if hasattr(operation_setting, 'value'):
                    return operation_setting.value == 5  # VACATION mode
                else:
                    return operation_setting == 5
        except (AttributeError, TypeError):
            pass
            
        return False

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
            # Get the operation modes for display
            operation_mode = getattr(status, 'operationMode', None)
            dhw_operation_setting = getattr(status, 'dhwOperationSetting', None)
            
            # Convert enum operation modes to friendly names
            current_operation_name = "unknown"
            dhw_setting_name = "unknown"
            
            # Use CURRENT_OPERATION_MODE_TO_HA for operationMode (current actual state)
            if operation_mode is not None:
                if hasattr(operation_mode, 'value'):
                    current_operation_name = CURRENT_OPERATION_MODE_TO_HA.get(
                        operation_mode.value, f"mode_{operation_mode.value}"
                    )
            
            # Use DHW_OPERATION_SETTING_TO_HA for dhwOperationSetting (user configured mode)
            if dhw_operation_setting is not None:
                if hasattr(dhw_operation_setting, 'value'):
                    dhw_setting_name = DHW_OPERATION_SETTING_TO_HA.get(
                        dhw_operation_setting.value, 
                        f"mode_{dhw_operation_setting.value}"
                    )
                else:
                    dhw_setting_name = DHW_OPERATION_SETTING_TO_HA.get(
                        dhw_operation_setting, f"mode_{dhw_operation_setting}"
                    )
            
            attrs.update({
                # User-friendly operation mode display
                "dhw_mode_setting": dhw_setting_name,  # What user has configured
                "current_operation_state": current_operation_name,  # What device is doing
                "mode_description": (
                    f"Set: {dhw_setting_name.replace('_', ' ').title()}, "
                    f"Running: {current_operation_name.replace('_', ' ').title()}"
                ),
                
                # Temperature and status info
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
                
                # Component status
                "compressor_running": getattr(status, 'compUse', None),
                "upper_element_on": getattr(status, 'heatUpperUse', None),
                "lower_element_on": getattr(status, 'heatLowerUse', None),
                
                # Raw values for diagnostics (keep existing)
                "operation_mode_raw": operation_mode,
                "dhw_operation_setting_raw": dhw_operation_setting,
            })
        except (AttributeError, TypeError):
            pass
        
        return attrs

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature.
        
        Uses set_dhw_temperature_display() which takes the display temperature
        directly (what users see on device/app) without requiring conversion.
        """
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
        """Set new operation mode using DHW mode control."""
        # Map Home Assistant operation mode to DHW mode value
        dhw_mode_value = HA_TO_DHW_MODE.get(operation_mode)
        
        if dhw_mode_value is None:
            _LOGGER.error("Invalid operation mode: %s", operation_mode)
            return
        
        _LOGGER.debug("Setting DHW mode to %s (value: %d)", operation_mode, dhw_mode_value)
        
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_dhw_mode", mode=dhw_mode_value
        )
        
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to set operation mode to %s", operation_mode)

    async def async_turn_on(self) -> None:
        """Turn the water heater on by setting it to energy saver mode."""
        # When turning "on", set to energy saver (eco) mode as default
        await self.async_set_operation_mode(STATE_ECO)

    async def async_turn_away_mode_on(self) -> None:
        """Turn away mode on by setting to vacation mode."""
        # Vacation mode is handled separately from operation modes since it's not in operation_list
        # This follows HA design where away_mode is a dedicated feature, not an operation mode
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_dhw_mode", mode=5  # VACATION mode
        )
        
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to set vacation mode")

    async def async_turn_away_mode_off(self) -> None:
        """Turn away mode off by returning to eco mode."""
        await self.async_set_operation_mode(STATE_ECO)

    async def async_turn_off(self) -> None:
        """Turn the water heater off by setting to power off mode."""
        # Use DHW mode 6 (POWER_OFF) instead of the uncertain set_power method
        # This maps to the "off" operation mode in our DHW_MODE_TO_HA mapping
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_dhw_mode", mode=6  # POWER_OFF mode
        )
        
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to set water heater to power off mode")