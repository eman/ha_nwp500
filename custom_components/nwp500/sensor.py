"""Sensor platform for Navien NWP500 integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPower,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity


@dataclass
class NWP500SensorEntityDescription(SensorEntityDescription):
    """Describes NWP500 sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any] | None = None


SENSOR_DESCRIPTIONS: tuple[NWP500SensorEntityDescription, ...] = (
    NWP500SensorEntityDescription(
        key="tank_upper_temperature",
        name="Tank Upper Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.tankUpperTemperature,
    ),
    NWP500SensorEntityDescription(
        key="tank_lower_temperature",
        name="Tank Lower Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.tankLowerTemperature,
    ),
    NWP500SensorEntityDescription(
        key="outside_temperature",
        name="Outside Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.outsideTemperature,
    ),
    NWP500SensorEntityDescription(
        key="discharge_temperature",
        name="Discharge Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.dischargeTemperature,
    ),
    NWP500SensorEntityDescription(
        key="suction_temperature",
        name="Suction Temperature", 
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.suctionTemperature,
    ),
    NWP500SensorEntityDescription(
        key="evaporator_temperature",
        name="Evaporator Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.evaporatorTemperature,
    ),
    NWP500SensorEntityDescription(
        key="ambient_temperature",
        name="Ambient Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.ambientTemperature,
    ),
    NWP500SensorEntityDescription(
        key="dhw_temperature",
        name="DHW Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.dhwTemperature,
    ),
    NWP500SensorEntityDescription(
        key="dhw_temperature_setting",
        name="DHW Temperature Setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.dhwTemperatureSetting,
    ),
    NWP500SensorEntityDescription(
        key="dhw_target_temperature_setting",
        name="DHW Target Temperature Setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda status: status.dhwTargetTemperatureSetting,
    ),
    NWP500SensorEntityDescription(
        key="current_power",
        name="Current Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda status: status.currentInstPower,
    ),
    NWP500SensorEntityDescription(
        key="dhw_charge_percentage",
        name="DHW Charge Percentage",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda status: status.dhwChargePer,
    ),
    NWP500SensorEntityDescription(
        key="wifi_rssi",
        name="WiFi Signal Strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        value_fn=lambda status: status.wifiRssi,
    ),
    NWP500SensorEntityDescription(
        key="error_code",
        name="Error Code",
        value_fn=lambda status: status.errorCode if status.errorCode != 0 else None,
    ),
    NWP500SensorEntityDescription(
        key="sub_error_code",
        name="Sub Error Code",
        value_fn=lambda status: status.subErrorCode if status.subErrorCode != 0 else None,
    ),
    NWP500SensorEntityDescription(
        key="operation_mode",
        name="Operation Mode",
        value_fn=lambda status: str(status.operationMode) if hasattr(status.operationMode, 'name') else str(status.operationMode),
    ),
    NWP500SensorEntityDescription(
        key="compressor_use",
        name="Compressor Running",
        value_fn=lambda status: "On" if status.compUse else "Off",
    ),
    NWP500SensorEntityDescription(
        key="heat_upper_use",
        name="Upper Heat Element",
        value_fn=lambda status: "On" if status.heatUpperUse else "Off",
    ),
    NWP500SensorEntityDescription(
        key="heat_lower_use", 
        name="Lower Heat Element",
        value_fn=lambda status: "On" if status.heatLowerUse else "Off",
    ),
    NWP500SensorEntityDescription(
        key="current_fan_rpm",
        name="Fan RPM",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="RPM",
        value_fn=lambda status: status.currentFanRpm,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    
    entities = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        for description in SENSOR_DESCRIPTIONS:
            entities.append(
                NWP500Sensor(coordinator, mac_address, device, description)
            )
    
    async_add_entities(entities, True)


class NWP500Sensor(NWP500Entity, SensorEntity):
    """Navien NWP500 sensor entity."""

    entity_description: NWP500SensorEntityDescription

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device,
        description: NWP500SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device)
        self.entity_description = description
        self._attr_unique_id = f"{mac_address}_{description.key}"
        self._attr_name = f"{self.device_name} {description.name}"

    @property
    def native_value(self) -> Any:
        """Return the value reported by the sensor."""
        if not self.device_data:
            return None
        
        status = self.device_data.get("status")
        if not status:
            return None
        
        if self.entity_description.value_fn:
            try:
                return self.entity_description.value_fn(status)
            except (AttributeError, TypeError) as err:
                _LOGGER.debug("Error getting sensor value for %s: %s", self.entity_description.key, err)
                return None
        
        return None