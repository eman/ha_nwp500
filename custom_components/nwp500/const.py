"""Constants for the Navien NWP500 integration."""
from typing import Final
from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    STATE_ELECTRIC,
)

DOMAIN: Final = "nwp500"

# Configuration
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"

# Default values
DEFAULT_NAME: Final = "Navien NWP500"
# Default polling interval for device status updates. 
# Set to 30 seconds to balance data freshness and server load.
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds

# Device types and models
DEVICE_TYPE_WATER_HEATER: Final = 52

# Operation modes mapping for Home Assistant water heater entity
# Maps nwp500-python OperationMode enum values to HA water heater states
OPERATION_MODE_TO_HA: Final = {
    0: "standby",           # STANDBY
    1: STATE_HEAT_PUMP,     # HEAT_PUMP -> "heat_pump"
    2: STATE_ELECTRIC,      # ELECTRIC -> "electric"  
    3: STATE_ECO,           # ENERGY_SAVER -> "eco" (maps to ENERGY_SAVER in HA)
    4: STATE_HIGH_DEMAND,   # HIGH_DEMAND -> "high_demand"
    5: "vacation",          # VACATION
    6: "off",               # POWER_OFF
    32: STATE_HEAT_PUMP,    # HEAT_PUMP_MODE (operational state)
    64: STATE_ECO,          # HYBRID_EFFICIENCY_MODE (operational state) 
    96: STATE_HIGH_DEMAND,  # HYBRID_BOOST_MODE (operational state)
}

# Reverse mapping for setting operation modes
HA_TO_OPERATION_MODE: Final = {
    STATE_ECO: 3,            # "eco" -> ENERGY_SAVER
    STATE_HEAT_PUMP: 1,      # "heat_pump" -> HEAT_PUMP
    STATE_HIGH_DEMAND: 4,    # "high_demand" -> HIGH_DEMAND
    STATE_ELECTRIC: 2,       # "electric" -> ELECTRIC
    "vacation": 5,           # "vacation" -> VACATION
}

# DHW modes that can be read from the device (for display/status purposes)
# This includes all possible device states including vacation and off
DHW_MODE_TO_HA: Final = {
    1: STATE_HEAT_PUMP,     # Heat Pump Only
    2: STATE_ELECTRIC,      # Electric Only
    3: STATE_ECO,           # Energy Saver (Eco)
    4: STATE_HIGH_DEMAND,   # High Demand
    5: "vacation",          # Vacation mode (displayed but controlled via away_mode)
    6: "off",               # Power Off (displayed but controlled via on_off)
}

# DHW modes that can be set via async_set_operation_mode() 
# This only includes "normal" operation modes, excluding special states
HA_TO_DHW_MODE: Final = {
    STATE_ECO: 3,           # "eco" -> Energy Saver mode
    STATE_HEAT_PUMP: 1,     # "heat_pump" -> Heat Pump Only mode
    STATE_HIGH_DEMAND: 4,   # "high_demand" -> High Demand mode
    STATE_ELECTRIC: 2,      # "electric" -> Electric Only mode
    # Note: vacation (5) and off (6) modes are handled separately via 
    # away_mode and on_off features, not through operation_mode
}

# Temperature ranges (from nwp500-python v1.1.5 documentation)
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