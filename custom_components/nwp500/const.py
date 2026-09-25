"""Constants for the Navien NWP500 integration."""

from typing import TYPE_CHECKING, Any, Final, TypedDict

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_ELECTRIC,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
)

from nwp500.enums import CurrentOperationMode, DhwOperationSetting

if TYPE_CHECKING:
    from nwp500 import (  # type: ignore[attr-defined]
        Device,
        DeviceFeature,
        DeviceStatus,
    )

DOMAIN: Final = "nwp500"


# Type definitions
class DeviceStatusEvent(TypedDict):
    """Type definition for device status event data."""

    device: Device
    status: DeviceStatus


class DeviceFeatureEvent(TypedDict):
    """Type definition for device feature event data."""

    device: Device
    feature: DeviceFeature


# Configuration
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TOKEN_DATA: Final = "token_data"  # Token persistence

# Default values
DEFAULT_NAME: Final = "Navien NWP500"
DEFAULT_TEMPERATURE_F: Final = 120.0
DEFAULT_TEMPERATURE_C: Final = 49.0
# Default polling interval for device status updates.
# Set to 30 seconds to balance data freshness and server load.
# Users can configure this via integration options (10-300 seconds).
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds
MIN_SCAN_INTERVAL: Final = 10  # seconds (minimum to avoid server overload)
MAX_SCAN_INTERVAL: Final = 300  # seconds (maximum 5 minutes)

# Performance monitoring
# MQTT request/response typically takes 2-4 seconds due to cloud roundtrip.
# First-update latency can reach ~8s due to AWS IoT TLS/session establishment.
# Set threshold to 15 seconds to suppress cold-start noise.
SLOW_UPDATE_THRESHOLD: Final = (
    15.0  # seconds - warn if update takes longer than this
)

# How many coordinator update cycles between re-reads of the programmed
# reservation/TOU schedules. They change rarely and only on request, so this
# is deliberately slower than the device-info fallback (every 10th cycle):
# at the default 30s interval this is roughly every 20 minutes.
SCHEDULE_REFRESH_CYCLES: Final = 40

# Coordinator cycles between REST re-reads of the device list, which carries
# the cloud-recorded fault and descaling window. Cheap (one HTTP request for
# the whole account) but not worth doing every cycle: at the default 30s
# interval this is roughly every 10 minutes.
DEVICE_METADATA_REFRESH_CYCLES: Final = 20

# Reconnection backoff parameters
# Exponential backoff delays (seconds) for MQTT reconnection attempts
RECONNECT_BACKOFF_DELAYS: Final = [2.0, 5.0, 15.0, 30.0, 60.0]
# Minimum seconds between reconnection attempts (prevents rapid retry loops)
MIN_RECONNECT_INTERVAL: Final = 30.0

# Entity staleness threshold
# Number of consecutive update cycles without fresh data before marking unavailable
STALE_DATA_THRESHOLD: Final = 5

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
# Maps nwp500.enums.CurrentOperationMode enum values to HA water heater states
CURRENT_OPERATION_MODE_TO_HA: Final = {
    CurrentOperationMode.STANDBY: "standby",
    CurrentOperationMode.HEAT_PUMP_MODE: STATE_HEAT_PUMP,
    CurrentOperationMode.HYBRID_EFFICIENCY_MODE: STATE_ECO,
    CurrentOperationMode.HYBRID_BOOST_MODE: STATE_HIGH_DEMAND,
}

# DhwOperationSetting mapping for Home Assistant water heater entity
# Maps nwp500.enums.DhwOperationSetting enum values to HA water heater states
DHW_OPERATION_SETTING_TO_HA: Final = {
    DhwOperationSetting.HEAT_PUMP: STATE_HEAT_PUMP,
    DhwOperationSetting.ELECTRIC: STATE_ELECTRIC,
    DhwOperationSetting.ENERGY_SAVER: STATE_ECO,
    DhwOperationSetting.HIGH_DEMAND: STATE_HIGH_DEMAND,
    DhwOperationSetting.VACATION: "vacation",
    DhwOperationSetting.POWER_OFF: "off",
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
# Uses dict unpacking (**) - Python 3.5+ feature for clean dict merging
HA_TO_DHW_OPERATION_SETTING: Final = {
    **HA_TO_DHW_MODE,  # Include all normal operation modes
    "vacation": 5,  # VACATION mode (handled via away_mode feature)
    # Note: power_off (6) handled via on_off feature, not stored here
}

# Alias for consistency
DHW_MODE_TO_HA: Final = DHW_OPERATION_SETTING_TO_HA


def get_current_operation_mode_state(
    value: Any, default: str | None = None
) -> str:
    """Map a CurrentOperationMode value to a Home Assistant state string."""
    raw_value = get_enum_value(value)
    fallback = f"mode_{raw_value}" if default is None else default

    try:
        return CURRENT_OPERATION_MODE_TO_HA[CurrentOperationMode(raw_value)]
    except TypeError, ValueError, KeyError:
        return fallback


def get_dhw_operation_setting_state(
    value: Any, default: str | None = None
) -> str:
    """Map a DhwOperationSetting value to a Home Assistant state string."""
    raw_value = get_enum_value(value)
    fallback = f"mode_{raw_value}" if default is None else default

    try:
        return DHW_OPERATION_SETTING_TO_HA[DhwOperationSetting(raw_value)]
    except TypeError, ValueError, KeyError:
        return fallback


# Mapping for reservation service calls (friendly mode names to DHW mode IDs)
# Used by set_reservation and related services
MODE_TO_DHW_ID: Final = {
    "heat_pump": 1,
    "electric": 2,
    "energy_saver": 3,
    "high_demand": 4,
    "vacation": 5,
    "power_off": 6,
}

# Temperature ranges (from nwp500-python documentation)
MIN_TEMPERATURE_F: Final = 80  # °F (minimum safe operating temperature)
MAX_TEMPERATURE_F: Final = 150  # °F (maximum supported by device)
MIN_TEMPERATURE_C: Final = 27  # °C (~80.6°F)
MAX_TEMPERATURE_C: Final = 65  # °C (~149°F)

# All device status fields that can be mapped to entities
# Most will be disabled by default but available for users to enable
DEVICE_STATUS_SENSORS: Final = {
    "outside_temperature": {
        "name": "Outside Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "tank_upper_temperature": {
        "name": "Tank Upper Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "tank_lower_temperature": {
        "name": "Tank Lower Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "discharge_temperature": {
        "name": "Compressor Discharge Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "suction_temperature": {
        "name": "Compressor Suction Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "evaporator_temperature": {
        "name": "Evaporator Coil Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "ambient_temperature": {
        "name": "Ambient Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "dhw_temperature": {
        "name": "DHW Outlet Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": True,
    },
    "dhw_temperature_2": {
        "name": "DHW Secondary Sensor Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "current_inlet_temperature": {
        "name": "Cold Water Inlet Temperature",
        "device_class": "temperature",
        "unit": None,
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
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "target_super_heat": {
        "name": "Target Superheat",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "entity_registry_enabled_default": False,
    },
    "current_super_heat": {
        "name": "Current Superheat",
        "device_class": "temperature",
        "unit": None,
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
        "unit": None,
        "state_class": "total_increasing",
        "entity_registry_enabled_default": False,
    },
    "usable_energy": {
        "name": "Usable Energy",
        "device_class": "energy_storage",
        "unit": "Wh",
        "entity_registry_enabled_default": True,
    },
    "energy_to_setpoint": {
        "name": "Energy to Setpoint",
        "device_class": None,
        "unit": "Wh",
        "entity_registry_enabled_default": False,
    },
    "full_recovery_energy": {
        "name": "Full Recovery Energy",
        "device_class": None,
        "unit": "Wh",
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
        "name": "Freeze Protection Active",
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
    "scald_use": {
        "name": "Scald Protection Warning",
        "device_class": "safety",
        "entity_registry_enabled_default": False,
    },
    "anti_legionella_use": {
        "name": "Anti-Legionella Enabled",
        "entity_registry_enabled_default": False,
    },
    "anti_legionella_operation_busy": {
        "name": "Anti-Legionella Cycle Running",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "air_filter_alarm_use": {
        "name": "Air Filter Alarm Enabled",
        "entity_registry_enabled_default": False,
    },
    "error_buzzer_use": {
        "name": "Error Buzzer Enabled",
        "entity_registry_enabled_default": False,
    },
    "eco_use": {
        "name": "Overheat Protection Enabled",
        "entity_registry_enabled_default": False,
    },
    "program_reservation_use": {
        "name": "Program Reservation Active",
        "device_class": None,
        "entity_registry_enabled_default": False,
    },
    # Recirculation sensors
    "recirculation_use": {
        "name": "Recirculation Active",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "recirculation_pump_operation_status": {
        "name": "Recirculation Pump Running",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "recirculation_operation_busy": {
        "name": "Recirculation Operation Busy",
        "device_class": "running",
        "entity_registry_enabled_default": False,
    },
    "recirculation_hot_button_ready": {
        "name": "Recirculation Hot Button Ready",
        "device_class": None,
        "entity_registry_enabled_default": False,
    },
    "recirculation_reservation_use": {
        "name": "Recirculation Reservation Active",
        "device_class": None,
        "entity_registry_enabled_default": False,
    },
    # Sensor status
    "con_ovr_sensor_use": {
        "name": "Condensate Overflow Sensor Active",
        "entity_registry_enabled_default": False,
    },
    "wtr_ovr_sensor_use": {
        "name": "Water Leak Detected",
        "device_class": "safety",
        "entity_registry_enabled_default": False,
    },
    "shut_off_valve_use": {
        "name": "Shut-Off Valve Status",
        "entity_registry_enabled_default": False,
    },
}

# Data-driven sensor configuration
# This replaces ~400 lines of repetitive sensor description code
SENSOR_CONFIGS: Final = {
    # Temperature sensors
    "outside_temperature": {
        "attr": "outside_temperature",
        "name": "Outside Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": True,
    },
    "tank_upper_temperature": {
        "attr": "tank_upper_temperature",
        "name": "Tank Upper Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": True,
    },
    "tank_lower_temperature": {
        "attr": "tank_lower_temperature",
        "name": "Tank Lower Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": True,
    },
    "discharge_temperature": {
        "attr": "discharge_temperature",
        "name": "Compressor Discharge Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "suction_temperature": {
        "attr": "suction_temperature",
        "name": "Compressor Suction Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "evaporator_temperature": {
        "attr": "evaporator_temperature",
        "name": "Evaporator Coil Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "ambient_temperature": {
        "attr": "ambient_temperature",
        "name": "Ambient Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "dhw_temperature": {
        "attr": "dhw_temperature",
        "name": "DHW Outlet Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": True,
    },
    "dhw_temperature_2": {
        "attr": "dhw_temperature2",
        "name": "DHW Secondary Sensor Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "current_inlet_temperature": {
        "attr": "current_inlet_temperature",
        "name": "Cold Water Inlet Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "freeze_protection_temperature": {
        "attr": "freeze_protection_temperature",
        "name": "Freeze Protection Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "target_super_heat": {
        "attr": "target_super_heat",
        "name": "Target Superheat",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "current_super_heat": {
        "attr": "current_super_heat",
        "name": "Current Superheat",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    # Power and energy sensors
    "current_inst_power": {
        "attr": "current_inst_power",
        "name": "Current Power",
        "device_class": "power",
        "unit": "W",
        "state_class": "measurement",
        "enabled": True,
    },
    # Energy drawable from the tank as useful hot water. This is the only one
    # of the three that behaves like a state of charge: the other two are both
    # measured from the setpoint, so they move when the setpoint moves even
    # though the water in the tank does not.
    "usable_energy": {
        "attr": "usable_energy",
        "name": "Usable Energy",
        "device_class": "energy_storage",
        "unit": "Wh",
        "state_class": "measurement",
        "enabled": True,
    },
    # Energy needed to bring the tank from its current temperature up to the
    # setpoint. No device_class: a deficit is not stored energy.
    "energy_to_setpoint": {
        "attr": "energy_to_setpoint",
        "name": "Energy to Setpoint",
        "unit": "Wh",
        "state_class": "measurement",
        "precision": 0,
        "enabled": False,
    },
    # Energy to recover a fully depleted tank to the current setpoint.
    "full_recovery_energy": {
        "attr": "full_recovery_energy",
        "name": "Full Recovery Energy",
        "unit": "Wh",
        "state_class": "measurement",
        "precision": 0,
        "enabled": False,
    },
    # Percentage sensors
    "dhw_charge_per": {
        "attr": "dhw_charge_per",
        "name": "DHW Charge",
        "unit": "%",
        "state_class": "measurement",
        "enabled": True,
    },
    "mixing_rate": {
        "attr": "mixing_rate",
        "name": "Mixing Rate",
        "unit": "%",
        "state_class": "measurement",
        "enabled": False,
    },
    "fan_pwm": {
        "attr": "fan_pwm",
        "name": "Fan PWM",
        "state_class": "measurement",
        "enabled": False,
    },
    # Signal strength
    "wifi_rssi": {
        "attr": "wifi_rssi",
        "name": "WiFi RSSI",
        "device_class": "signal_strength",
        "unit": "dBm",
        "state_class": "measurement",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    # Status and error codes
    "error_code": {
        "attr": "error_code",
        "name": "Error Code",
        "special": "enum_name",
        "enabled": True,
        "entity_category": "diagnostic",
    },
    "sub_error_code": {
        "attr": "sub_error_code",
        "name": "Sub Error Code",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    # Flow rate sensors
    "current_dhw_flow_rate": {
        "attr": "current_dhw_flow_rate",
        "name": "Current DHW Flow Rate",
        "unit": "GPM",
        "state_class": "measurement",
        "enabled": False,
    },
    "cumulated_dhw_flow_rate": {
        "attr": "cumulated_dhw_flow_rate",
        "name": "Cumulated DHW Flow Rate",
        "device_class": "water",
        "unit": "gal",
        "state_class": "total_increasing",
        "enabled": False,
    },
    # Fan sensors
    "target_fan_rpm": {
        "attr": "target_fan_rpm",
        "name": "Target Fan RPM",
        "unit": "RPM",
        "state_class": "measurement",
        "enabled": False,
    },
    "current_fan_rpm": {
        "attr": "current_fan_rpm",
        "name": "Current Fan RPM",
        "unit": "RPM",
        "state_class": "measurement",
        "enabled": False,
    },
    # Vacation sensors
    "vacation_day_setting": {
        "attr": "vacation_day_setting",
        "name": "Vacation Day Setting",
        "unit": "d",
        "device_class": "duration",
        "enabled": False,
    },
    "vacation_day_elapsed": {
        "attr": "vacation_day_elapsed",
        "name": "Vacation Day Elapsed",
        "unit": "d",
        "device_class": "duration",
        "state_class": "measurement",
        "enabled": False,
    },
    # Heat source sensor
    "current_heat_use": {
        "attr": "current_heat_use",
        "name": "Current Heat Source",
        "special": "enum_name",
        "enabled": True,
    },
    # Diagnostic sensors
    "eev_step": {
        "attr": "eev_step",
        "name": "EEV Step",
        "state_class": "measurement",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    "current_state_num": {
        "attr": "current_statenum",
        "name": "Current State Number",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    "smart_diagnostic": {
        "attr": "smart_diagnostic",
        "name": "Smart Diagnostic",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    "special_function_status": {
        "attr": "special_function_status",
        "name": "Special Function Status",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    "fault_status_1": {
        "attr": "fault_status1",
        "name": "Fault Status 1",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    "fault_status_2": {
        "attr": "fault_status2",
        "name": "Fault Status 2",
        "enabled": False,
        "entity_category": "diagnostic",
    },
    # Operation mode sensors (these have custom value_fn handling)
    "operation_mode": {
        "attr": "operation_mode",
        "name": "Current Operation Mode",
        "enabled": True,
        "special": "enum_name",  # Custom handling for enum.name
    },
    "dhw_operation_setting": {
        "attr": "dhw_operation_setting",
        "name": "DHW Operation Setting",
        "enabled": True,
        "special": "enum_name",  # Custom handling for enum.name
    },
    # DHW temperature settings
    "dhw_target_temperature_setting": {
        "attr": "dhw_target_temperature_setting",
        "name": "DHW Target Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "dhw_temperature_setting": {
        "attr": "dhw_temperature_setting",
        "name": "DHW Target Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    # Heat pump temperature settings
    "hp_upper_on_temp_setting": {
        "attr": "hp_upper_on_temp_setting",
        "name": "HP Upper On Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "hp_lower_on_temp_setting": {
        "attr": "hp_lower_on_temp_setting",
        "name": "HP Lower On Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "hp_upper_off_temp_setting": {
        "attr": "hp_upper_off_temp_setting",
        "name": "HP Upper Off Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "hp_lower_off_temp_setting": {
        "attr": "hp_lower_off_temp_setting",
        "name": "HP Lower Off Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    # Differential temperature settings (relative values, not absolute)
    # Note: device_class is intentionally omitted from differential temp sensors.
    # Home Assistant applies absolute temperature conversions (including offset) to
    # entities with device_class: temperature, which is incorrect for relative values.
    # Differential sensors represent the difference between two temperatures, not
    # absolute values, so the conversion would produce invalid results.
    "hp_upper_on_diff_temp_setting": {
        "attr": "hp_upper_on_diff_temp_setting",
        "name": "HP Upper On Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "hp_lower_on_diff_temp_setting": {
        "attr": "hp_lower_on_diff_temp_setting",
        "name": "HP Lower On Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "hp_upper_off_diff_temp_setting": {
        "attr": "hp_upper_off_diff_temp_setting",
        "name": "HP Upper Off Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "hp_lower_off_diff_temp_setting": {
        "attr": "hp_lower_off_diff_temp_setting",
        "name": "HP Lower Off Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    # Electric heating temperature settings
    "he_upper_on_temp_setting": {
        "attr": "he_upper_on_temp_setting",
        "name": "HE Upper On Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "he_lower_on_temp_setting": {
        "attr": "he_lower_on_temp_setting",
        "name": "HE Lower On Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "he_upper_off_temp_setting": {
        "attr": "he_upper_off_temp_setting",
        "name": "HE Upper Off Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "he_lower_off_temp_setting": {
        "attr": "he_lower_off_temp_setting",
        "name": "HE Lower Off Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "he_upper_on_diff_temp_setting": {
        "attr": "he_upper_on_diff_temp_setting",
        "name": "HE Upper On Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "he_lower_on_diff_temp_setting": {
        "attr": "he_lower_on_diff_temp_setting",
        "name": "HE Lower On Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "he_upper_off_diff_temp_setting": {
        "attr": "he_upper_off_diff_temp_setting",
        "name": "HE Upper Off Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "he_lower_off_diff_temp_setting": {
        "attr": "he_lower_off_diff_temp_setting",
        "name": "HE Lower Off Diff Temperature Setting",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    # Other temperature settings
    "heat_min_op_temperature": {
        "attr": "heat_min_op_temperature",
        "name": "Heat Min Operating Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "freeze_protection_temp_min": {
        "attr": "freeze_protection_temp_min",
        "name": "Freeze Protection Min Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "freeze_protection_temp_max": {
        "attr": "freeze_protection_temp_max",
        "name": "Freeze Protection Max Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "recirculation_temp_setting": {
        "attr": "recirc_temp_setting",
        "name": "Recirculation Temperature Setting",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "recirculation_temperature": {
        "attr": "recirc_temperature",
        "name": "Recirculation Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "recirculation_faucet_temperature": {
        "attr": "recirc_faucet_temperature",
        "name": "Recirculation Faucet Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    # Flow rate sensors
    "recirculation_dhw_flow_rate": {
        "attr": "recirc_dhw_flow_rate",
        "name": "Recirculation DHW Flow Rate",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    # Operation time sensors
    "cumulated_evaporator_fan_op_time": {
        "attr": "cumulated_op_time_eva_fan",
        "name": "Cumulated Evaporator Fan Operation Time",
        "unit": "h",
        "device_class": "duration",
        "state_class": "total_increasing",
        "enabled": False,
    },
    # Anti-legionella and alarm settings
    "anti_legionella_period": {
        "attr": "anti_legionella_period",
        "name": "Anti-Legionella Period",
        "unit": "d",
        "device_class": "duration",
        "state_class": "measurement",
        "enabled": False,
    },
    "air_filter_alarm_period": {
        "attr": "air_filter_alarm_period",
        "name": "Air Filter Alarm Period",
        "unit": "h",
        "device_class": "duration",
        "state_class": "measurement",
        "enabled": False,
    },
    "air_filter_alarm_elapsed": {
        "attr": "air_filter_alarm_elapsed",
        "name": "Air Filter Alarm Elapsed",
        "unit": "h",
        "device_class": "duration",
        "state_class": "measurement",
        "enabled": False,
    },
    # Diagnostic and status sensors
    "temperature_type": {
        "attr": "temperature_type",
        "name": "Temperature Type",
        "special": "enum_name",
        "enabled": False,
    },
    "temp_formula_type": {
        "attr": "temp_formula_type",
        "name": "Temperature Formula Type",
        "special": "enum_name",
        "enabled": False,
    },
    "dr_event_status": {
        "attr": "dr_event_status",
        "name": "DR Event Status",
        "special": "enum_name",
        "enabled": False,
    },
    "dr_override_status": {
        "attr": "dr_override_status",
        "name": "DR Override Hours Remaining",
        "unit": "h",
        "device_class": "duration",
        "state_class": "measurement",
        "enabled": False,
    },
    "recirculation_error_status": {
        "attr": "recirc_error_status",
        "name": "Recirculation Error Status",
        "enabled": False,
    },
    "recirculation_operation_reason": {
        "attr": "recirc_operation_reason",
        "name": "Recirculation Operation Reason",
        "enabled": False,
    },
    "recirculation_operation_mode": {
        "attr": "recirc_operation_mode",
        "name": "Recirculation Operation Mode",
        "special": "enum_name",
        "enabled": False,
    },
    "recirculation_model_type_code": {
        "attr": "recirc_model_type_code",
        "name": "Recirculation Model Type Code",
        "enabled": False,
    },
    "recirculation_sw_version": {
        "attr": "recirc_sw_version",
        "name": "Recirculation Software Version",
        "enabled": False,
    },
    "recirculation_temperature_min": {
        "attr": "recirc_temperature_min",
        "name": "Recirculation Minimum Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "recirculation_temperature_max": {
        "attr": "recirc_temperature_max",
        "name": "Recirculation Maximum Temperature",
        "device_class": "temperature",
        "unit": None,
        "state_class": "measurement",
        "enabled": False,
    },
    "program_reservation_type": {
        "attr": "program_reservation_type",
        "name": "Program Reservation Type",
        "enabled": False,
    },
}


# Installer diagnostics counters (nwp500-python 9.4.0, `DeviceDiagnostics`).
# These are lifetime totals the device reports only when asked, so they are
# read from the coordinator's `device_diagnostics` store rather than from the
# status object. `block` is the model section (`ts_data`, `td_data` or
# `ta_data`) and `field` the counter within it. Units follow the library's
# notes: energies are Wh (they match the energy query's lifetime totals),
# `cumulated_op_time_*` are hours, `cumulated_op_num_*` are start counts.
# Counters whose units the vendor does not document (demand-response
# operation times, hot-water draw statistics) are left to the diagnostics
# dump rather than shown as sensors with a made-up unit.
INSTALLER_DIAGNOSTICS_SENSORS: Final[dict[str, dict[str, Any]]] = {
    # The two headline counters. Split lifetime energy by heat source is
    # what the Energy dashboard wants and nothing else on the device
    # measures it, so these are on by default and not filed as diagnostic.
    "lifetime_heat_pump_energy": {
        "block": "ts_data",
        "field": "cumulated_pwr_hp",
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "entity_category": None,
        "enabled": True,
    },
    "lifetime_heat_element_energy": {
        "block": "ts_data",
        "field": "cumulated_pwr_he",
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "entity_category": None,
        "enabled": True,
    },
    "days_since_installation": {
        "block": "ts_data",
        "field": "days_since_installation",
        "unit": "d",
        "device_class": "duration",
        "state_class": "total_increasing",
        "enabled": False,
    },
    # Component run times (hours) and start counts.
    "compressor_run_time": {
        "block": "ta_data",
        "field": "cumulated_op_time_comp",
        "unit": "h",
        "device_class": "duration",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "compressor_start_count": {
        "block": "ta_data",
        "field": "cumulated_op_num_comp",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "evaporator_fan_run_time": {
        "block": "ta_data",
        "field": "cumulated_op_time_eva_fan",
        "unit": "h",
        "device_class": "duration",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "evaporator_fan_start_count": {
        "block": "ta_data",
        "field": "cumulated_op_num_eva_fan",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "upper_element_run_time": {
        "block": "ta_data",
        "field": "cumulated_op_time_uhe",
        "unit": "h",
        "device_class": "duration",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "upper_element_start_count": {
        "block": "ta_data",
        "field": "cumulated_op_num_uhe",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "lower_element_run_time": {
        "block": "ta_data",
        "field": "cumulated_op_time_lhe",
        "unit": "h",
        "device_class": "duration",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "lower_element_start_count": {
        "block": "ta_data",
        "field": "cumulated_op_num_lhe",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "recirculation_pump_run_time": {
        "block": "ta_data",
        "field": "cumulated_op_time_recirc_pump",
        "unit": "h",
        "device_class": "duration",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "recirculation_pump_start_count": {
        "block": "ta_data",
        "field": "cumulated_op_num_recirc_pump",
        "state_class": "total_increasing",
        "enabled": False,
    },
    # Fault and protection event counts.
    "freeze_protection_event_count": {
        "block": "ts_data",
        "field": "num_of_frost_protect_burn",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "dry_fire_event_count": {
        "block": "ts_data",
        "field": "cumulated_occ_num_dry_fire",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "condensate_overflow_event_count": {
        "block": "ts_data",
        "field": "cumulated_occ_num_con_ovr_flow",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "water_leak_event_count": {
        "block": "ts_data",
        "field": "cumulated_occ_num_wtr_ovr_flow",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "heat_pump_error_count": {
        "block": "ts_data",
        "field": "cumulated_occ_num_hpo",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "abnormal_discharge_temperature_count": {
        "block": "ts_data",
        "field": "cumulated_occ_num_ab_dis_tmp",
        "state_class": "total_increasing",
        "enabled": False,
    },
    "abnormal_suction_temperature_count": {
        "block": "ts_data",
        "field": "cumulated_occ_num_ab_suc_tmp",
        "state_class": "total_increasing",
        "enabled": False,
    },
}


# External control (issue #158). The option keys live here because the
# options flow is shared code; everything else about the feature is in the
# `control` subpackage, which is imported only when the feature is enabled.
CONF_CONTROL_ENABLED: Final = "control_enabled"
CONF_CONTROL_MODE: Final = "control_mode"
CONF_CONTROL_INTENT_ENTITY: Final = "control_intent_entity"
CONF_CONTROL_LIVE_SEGMENTS: Final = "control_live_segments"
CONF_CONTROL_LIVE_GRANTS: Final = "control_live_grants"
CONF_CONTROL_SURPLUS_ENTITY: Final = "control_surplus_entity"
CONF_CONTROL_SURPLUS_THRESHOLD_KW: Final = "control_surplus_threshold_kw"
CONF_CONTROL_SETPOINT_MIN_F: Final = "control_setpoint_min_f"
CONF_CONTROL_SETPOINT_MAX_F: Final = "control_setpoint_max_f"
CONF_CONTROL_ALLOWED_MODES: Final = "control_allowed_modes"
CONF_CONTROL_ASSISTED_MODE: Final = "control_assisted_mode"
CONF_CONTROL_MIN_RUN_BEFORE_LOWER_MIN: Final = (
    "control_min_run_before_lower_min"
)
CONF_CONTROL_RESERVATION_ENTRY_LIMIT: Final = "control_reservation_entry_limit"
CONF_CONTROL_RESERVATION_ENTRY_RESERVE: Final = (
    "control_reservation_entry_reserve"
)

# Options the first draft of the specification had. They are dropped from
# an entry's options whenever the control form is saved.
CONTROL_OBSOLETE_OPTIONS: Final = (
    "control_live_types",
    "control_hold_off_supported",
    "control_hold_off_margin_f",
    "control_tou_off_for_mode",
    "control_min_run_before_stop_min",
    "control_daily_revert_time",
    "control_baseline",
)

CONF_CONTROL_OWNER_PROGRAM: Final = "control_owner_program"

CONTROL_MODE_SHADOW: Final = "shadow"
CONTROL_MODE_LIVE: Final = "live"
CONTROL_MODE_DISABLED: Final = "disabled"
CONTROL_MODES_SELECTABLE: Final = (CONTROL_MODE_SHADOW, CONTROL_MODE_DISABLED)

# Whether live mode can be chosen at all (issue #158, delivery step 5). Live
# writes the heater's reservation list. It was opened after the staged live
# cut-over on a real heater (spec section 8, the live trial, 2026-09-25).
# It is the kill switch: set it to False and the options form stops offering
# `live`, and a `live` option runs as shadow. Handing the heater back is not
# gated by it.
CONTROL_LIVE_AVAILABLE: Final = True

# The mode names a segment may use (spec section 3.4). Vacation and
# power-off are never accepted. Entries are skipped during Vacation, so the
# plan's next entry would never end it; whether an entry with the power-off
# mode powers the heater off is untested, and the mode command with that
# value switched the unit tested to Energy Saver (#160).
CONTROL_MODE_NAMES: Final = (
    "heat_pump",
    "energy_saver",
    "high_demand",
    "electric",
)

DEFAULT_CONTROL_MODE: Final = CONTROL_MODE_SHADOW
DEFAULT_CONTROL_SURPLUS_THRESHOLD_KW: Final = 0.45
# A segment carries the heater's whole state and the last one holds, so the
# owner's usual Heat Pump must be allowed, and grants raise only in it. A
# single-mode cut-over (spec 1.2.2) is the owner's choice in the options.
DEFAULT_CONTROL_ALLOWED_MODES: Final = ("heat_pump", "energy_saver")
DEFAULT_CONTROL_ASSISTED_MODE: Final = "energy_saver"
DEFAULT_CONTROL_MIN_RUN_BEFORE_LOWER_MIN: Final = 120
# The unit tested accepted and read back a list of 32 entries (spec
# section 8); larger lists are untested. The default stays at the library's
# documented 16, and the option goes no higher than what was measured.
DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT: Final = 16
DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE: Final = 2
MAX_CONTROL_RESERVATION_ENTRY_LIMIT: Final = 32

# Keys into hass.data[DOMAIN][entry_id].
DATA_PLATFORMS: Final = "platforms"
DATA_CONTROL: Final = "control"


def control_enabled(entry: Any) -> bool:
    """Whether the external control feature is switched on for an entry.

    Reads the option only. Nothing of the feature is imported or built for
    an entry that has it off. Only the boolean True counts: the feature
    writes to a water heater, so a truthy string or a mock is not enough.
    """
    return entry.options.get(CONF_CONTROL_ENABLED, False) is True


def control_feature(hass: Any, entry: Any) -> Any | None:
    """Return the running control feature for an entry, or None.

    Typed loosely on purpose: the feature's class lives in the `control`
    subpackage, and naming it here would import that package on every path.
    """
    return hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(DATA_CONTROL)
