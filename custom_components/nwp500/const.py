"""Constants for the Navien NWP500 integration."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_ELECTRIC,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
)

DOMAIN: Final = "nwp500"

# Configuration
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TOKEN_DATA: Final = "token_data"  # Token persistence

# Default values
DEFAULT_NAME: Final = "Navien NWP500"
# Default polling interval for device status updates.
# Set to 30 seconds to balance data freshness and server load.
# Users can configure this via integration options (10-300 seconds).
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds
MIN_SCAN_INTERVAL: Final = 10  # seconds (minimum to avoid server overload)
MAX_SCAN_INTERVAL: Final = 300  # seconds (maximum 5 minutes)

# Performance monitoring
# MQTT request/response typically takes 2-4 seconds due to cloud roundtrip
# Set threshold to 5 seconds to avoid false warnings during normal operation
SLOW_UPDATE_THRESHOLD: Final = (
    5.0  # seconds - warn if update takes longer than this
)

# Device types and models
DEVICE_TYPE_WATER_HEATER: Final = 52


# Utility functions
def get_enum_value(obj: Any) -> Any:
    """Get the value of an enum or return the object itself.

    This helper function safely extracts values from enums while
    gracefully handling non-enum types.

    Args:
        obj: An enum object or any other type

    Returns:
        The .value attribute if the object has one, otherwise the object itself
    """
    return obj.value if hasattr(obj, "value") else obj


# CurrentOperationMode mapping for Home Assistant water heater entity
# Maps nwp500-python CurrentOperationMode enum values to HA water heater states
CURRENT_OPERATION_MODE_TO_HA: Final = {
    0: "standby",  # STANDBY
    32: STATE_HEAT_PUMP,  # HEAT_PUMP_MODE (operational state)
    64: STATE_ECO,  # HYBRID_EFFICIENCY_MODE (operational state)
    96: STATE_HIGH_DEMAND,  # HYBRID_BOOST_MODE (operational state)
}

# DhwOperationSetting mapping for Home Assistant water heater entity
# Maps nwp500-python DhwOperationSetting enum values to HA water heater states
DHW_OPERATION_SETTING_TO_HA: Final = {
    1: STATE_HEAT_PUMP,  # HEAT_PUMP -> "heat_pump"
    2: STATE_ELECTRIC,  # ELECTRIC -> "electric"
    3: STATE_ECO,  # ENERGY_SAVER -> "eco"
    4: STATE_HIGH_DEMAND,  # HIGH_DEMAND -> "high_demand"
    5: "vacation",  # VACATION
    6: "off",  # POWER_OFF
}

# Reverse mapping for setting DHW operation modes
# This only includes "normal" operation modes that can be set through
# the operation_mode feature. Special states (vacation, power_off) are
# handled separately via away_mode and on_off features
HA_TO_DHW_MODE: Final = {
    STATE_ECO: 3,  # "eco" -> Energy Saver mode
    STATE_HEAT_PUMP: 1,  # "heat_pump" -> Heat Pump Only mode
    STATE_HIGH_DEMAND: 4,  # "high_demand" -> High Demand mode
    STATE_ELECTRIC: 2,  # "electric" -> Electric Only mode
    # Note: vacation (5) and power_off (6) modes are excluded here as
    # they are handled via dedicated away/on_off features
}

# Complete mapping for all DHW operation settings (includes special)
# Use this when handling vacation mode or displaying current DHW
HA_TO_DHW_OPERATION_SETTING: Final = {
    **HA_TO_DHW_MODE,  # Include all normal operation modes
    "vacation": 5,  # VACATION mode (handled via away_mode feature)
    # Note: power_off (6) handled via on_off feature, not stored here
}

# Legacy alias for backward compatibility within this component
DHW_MODE_TO_HA: Final = DHW_OPERATION_SETTING_TO_HA

# Temperature ranges (from nwp500-python documentation)
MIN_TEMPERATURE: Final = 80  # °F (minimum safe operating temperature)
MAX_TEMPERATURE: Final = 150  # °F (maximum supported by device)

# All device status fields that can be mapped to entities
# Most will be disabled by default but available for users to enable
DEVICE_STATUS_SENSORS: Final = {
    "outside_temperature": {
        "name": "Outside Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "tank_upper_temperature": {
        "name": "Tank Upper Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "tank_lower_temperature": {
        "name": "Tank Lower Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "discharge_temperature": {
        "name": "Discharge Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "suction_temperature": {
        "name": "Suction Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "evaporator_temperature": {
        "name": "Evaporator Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "ambient_temperature": {
        "name": "Ambient Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "dhw_temperature": {
        "name": "DHW Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "dhw_temperature_2": {
        "name": "DHW Temperature 2",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "current_inlet_temperature": {
        "name": "Current Inlet Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "current_inst_power": {
        "name": "Current Power",
        "device_class": "power",
        "unit": "W",
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "dhw_charge_per": {
        "name": "DHW Charge Percentage",
        "device_class": None,
        "unit": "%",
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "wifi_rssi": {
        "name": "WiFi RSSI",
        "device_class": "signal_strength",
        "unit": "dBm",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "error_code": {
        "name": "Error Code",
        "device_class": None,
        "unit": None,
        "state_class": None,
        "entity_registry_enabled_default": True,
    },
    "sub_error_code": {
        "name": "Sub Error Code",
        "device_class": None,
        "unit": None,
        "state_class": None,
        "entity_registry_enabled_default": False,
    },
    "current_dhw_flow_rate": {
        "name": "Current DHW Flow Rate",
        "device_class": None,
        "unit": "GPM",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "target_super_heat": {
        "name": "Target Super Heat",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "current_super_heat": {
        "name": "Current Super Heat",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "target_fan_rpm": {
        "name": "Target Fan RPM",
        "device_class": None,
        "unit": "RPM",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "current_fan_rpm": {
        "name": "Current Fan RPM",
        "device_class": None,
        "unit": "RPM",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "fan_pwm": {
        "name": "Fan PWM",
        "device_class": None,
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "mixing_rate": {
        "name": "Mixing Rate",
        "device_class": None,
        "unit": "%",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "eev_step": {
        "name": "EEV Step",
        "device_class": None,
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "vacation_day_setting": {
        "name": "Vacation Day Setting",
        "device_class": None,
        "unit": "days",
        "state_class": None,
        "entity_registry_enabled_default": False,
    },
    "vacation_day_elapsed": {
        "name": "Vacation Day Elapsed",
        "device_class": None,
        "unit": "days",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "cumulated_dhw_flow_rate": {
        "name": "Cumulated DHW Flow Rate",
        "device_class": None,
        "unit": "gallons",
        "state_class": "total_increasing",
        "entity_registry_enabled_default": False,
    },
    "total_energy_capacity": {
        "name": "Total Energy Capacity",
        "device_class": "energy",
        "unit": "Wh",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "available_energy_capacity": {
        "name": "Available Energy Capacity",
        "device_class": "energy",
        "unit": "Wh",
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
}

# Binary sensor fields for on/off states
DEVICE_STATUS_BINARY_SENSORS: Final = {
    "operation_busy": {
        "name": "Operation Busy",
        "device_class": "running",
        "entity_registry_enabled_default": True,
    },
    "freeze_protection_use": {
        "name": "Freeze Protection",
        "device_class": "safety",
        "entity_registry_enabled_default": False,
    },
    "dhw_use": {
        "name": "DHW In Use",
        "device_class": "running",
        "entity_registry_enabled_default": True,
    },
    "dhw_use_sustained": {
        "name": "DHW Use Sustained",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "comp_use": {
        "name": "Compressor Running",
        "device_class": "running",
        "entity_registry_enabled_default": True,
    },
    "eev_use": {
        "name": "EEV Active",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "eva_fan_use": {
        "name": "Evaporator Fan Running",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "heat_upper_use": {
        "name": "Upper Electric Heating Element",
        "device_class": "heat",
        "entity_registry_enabled_default": True,
    },
    "heat_lower_use": {
        "name": "Lower Electric Heating Element",
        "device_class": "heat",
        "entity_registry_enabled_default": True,
    },
    "current_heat_use": {
        "name": "Current Heat Use",
        "device_class": "heat",
        "entity_registry_enabled_default": False,
    },
    "scald_use": {
        "name": "Scald Warning",
        "device_class": "safety",
        "entity_registry_enabled_default": False,
    },
    "anti_legionella_use": {
        "name": "Anti-Legionella",
        "device_class": "safety",
        "entity_registry_enabled_default": False,
    },
    "anti_legionella_operation_busy": {
        "name": "Anti-Legionella Operation Busy",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "air_filter_alarm_use": {
        "name": "Air Filter Alarm",
        "device_class": "safety",
        "entity_registry_enabled_default": False,
    },
    "error_buzzer_use": {
        "name": "Error Buzzer",
        "device_class": "sound",
        "entity_registry_enabled_default": False,
    },
    "eco_use": {
        "name": "Eco Mode Active",
        "device_class": None,
        "entity_registry_enabled_default": False,
    },
    "program_reservation_use": {
        "name": "Program Reservation Active",
        "device_class": None,
        "entity_registry_enabled_default": False,
    },
}

# Data-driven sensor configuration
# This replaces ~400 lines of repetitive sensor description code
SENSOR_CONFIGS: Final = {
    # Temperature sensors
    "outside_temperature": {
        "attr": "outsideTemperature",
        "name": "Outside Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": True,
    },
    "tank_upper_temperature": {
        "attr": "tankUpperTemperature",
        "name": "Tank Upper Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": True,
    },
    "tank_lower_temperature": {
        "attr": "tankLowerTemperature",
        "name": "Tank Lower Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": True,
    },
    "discharge_temperature": {
        "attr": "dischargeTemperature",
        "name": "Discharge Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "suction_temperature": {
        "attr": "suctionTemperature",
        "name": "Suction Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "evaporator_temperature": {
        "attr": "evaporatorTemperature",
        "name": "Evaporator Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "ambient_temperature": {
        "attr": "ambientTemperature",
        "name": "Ambient Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "dhw_temperature": {
        "attr": "dhwTemperature",
        "name": "DHW Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": True,
    },
    "dhw_temperature_2": {
        "attr": "dhwTemperature2",
        "name": "DHW Temperature 2",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "current_inlet_temperature": {
        "attr": "currentInletTemperature",
        "name": "Current Inlet Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "freeze_protection_temperature": {
        "attr": "freezeProtectionTemperature",
        "name": "Freeze Protection Temperature",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "target_super_heat": {
        "attr": "targetSuperHeat",
        "name": "Target Super Heat",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    "current_super_heat": {
        "attr": "currentSuperHeat",
        "name": "Current Super Heat",
        "device_class": "temperature",
        "unit": "°F",
        "state_class": "measurement",
        "enabled": False,
    },
    # Power and energy sensors
    "current_inst_power": {
        "attr": "currentInstPower",
        "name": "Current Power",
        "device_class": "power",
        "unit": "W",
        "state_class": "measurement",
        "enabled": True,
    },
    "total_energy_capacity": {
        "attr": "totalEnergyCapacity",
        "name": "Total Energy Capacity",
        "device_class": "energy",
        "unit": "Wh",
        "state_class": "measurement",
        "enabled": False,
    },
    "available_energy_capacity": {
        "attr": "availableEnergyCapacity",
        "name": "Available Energy Capacity",
        "device_class": "energy",
        "unit": "Wh",
        "state_class": "measurement",
        "enabled": False,
    },
    # Percentage sensors
    "dhw_charge_per": {
        "attr": "dhwChargePer",
        "name": "DHW Charge",
        "unit": "%",
        "state_class": "measurement",
        "enabled": True,
    },
    "mixing_rate": {
        "attr": "mixingRate",
        "name": "Mixing Rate",
        "unit": "%",
        "state_class": "measurement",
        "enabled": False,
    },
    "fan_pwm": {
        "attr": "fanPwm",
        "name": "Fan PWM",
        "state_class": "measurement",
        "enabled": False,
    },
    # Signal strength
    "wifi_rssi": {
        "attr": "wifiRssi",
        "name": "WiFi RSSI",
        "device_class": "signal_strength",
        "unit": "dBm",
        "state_class": "measurement",
        "enabled": False,
    },
    # Status and error codes
    "error_code": {
        "attr": "errorCode",
        "name": "Error Code",
        "enabled": True,
    },
    "sub_error_code": {
        "attr": "subErrorCode",
        "name": "Sub Error Code",
        "enabled": False,
    },
    # Flow rate sensors
    "current_dhw_flow_rate": {
        "attr": "currentDhwFlowRate",
        "name": "Current DHW Flow Rate",
        "unit": "GPM",
        "state_class": "measurement",
        "enabled": False,
    },
    "cumulated_dhw_flow_rate": {
        "attr": "cumulatedDhwFlowRate",
        "name": "Cumulated DHW Flow Rate",
        "unit": "gallons",
        "state_class": "total_increasing",
        "enabled": False,
    },
    # Fan sensors
    "target_fan_rpm": {
        "attr": "targetFanRpm",
        "name": "Target Fan RPM",
        "unit": "RPM",
        "state_class": "measurement",
        "enabled": False,
    },
    "current_fan_rpm": {
        "attr": "currentFanRpm",
        "name": "Current Fan RPM",
        "unit": "RPM",
        "state_class": "measurement",
        "enabled": False,
    },
    # Vacation sensors
    "vacation_day_setting": {
        "attr": "vacationDaySetting",
        "name": "Vacation Day Setting",
        "unit": "days",
        "enabled": False,
    },
    "vacation_day_elapsed": {
        "attr": "vacationDayElapsed",
        "name": "Vacation Day Elapsed",
        "unit": "days",
        "state_class": "measurement",
        "enabled": False,
    },
    # Diagnostic sensors
    "eev_step": {
        "attr": "eevStep",
        "name": "EEV Step",
        "state_class": "measurement",
        "enabled": False,
    },
    "current_state_num": {
        "attr": "currentStateNum",
        "name": "Current State Number",
        "enabled": False,
    },
    "smart_diagnostic": {
        "attr": "smartDiagnostic",
        "name": "Smart Diagnostic",
        "enabled": False,
    },
    "special_function_status": {
        "attr": "specialFunctionStatus",
        "name": "Special Function Status",
        "enabled": False,
    },
    "fault_status_1": {
        "attr": "faultStatus1",
        "name": "Fault Status 1",
        "enabled": False,
    },
    "fault_status_2": {
        "attr": "faultStatus2",
        "name": "Fault Status 2",
        "enabled": False,
    },
    # Operation mode sensors (these have custom value_fn handling)
    "operation_mode": {
        "attr": "operationMode",
        "name": "Current Operation Mode",
        "enabled": True,
        "special": "enum_name",  # Custom handling for enum.name
    },
    "dhw_operation_setting": {
        "attr": "dhwOperationSetting",
        "name": "DHW Operation Setting",
        "enabled": True,
        "special": "enum_name",  # Custom handling for enum.name
    },
}
