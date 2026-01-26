"""Number platform for Navien NWP500 integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_TEMPERATURE, MIN_TEMPERATURE
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity

if TYPE_CHECKING:
    from nwp500 import Device  # type: ignore[attr-defined]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        entities.append(
            NWP500TargetTemperature(coordinator, mac_address, device)
        )

    async_add_entities(entities, True)


class NWP500TargetTemperature(NWP500Entity, NumberEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 target temperature number entity."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_TEMPERATURE
    _attr_native_max_value = MAX_TEMPERATURE
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_target_temperature"
        self._attr_name = f"{self.device_name} Target Temperature"
        self._attr_icon = "mdi:thermometer"

    @property
    def native_unit_of_measurement(self) -> str:
        """Return Home Assistant's configured temperature unit.
        
        Number entities don't do automatic unit conversion like sensors.
        We return HA's configured unit and let the library handle conversion
        through the DeviceStatus's temperature_type field.
        """
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return target temperature in HA's configured unit.
        
        The library returns values in the device's native unit. We convert
        to HA's configured unit if needed.
        """
        if not (status := self._status):
            return None
        try:
            target_temp = getattr(
                status, "dhw_target_temperature_setting", None
            )
            if target_temp is None:
                target_temp = getattr(status, "dhw_temperature_setting", None)
            
            if target_temp is None:
                return None
            
            target_temp = float(target_temp)
            
            # Get device's native unit and convert if needed
            try:
                device_unit = status.get_field_unit(
                    "dhw_target_temperature_setting"
                ).strip()
                ha_unit = self.hass.config.units.temperature_unit
                
                # Convert between Celsius and Fahrenheit if units differ
                if device_unit != ha_unit:
                    if device_unit == "°C" and ha_unit == "°F":
                        target_temp = (target_temp * 9 / 5) + 32
                    elif device_unit == "°F" and ha_unit == "°C":
                        target_temp = (target_temp - 32) * 5 / 9
            except (AttributeError, TypeError):
                pass
            
            return target_temp
        except (AttributeError, TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set target temperature, converting from HA's unit to device's unit."""
        try:
            if not (status := self._status):
                return
            
            temp_to_send = value
            
            # Convert from HA's unit back to device's native unit if needed
            try:
                device_unit = status.get_field_unit(
                    "dhw_target_temperature_setting"
                ).strip()
                ha_unit = self.hass.config.units.temperature_unit
                
                if device_unit != ha_unit:
                    if device_unit == "°C" and ha_unit == "°F":
                        # Convert from Fahrenheit to Celsius
                        temp_to_send = (value - 32) * 5 / 9
                    elif device_unit == "°F" and ha_unit == "°C":
                        # Convert from Celsius to Fahrenheit
                        temp_to_send = (value * 9 / 5) + 32
            except (AttributeError, TypeError):
                pass
            
            success = await self.coordinator.async_control_device(
                self.mac_address,
                "set_temperature",
                temperature=int(temp_to_send),
            )

            if success:
                await self.coordinator.async_request_refresh()
        except (AttributeError, TypeError, ValueError):
            pass
