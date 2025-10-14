"""Binary sensor platform for Navien NWP500 integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DEVICE_STATUS_BINARY_SENSORS
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity


@dataclass
class NWP500BinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes NWP500 binary sensor entity."""

    value_fn: Callable[[Any], bool | None] | None = None


def create_binary_sensor_descriptions() -> tuple[NWP500BinarySensorEntityDescription, ...]:
    """Create binary sensor descriptions from constants."""
    descriptions = []
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="operation_busy",
        name="Operation Busy",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'operationBusy', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="freeze_protection_use",
        name="Freeze Protection",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'freezeProtectionUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="dhw_use",
        name="DHW In Use",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'dhwUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="dhw_use_sustained",
        name="DHW Use Sustained",
        device_class=BinarySensorDeviceClass.RUNNING, 
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'dhwUseSustained', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="comp_use",
        name="Compressor Running",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'compUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="eev_use",
        name="EEV Active",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'eevUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="eva_fan_use",
        name="Evaporator Fan Running",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'evaFanUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="heat_upper_use",
        name="Upper Electric Heating Element",
        device_class=BinarySensorDeviceClass.HEAT,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'heatUpperUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="heat_lower_use",
        name="Lower Electric Heating Element", 
        device_class=BinarySensorDeviceClass.HEAT,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'heatLowerUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="current_heat_use",
        name="Current Heat Use",
        device_class=BinarySensorDeviceClass.HEAT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'currentHeatUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="scald_use",
        name="Scald Warning",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'scaldUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="anti_legionella_use",
        name="Anti-Legionella",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'antiLegionellaUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="anti_legionella_operation_busy",
        name="Anti-Legionella Operation Busy",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'antiLegionellaOperationBusy', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="air_filter_alarm_use",
        name="Air Filter Status",
        # Changed from PROBLEM to make it show normal/abnormal instead of problem/ok
        entity_registry_enabled_default=False,
        # Invert logic: False = normal (off), True = needs attention (on)
        value_fn=lambda status: not getattr(status, 'airFilterAlarmUse', True),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="error_buzzer_use",
        name="Error Buzzer",
        device_class=BinarySensorDeviceClass.SOUND,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'errorBuzzerUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="eco_use",
        name="Eco Mode Active",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'ecoUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="program_reservation_use",
        name="Program Reservation Active",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'programReservationUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="shut_off_valve_use",
        name="Shut Off Valve Use",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'shutOffValveUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="con_ovr_sensor_use",
        name="Condenser Override Sensor Use",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'conOvrSensorUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="wtr_ovr_sensor_use",
        name="Water Override Sensor Use",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'wtrOvrSensorUse', None),
    ))
    
    descriptions.append(NWP500BinarySensorEntityDescription(
        key="did_reload",
        name="Device Reloaded",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'didReload', None),
    ))
    
    return tuple(descriptions)


BINARY_SENSOR_DESCRIPTIONS = create_binary_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    entities = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(
                NWP500BinarySensor(coordinator, mac_address, device, description)
            )
    
    async_add_entities(entities, True)


class NWP500BinarySensor(NWP500Entity, BinarySensorEntity):
    """Navien NWP500 binary sensor entity."""

    entity_description: NWP500BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device,
        description: NWP500BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, mac_address, device)
        self.entity_description = description
        self._attr_unique_id = f"{mac_address}_{description.key}"
        self._attr_name = f"{self.device_name} {description.name}"

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        if self.entity_description.value_fn:
            try:
                return self.entity_description.value_fn(status)
            except (AttributeError, TypeError):
                return None
        
        return None

    @property
    def state(self) -> str | None:
        """Return the state of the binary sensor."""
        # For specific sensors, display custom state values
        if self.entity_description.key == "scald_use":
            # Scald Warning sensor displays "enabled/disabled"
            is_on = self.is_on
            if is_on is None:
                return None
            return "enabled" if is_on else "disabled"
        elif self.entity_description.key == "freeze_protection_use":
            # Freeze Protection sensor displays "enabled/disabled"
            is_on = self.is_on
            if is_on is None:
                return None
            return "enabled" if is_on else "disabled"
        elif self.entity_description.key == "anti_legionella_use":
            # Anti-Legionella sensor displays "enabled/disabled"
            is_on = self.is_on
            if is_on is None:
                return None
            return "enabled" if is_on else "disabled"
        elif self.entity_description.key == "error_buzzer_use":
            # Error Buzzer sensor displays "enabled/disabled"
            is_on = self.is_on
            if is_on is None:
                return None
            return "enabled" if is_on else "disabled"
        elif self.entity_description.key == "heat_upper_use":
            # Upper Electric Heating Element displays "active/inactive"
            is_on = self.is_on
            if is_on is None:
                return None
            return "active" if is_on else "inactive"
        elif self.entity_description.key == "heat_lower_use":
            # Lower Electric Heating Element displays "active/inactive"
            is_on = self.is_on
            if is_on is None:
                return None
            return "active" if is_on else "inactive"
        
        # For all other binary sensors, use the default behavior (on/off)
        return super().state
