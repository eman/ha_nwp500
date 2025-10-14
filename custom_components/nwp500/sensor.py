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
    UnitOfEnergy,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DEVICE_STATUS_SENSORS
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity


@dataclass
class NWP500SensorEntityDescription(SensorEntityDescription):
    """Describes NWP500 sensor entity."""

    value_fn: Callable[[Any], Any] | None = None


def create_sensor_descriptions() -> tuple[NWP500SensorEntityDescription, ...]:
    """Create sensor descriptions from constants."""
    descriptions = []
    
    # Temperature sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="outside_temperature",
        name="Outside Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'outsideTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="tank_upper_temperature",
        name="Tank Upper Temperature", 
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'tankUpperTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="tank_lower_temperature",
        name="Tank Lower Temperature",
        device_class=SensorDeviceClass.TEMPERATURE, 
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'tankLowerTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="discharge_temperature",
        name="Discharge Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'dischargeTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="suction_temperature",
        name="Suction Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'suctionTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="evaporator_temperature",
        name="Evaporator Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'evaporatorTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="ambient_temperature",
        name="Ambient Temperature", 
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'ambientTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="dhw_temperature",
        name="DHW Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'dhwTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="dhw_temperature_2",
        name="DHW Temperature 2",
        device_class=SensorDeviceClass.TEMPERATURE, 
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'dhwTemperature2', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="current_inlet_temperature",
        name="Current Inlet Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'currentInletTemperature', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="freeze_protection_temperature",
        name="Freeze Protection Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'freezeProtectionTemperature', None),
    ))
    
    # Power and energy sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="current_inst_power",
        name="Current Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'currentInstPower', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="total_energy_capacity", 
        name="Total Energy Capacity",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'totalEnergyCapacity', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="available_energy_capacity",
        name="Available Energy Capacity",
        device_class=SensorDeviceClass.ENERGY, 
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'availableEnergyCapacity', None),
    ))
    
    # Percentage sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="dhw_charge_per",
        name="DHW Charge",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'dhwChargePer', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="mixing_rate",
        name="Mixing Rate",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'mixingRate', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="fan_pwm",
        name="Fan PWM",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'fanPwm', None),
    ))
    
    # Signal strength
    descriptions.append(NWP500SensorEntityDescription(
        key="wifi_rssi",
        name="WiFi RSSI",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm", 
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'wifiRssi', None),
    ))
    
    # Error codes
    descriptions.append(NWP500SensorEntityDescription(
        key="error_code",
        name="Error Code",
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'errorCode', None) if getattr(status, 'errorCode', 0) != 0 else None,
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="sub_error_code",
        name="Sub Error Code",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'subErrorCode', None) if getattr(status, 'subErrorCode', 0) != 0 else None,
    ))
    
    # Flow rate sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="current_dhw_flow_rate",
        name="Current DHW Flow Rate",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="GPM",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'currentDhwFlowRate', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="cumulated_dhw_flow_rate",
        name="Cumulated DHW Flow Rate",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="gallons",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'cumulatedDhwFlowRate', None),
    ))
    
    # Fan sensors  
    descriptions.append(NWP500SensorEntityDescription(
        key="target_fan_rpm",
        name="Target Fan RPM",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="RPM",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'targetFanRpm', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="current_fan_rpm",
        name="Current Fan RPM",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="RPM", 
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'currentFanRpm', None),
    ))
    
    # Operation mode sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="operation_mode",
        name="Operation Mode",
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'operationMode', None).name if hasattr(getattr(status, 'operationMode', None), 'name') else str(getattr(status, 'operationMode', None)),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="dhw_operation_setting",
        name="DHW Operation Setting",
        entity_registry_enabled_default=True,
        value_fn=lambda status: getattr(status, 'dhwOperationSetting', None).name if hasattr(getattr(status, 'dhwOperationSetting', None), 'name') else str(getattr(status, 'dhwOperationSetting', None)),
    ))
    
    # Heat-related temperature sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="target_super_heat",
        name="Target Super Heat",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'targetSuperHeat', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="current_super_heat", 
        name="Current Super Heat",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'currentSuperHeat', None),
    ))
    
    # Vacation mode sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="vacation_day_setting",
        name="Vacation Day Setting",
        native_unit_of_measurement="days",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'vacationDaySetting', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="vacation_day_elapsed",
        name="Vacation Day Elapsed",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="days",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'vacationDayElapsed', None),
    ))
    
    # Misc numeric sensors
    descriptions.append(NWP500SensorEntityDescription(
        key="eev_step",
        name="EEV Step",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'eevStep', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="current_state_num",
        name="Current State Number",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'currentStatenum', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="smart_diagnostic",
        name="Smart Diagnostic",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'smartDiagnostic', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="special_function_status",
        name="Special Function Status",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'specialFunctionStatus', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="fault_status_1",
        name="Fault Status 1",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'faultStatus1', None),
    ))
    
    descriptions.append(NWP500SensorEntityDescription(
        key="fault_status_2",
        name="Fault Status 2",
        entity_registry_enabled_default=False,
        value_fn=lambda status: getattr(status, 'faultStatus2', None),
    ))
    
    return tuple(descriptions)


SENSOR_DESCRIPTIONS = create_sensor_descriptions()


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
                # Don't log as debug since many sensors may not be available on all devices
                return None
        
        return None
