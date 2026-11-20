"""Sensor platform for Navien NWP500 integration."""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import schedule_state
from .const import (
    control_feature,
    simulator_enabled,
    simulator_feature,
    simulator_started_signal,
)
from .coordinator import (
    NWP500ConfigEntry,
    NWP500DataUpdateCoordinator,
)
from .entity import NWP500Entity

if TYPE_CHECKING:
    from nwp500 import Device  # type: ignore[attr-defined]

_LOGGER = logging.getLogger(__name__)

# The library's unit names that are not Home Assistant's; a duration sensor
# rejects them.
_LIBRARY_UNITS: dict[str, str] = {
    "days": UnitOfTime.DAYS,
    "hours": UnitOfTime.HOURS,
    # The library's flow units, as Home Assistant names them.
    "LPM": UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
    "GPM": UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
}


@dataclass(frozen=True, kw_only=True)
class NWP500SensorEntityDescription(SensorEntityDescription):
    """A device status field's sensor.

    The field is `attr_name`, the key when not given; `value_fn` turns its
    value into the state, the value itself when not given. A field the
    status lacks, or None, is unknown.
    """

    attr_name: str | None = None
    value_fn: Callable[[Any], Any] | None = None

    @property
    def field(self) -> str:
        """The status field the sensor reads."""
        return self.attr_name or self.key


def _enum_name(value: Any) -> str:
    """A library enum's name; anything else as text."""
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value)


def _on_off(value: Any) -> str:
    return "On" if value else "Off"


def _coded(states: Mapping[int, str]) -> Callable[[Any], str | None]:
    """The translated state of a device code.

    A code with no state (the protocol's own "unknown", or one the
    library does not know) reads unknown.
    """

    def value_fn(value: Any) -> str | None:
        try:
            return states.get(int(value))
        except TypeError, ValueError:
            return None

    return value_fn


# The enum sensors' states, keyed by the device's code.
_CURRENT_HEAT_USE_STATES: Final = {
    0: "none",
    1: "heat_pump",
    2: "electric",
    3: "heat_pump_and_electric",
}
_TEMPERATURE_TYPE_STATES: Final = {1: "celsius", 2: "fahrenheit"}
_TEMP_FORMULA_TYPE_STATES: Final = {0: "asymmetric", 1: "standard"}
_DR_EVENT_STATUS_STATES: Final = {
    0: "not_applied",
    1: "run_normal",
    2: "shed",
    3: "load_up",
    4: "advance_load_up",
    5: "customer_peak_event",
    6: "grid_emergency",
}
_RECIRCULATION_OPERATION_MODE_STATES: Final = {
    1: "always_on",
    2: "weekly",
    3: "hot_button_only",
    4: "no_recirculation",
}


SENSOR_DESCRIPTIONS: tuple[NWP500SensorEntityDescription, ...] = (
    # Temperature sensors
    NWP500SensorEntityDescription(
        key="outside_temperature",
        translation_key="outside_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    NWP500SensorEntityDescription(
        key="tank_upper_temperature",
        translation_key="tank_upper_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    NWP500SensorEntityDescription(
        key="tank_lower_temperature",
        translation_key="tank_lower_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    NWP500SensorEntityDescription(
        key="discharge_temperature",
        translation_key="discharge_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="suction_temperature",
        translation_key="suction_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="evaporator_temperature",
        translation_key="evaporator_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="ambient_temperature",
        translation_key="ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # The simulator reads its history (SIMULATOR_HEATER_ENTITIES).
    NWP500SensorEntityDescription(
        key="dhw_temperature",
        translation_key="dhw_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    NWP500SensorEntityDescription(
        key="dhw_temperature_2",
        translation_key="dhw_temperature_2",
        attr_name="dhw_temperature2",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="current_inlet_temperature",
        translation_key="current_inlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="freeze_protection_temperature",
        translation_key="freeze_protection_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="target_super_heat",
        translation_key="target_super_heat",
        device_class=SensorDeviceClass.TEMPERATURE_DELTA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="current_super_heat",
        translation_key="current_super_heat",
        device_class=SensorDeviceClass.TEMPERATURE_DELTA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    # Power and energy sensors
    NWP500SensorEntityDescription(
        key="current_inst_power",
        translation_key="current_inst_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
    ),
    # Energy drawable from the tank as useful hot water. This is the only one
    # of the three that behaves like a state of charge: the other two are both
    # measured from the setpoint, so they move when the setpoint moves even
    # though the water in the tank does not.
    NWP500SensorEntityDescription(
        key="usable_energy",
        translation_key="usable_energy",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_display_precision=0,
    ),
    # Energy needed to bring the tank from its current temperature up to the
    # setpoint. No device_class: a deficit is not stored energy.
    NWP500SensorEntityDescription(
        key="energy_to_setpoint",
        translation_key="energy_to_setpoint",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_display_precision=0,
    ),
    # The simulator reads its history (SIMULATOR_HEATER_ENTITIES).
    # Energy to recover a fully depleted tank to the current setpoint.
    NWP500SensorEntityDescription(
        key="full_recovery_energy",
        translation_key="full_recovery_energy",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
    ),
    # Percentage sensors
    NWP500SensorEntityDescription(
        key="dhw_charge_per",
        translation_key="dhw_charge_per",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    NWP500SensorEntityDescription(
        key="mixing_rate",
        translation_key="mixing_rate",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="fan_pwm",
        translation_key="fan_pwm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    # Signal strength
    NWP500SensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Status and error codes
    NWP500SensorEntityDescription(
        key="error_code",
        translation_key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_enum_name,
    ),
    NWP500SensorEntityDescription(
        key="sub_error_code",
        translation_key="sub_error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Flow rate sensors
    NWP500SensorEntityDescription(
        key="current_dhw_flow_rate",
        translation_key="current_dhw_flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="cumulated_dhw_flow_rate",
        translation_key="cumulated_dhw_flow_rate",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        entity_registry_enabled_default=False,
    ),
    # Fan sensors
    NWP500SensorEntityDescription(
        key="target_fan_rpm",
        translation_key="target_fan_rpm",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="RPM",
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="current_fan_rpm",
        translation_key="current_fan_rpm",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="RPM",
        entity_registry_enabled_default=False,
    ),
    # Vacation sensors
    NWP500SensorEntityDescription(
        key="vacation_day_setting",
        translation_key="vacation_day_setting",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="vacation_day_elapsed",
        translation_key="vacation_day_elapsed",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_registry_enabled_default=False,
    ),
    # Heat source sensor
    NWP500SensorEntityDescription(
        key="current_heat_use",
        translation_key="current_heat_use",
        device_class=SensorDeviceClass.ENUM,
        options=list(_CURRENT_HEAT_USE_STATES.values()),
        value_fn=_coded(_CURRENT_HEAT_USE_STATES),
    ),
    # Diagnostic sensors
    NWP500SensorEntityDescription(
        key="eev_step",
        translation_key="eev_step",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="current_state_num",
        translation_key="current_state_num",
        attr_name="current_statenum",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="smart_diagnostic",
        translation_key="smart_diagnostic",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="special_function_status",
        translation_key="special_function_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="fault_status_1",
        translation_key="fault_status_1",
        attr_name="fault_status1",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="fault_status_2",
        translation_key="fault_status_2",
        attr_name="fault_status2",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Operation mode sensors (these have custom value_fn handling)
    NWP500SensorEntityDescription(
        key="operation_mode",
        translation_key="operation_mode",
        value_fn=_enum_name,
    ),
    NWP500SensorEntityDescription(
        key="dhw_operation_setting",
        translation_key="dhw_operation_setting",
        value_fn=_enum_name,
    ),
    # DHW temperature settings
    NWP500SensorEntityDescription(
        key="dhw_target_temperature_setting",
        translation_key="dhw_target_temperature_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="dhw_temperature_setting",
        translation_key="dhw_temperature_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # The simulator reads its history (SIMULATOR_HEATER_ENTITIES).
    # Heat pump temperature settings
    NWP500SensorEntityDescription(
        key="hp_upper_on_temp_setting",
        translation_key="hp_upper_on_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="hp_lower_on_temp_setting",
        translation_key="hp_lower_on_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="hp_upper_off_temp_setting",
        translation_key="hp_upper_off_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="hp_lower_off_temp_setting",
        translation_key="hp_lower_off_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # The simulator reads its history (SIMULATOR_HEATER_ENTITIES).
    # Differential temperature settings (relative values, not absolute)
    # Note: device_class is intentionally omitted from differential temp sensors.
    # Home Assistant applies absolute temperature conversions (including offset) to
    # entities with device_class: temperature, which is incorrect for relative values.
    # Differential sensors represent the difference between two temperatures, not
    # absolute values, so the conversion would produce invalid results.
    NWP500SensorEntityDescription(
        key="hp_upper_on_diff_temp_setting",
        translation_key="hp_upper_on_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="hp_lower_on_diff_temp_setting",
        translation_key="hp_lower_on_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="hp_upper_off_diff_temp_setting",
        translation_key="hp_upper_off_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="hp_lower_off_diff_temp_setting",
        translation_key="hp_lower_off_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    # Electric heating temperature settings
    NWP500SensorEntityDescription(
        key="he_upper_on_temp_setting",
        translation_key="he_upper_on_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="he_lower_on_temp_setting",
        translation_key="he_lower_on_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="he_upper_off_temp_setting",
        translation_key="he_upper_off_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="he_lower_off_temp_setting",
        translation_key="he_lower_off_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="he_upper_on_diff_temp_setting",
        translation_key="he_upper_on_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="he_lower_on_diff_temp_setting",
        translation_key="he_lower_on_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="he_upper_off_diff_temp_setting",
        translation_key="he_upper_off_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="he_lower_off_diff_temp_setting",
        translation_key="he_lower_off_diff_temp_setting",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    # Other temperature settings
    NWP500SensorEntityDescription(
        key="heat_min_op_temperature",
        translation_key="heat_min_op_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="freeze_protection_temp_min",
        translation_key="freeze_protection_temp_min",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="freeze_protection_temp_max",
        translation_key="freeze_protection_temp_max",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_temp_setting",
        translation_key="recirculation_temp_setting",
        attr_name="recirc_temp_setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_temperature",
        translation_key="recirculation_temperature",
        attr_name="recirc_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_faucet_temperature",
        translation_key="recirculation_faucet_temperature",
        attr_name="recirc_faucet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    # Flow rate sensors
    NWP500SensorEntityDescription(
        key="recirculation_dhw_flow_rate",
        translation_key="recirculation_dhw_flow_rate",
        attr_name="recirc_dhw_flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
        entity_registry_enabled_default=False,
    ),
    # Operation time sensors
    NWP500SensorEntityDescription(
        key="cumulated_evaporator_fan_op_time",
        translation_key="cumulated_evaporator_fan_op_time",
        attr_name="cumulated_op_time_eva_fan",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_registry_enabled_default=False,
    ),
    # Hours since the last air-filter reset (nwp500-python 9.5.0); the
    # lifetime total is evaporator_fan_run_time.
    # Anti-legionella and alarm settings
    NWP500SensorEntityDescription(
        key="anti_legionella_period",
        translation_key="anti_legionella_period",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="air_filter_alarm_period",
        translation_key="air_filter_alarm_period",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="air_filter_alarm_elapsed",
        translation_key="air_filter_alarm_elapsed",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_registry_enabled_default=False,
    ),
    # Diagnostic and status sensors
    NWP500SensorEntityDescription(
        key="temperature_type",
        translation_key="temperature_type",
        device_class=SensorDeviceClass.ENUM,
        options=list(_TEMPERATURE_TYPE_STATES.values()),
        entity_registry_enabled_default=False,
        value_fn=_coded(_TEMPERATURE_TYPE_STATES),
    ),
    NWP500SensorEntityDescription(
        key="temp_formula_type",
        translation_key="temp_formula_type",
        device_class=SensorDeviceClass.ENUM,
        options=list(_TEMP_FORMULA_TYPE_STATES.values()),
        entity_registry_enabled_default=False,
        value_fn=_coded(_TEMP_FORMULA_TYPE_STATES),
    ),
    NWP500SensorEntityDescription(
        key="dr_event_status",
        translation_key="dr_event_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(_DR_EVENT_STATUS_STATES.values()),
        entity_registry_enabled_default=False,
        value_fn=_coded(_DR_EVENT_STATUS_STATES),
    ),
    # 0 unknown, 1 off, 2 on: the device's tri-state flag, not hours.
    NWP500SensorEntityDescription(
        key="dr_override_status",
        translation_key="dr_override_status",
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_error_status",
        translation_key="recirculation_error_status",
        attr_name="recirc_error_status",
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_operation_reason",
        translation_key="recirculation_operation_reason",
        attr_name="recirc_operation_reason",
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_operation_mode",
        translation_key="recirculation_operation_mode",
        attr_name="recirc_operation_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(_RECIRCULATION_OPERATION_MODE_STATES.values()),
        entity_registry_enabled_default=False,
        value_fn=_coded(_RECIRCULATION_OPERATION_MODE_STATES),
    ),
    NWP500SensorEntityDescription(
        key="recirculation_model_type_code",
        translation_key="recirculation_model_type_code",
        attr_name="recirc_model_type_code",
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_sw_version",
        translation_key="recirculation_sw_version",
        attr_name="recirc_sw_version",
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_temperature_min",
        translation_key="recirculation_temperature_min",
        attr_name="recirc_temperature_min",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="recirculation_temperature_max",
        translation_key="recirculation_temperature_max",
        attr_name="recirc_temperature_max",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    NWP500SensorEntityDescription(
        key="program_reservation_type",
        translation_key="program_reservation_type",
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NWP500ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator = config_entry.runtime_data.coordinator

    entities: list[SensorEntity] = []
    # Devices that have a Recirculation Schedule sensor, so a later feature
    # update neither duplicates one nor misses a device.
    recirculation_sensor_macs: set[str] = set()

    # Add device-specific sensors
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        for description in SENSOR_DESCRIPTIONS:
            entities.append(
                NWP500Sensor(coordinator, mac_address, device, description)
            )
        # Programmed schedules, readable per device
        entities.append(
            NWP500ReservationScheduleSensor(coordinator, mac_address, device)
        )
        entities.append(
            NWP500TOUScheduleSensor(coordinator, mac_address, device)
        )
        # Only where the device reports recirculation scheduling: elsewhere
        # the schedule is never asked for, and the sensor would sit unknown
        # forever. The feature flags normally arrive with the device-info
        # reply before the platforms load; a device whose reply was lost
        # gets its sensor from _add_recirculation_sensors_when_supported
        # once a later reply lands.
        if coordinator.supports_recirculation_schedule(mac_address):
            entities.append(
                NWP500RecirculationScheduleSensor(
                    coordinator, mac_address, device
                )
            )
            recirculation_sensor_macs.add(mac_address)
        # Lifetime counters from the installer diagnostics query
        entities.extend(
            NWP500InstallerDiagnosticsSensor(
                coordinator, mac_address, device, description
            )
            for description in INSTALLER_DIAGNOSTICS_DESCRIPTIONS
        )
        # When this heater last answered with its status
        entities.append(
            NWP500LastStatusSensor(coordinator, mac_address, device)
        )
        # Cloud-recorded metadata, readable while the device is offline
        entities.append(
            NWP500CloudErrorSensor(coordinator, mac_address, device)
        )
        entities.extend(
            NWP500DescalingSensor(coordinator, mac_address, device, field)
            for field in ("descaling_start_time", "descaling_end_time")
        )

    # Add diagnostic telemetry sensors (one per integration, not per device)
    if coordinator.data:
        # Use first device's mac_address for device association
        first_mac = next(iter(coordinator.data.keys()))
        first_device = coordinator.data[first_mac]["device"]

        entities.extend(
            [
                NWP500LastResponseTimeSensor(
                    coordinator, first_mac, first_device
                ),
                NWP500MQTTRequestCountSensor(
                    coordinator, first_mac, first_device
                ),
                NWP500MQTTResponseCountSensor(
                    coordinator, first_mac, first_device
                ),
                NWP500MQTTConnectedSensor(coordinator, first_mac, first_device),
                NWP500ConsecutiveTimeoutsSensor(
                    coordinator, first_mac, first_device
                ),
            ]
        )

    # The external control feature's sensors, when it is on. The import is
    # inside the branch: nothing of the feature loads while it is off.
    if (feature := control_feature(config_entry)) is not None:
        from .control.sensor import create_control_sensors

        entities.extend(create_control_sensors(feature))

    async_add_entities(entities, True)

    # The hot water simulator's sensors, once it runs: it starts after the
    # platforms, in the background. Added on their own, with no update
    # before adding: their state comes from the simulator's runner, not
    # from a coordinator refresh. The import is inside: nothing of the
    # simulator loads while it is off, and by the signal it is loaded.
    if simulator_enabled(config_entry):

        @callback
        def _add_simulator_sensors() -> None:
            simulator = simulator_feature(config_entry)
            if simulator is None:
                return
            from .simulator.sensor import create_simulator_sensors

            async_add_entities(create_simulator_sensors(simulator))

        config_entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                simulator_started_signal(config_entry.entry_id),
                _add_simulator_sensors,
            )
        )

    def _add_recirculation_sensors_when_supported() -> None:
        """Create the schedule sensor for a device whose features came late.

        The initial device-info request is best-effort: a timeout or a
        dropped reply leaves `device_features` empty and setup continues.
        The periodic info request fills it in later, and the coordinator
        notifies listeners on every feature update, so this is where a
        supported device that missed the first pass gets its sensor.
        """
        late: list[SensorEntity] = []
        for mac_address, device_data in coordinator.data.items():
            if mac_address in recirculation_sensor_macs:
                continue
            if not coordinator.supports_recirculation_schedule(mac_address):
                continue
            recirculation_sensor_macs.add(mac_address)
            late.append(
                NWP500RecirculationScheduleSensor(
                    coordinator, mac_address, device_data["device"]
                )
            )
        if late:
            async_add_entities(late, True)

    config_entry.async_on_unload(
        coordinator.async_add_listener(
            _add_recirculation_sensors_when_supported
        )
    )


class NWP500Sensor(NWP500Entity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 sensor entity."""

    entity_description: NWP500SensorEntityDescription  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        description: NWP500SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device)
        self.entity_description = description  # pyright: ignore[reportIncompatibleVariableOverride]
        self._attr_unique_id = f"{mac_address}_{description.key}"

    @property
    def native_unit_of_measurement(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the native unit for this field.

        Prioritize the unit reported by the device status to ensure the value matches the unit.
        Fallback to the entity description default if status is unavailable.
        """
        status = self._status

        # 1. Try to get the actual unit from the device status
        if status:
            # Use the actual attribute name for unit lookup in the library,
            # not the entity key which might be different.
            unit = self.coordinator.get_field_unit_safe(
                status, self.entity_description.field
            )
            if unit:
                return _LIBRARY_UNITS.get(unit, unit)

        # 2. For temperature sensors, if we have a status but no specific unit field,
        # we might want to check the coordinator's unit system setting, but it's safer
        # to fall back to the static definition if the device doesn't explicitly tell us.

        # 3. Fallback to entity description unit
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the state of the sensor."""
        if not (status := self._status):
            return None
        description = self.entity_description
        value = getattr(status, description.field, None)
        if value is None or description.value_fn is None:
            return value
        return description.value_fn(value)


class NWP500DiagnosticSensor(NWP500Entity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Base class for diagnostic sensors that report coordinator telemetry."""

    _attr_entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        key: str,
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_diagnostic_{key}"
        self._attr_translation_key = key


class NWP500LastStatusSensor(NWP500Entity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """When this heater last answered with its status.

    One per heater, changing with every status it sends, so its history
    marks each time the heater was heard from and its silences show.
    The integration's last MQTT response is one sensor for every heater.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_status"

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_last_status"

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """When the heater's last status arrived."""
        device_data = self.device_data
        last_update = device_data.get("last_update") if device_data else None
        if last_update is None:
            return None
        return datetime.fromtimestamp(last_update, tz=UTC)

    @property
    @override
    def available(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Available while the heater is silent: its silences show here."""
        return self.coordinator.last_update_success


class NWP500TelemetrySensor(NWP500DiagnosticSensor):
    """A sensor about the connection itself.

    It stays available while the device is silent: an outage is exactly
    when the connection's state and counters are worth reading.
    """

    @property
    @override
    def available(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return True while the coordinator itself is healthy."""
        return self.coordinator.last_update_success


class NWP500LastResponseTimeSensor(NWP500TelemetrySensor):
    """Sensor showing timestamp of last MQTT response."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "last_response",
        )

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the timestamp of the last response."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        if telemetry["last_response_time"]:
            return datetime.fromtimestamp(
                telemetry["last_response_time"], tz=UTC
            )
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return additional attributes."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        attrs = {
            "last_request_id": telemetry["last_request_id"],
            "last_response_id": telemetry["last_response_id"],
        }
        if telemetry["last_request_time"] and telemetry["last_response_time"]:
            attrs["response_latency"] = (
                telemetry["last_response_time"] - telemetry["last_request_time"]
            )
        return attrs


class NWP500MQTTRequestCountSensor(NWP500TelemetrySensor):
    """Sensor showing total MQTT requests sent."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "request_count",
        )

    @property
    def native_value(self) -> int:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the total number of requests sent."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        value = telemetry.get("total_requests_sent")
        return int(value) if value is not None else 0


class NWP500MQTTResponseCountSensor(NWP500TelemetrySensor):
    """Sensor showing total MQTT responses received."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "response_count",
        )

    @property
    def native_value(self) -> int:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the total number of responses received."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        value = telemetry.get("total_responses_received")
        return int(value) if value is not None else 0


class NWP500MQTTConnectedSensor(NWP500TelemetrySensor):
    """Sensor showing MQTT connection status and duration."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "mqtt_status",
        )

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the connection status."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        return "connected" if telemetry["mqtt_connected"] else "disconnected"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return additional attributes."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        attrs = {}
        if telemetry["mqtt_connected_since"]:
            attrs["connected_since"] = datetime.fromtimestamp(
                telemetry["mqtt_connected_since"], tz=UTC
            )
            attrs["connected_duration_seconds"] = (
                time.time() - telemetry["mqtt_connected_since"]
            )
        return attrs


class NWP500ConsecutiveTimeoutsSensor(NWP500TelemetrySensor):
    """Sensor showing consecutive MQTT timeouts."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "consecutive_timeouts",
        )

    @property
    def native_value(self) -> int:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the number of consecutive timeouts."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        return int(telemetry.get("consecutive_timeouts", 0))


class NWP500ScheduleSensor(NWP500DiagnosticSensor):
    """Exposes a programmed schedule as readable entity state.

    The reservation and TOU schedules were previously reachable only as
    coordinator dicts and one-shot bus events, so an external scheduler had
    to run a WebSocket request/event dance and could not simply poll. The
    state is the entry count and the program itself is in the attributes,
    including a canonical hash for cheap desired-vs-programmed comparison.

    No state_class: the entry count is a unitless configuration value, not a
    measurement, and long-term statistics over it would be meaningless.
    """

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        key: str,
        store: str,
        canonical_fn: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Initialize the schedule sensor."""
        super().__init__(coordinator, mac_address, device, key)
        self._store = store
        self._canonical_fn = canonical_fn

    def _schedule(self) -> dict[str, Any] | None:
        """Return the stored schedule, or None if never fetched."""
        store: dict[str, dict[str, Any]] = getattr(
            self.coordinator, self._store
        )
        return store.get(self.mac_address)

    @property
    def native_value(self) -> int | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the number of programmed entries.

        None until the schedule has been read, so "not fetched yet" stays
        distinguishable from "device has no entries".
        """
        schedule = self._schedule()
        if schedule is None:
            return None
        return len(schedule_state.reservation_entries(schedule))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the programmed entries and a hash of the program."""
        schedule = self._schedule()
        if schedule is None:
            return {"entries": [], "enabled": None, "schedule_hash": None}

        return {
            "entries": schedule_state.reservation_entries(schedule),
            "enabled": schedule_state.is_enabled(schedule),
            "schedule_hash": schedule_state.schedule_hash(
                self._canonical_fn(schedule)
            ),
        }


class NWP500ReservationScheduleSensor(NWP500ScheduleSensor):
    """The programmed reservation schedule."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "reservation_schedule",
            "reservation_schedules",
            schedule_state.reservation_canonical,
        )


class NWP500TOUScheduleSensor(NWP500ScheduleSensor):
    """The programmed Time of Use schedule."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "tou_schedule",
            "tou_schedules",
            schedule_state.tou_canonical,
        )


class NWP500RecirculationScheduleSensor(NWP500ScheduleSensor):
    """The programmed recirculation pump schedule.

    Read with nwp500-python 9.4.0's recirculation schedule query. The
    entries have the reservation entry shape (enable/week/hour/min/mode/
    param), so the reservation canonical form applies unchanged.
    """

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "recirculation_schedule",
            "recirculation_schedules",
            schedule_state.reservation_canonical,
        )


@dataclass(frozen=True, kw_only=True)
class NWP500InstallerDiagnosticsDescription(SensorEntityDescription):
    """A lifetime counter: `field` of the installer diagnostics' `block`."""

    block: str
    field: str


INSTALLER_DIAGNOSTICS_DESCRIPTIONS: tuple[
    NWP500InstallerDiagnosticsDescription, ...
] = (
    # The two headline counters. Split lifetime energy by heat source is
    # what the Energy dashboard wants and nothing else on the device
    # measures it, so these are on by default and not filed as diagnostic.
    NWP500InstallerDiagnosticsDescription(
        key="lifetime_heat_pump_energy",
        translation_key="lifetime_heat_pump_energy",
        block="ts_data",
        field="cumulated_pwr_hp",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=None,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="lifetime_heat_element_energy",
        translation_key="lifetime_heat_element_energy",
        block="ts_data",
        field="cumulated_pwr_he",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=None,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="days_since_installation",
        translation_key="days_since_installation",
        block="ts_data",
        field="days_since_installation",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Component run times (hours) and start counts.
    NWP500InstallerDiagnosticsDescription(
        key="compressor_run_time",
        translation_key="compressor_run_time",
        block="ta_data",
        field="cumulated_op_time_comp",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="compressor_start_count",
        translation_key="compressor_start_count",
        block="ta_data",
        field="cumulated_op_num_comp",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="evaporator_fan_run_time",
        translation_key="evaporator_fan_run_time",
        block="ta_data",
        field="cumulated_op_time_eva_fan",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="evaporator_fan_start_count",
        translation_key="evaporator_fan_start_count",
        block="ta_data",
        field="cumulated_op_num_eva_fan",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="upper_element_run_time",
        translation_key="upper_element_run_time",
        block="ta_data",
        field="cumulated_op_time_uhe",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="upper_element_start_count",
        translation_key="upper_element_start_count",
        block="ta_data",
        field="cumulated_op_num_uhe",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="lower_element_run_time",
        translation_key="lower_element_run_time",
        block="ta_data",
        field="cumulated_op_time_lhe",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="lower_element_start_count",
        translation_key="lower_element_start_count",
        block="ta_data",
        field="cumulated_op_num_lhe",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="recirculation_pump_run_time",
        translation_key="recirculation_pump_run_time",
        block="ta_data",
        field="cumulated_op_time_recirc_pump",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="recirculation_pump_start_count",
        translation_key="recirculation_pump_start_count",
        block="ta_data",
        field="cumulated_op_num_recirc_pump",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Fault and protection event counts.
    NWP500InstallerDiagnosticsDescription(
        key="freeze_protection_event_count",
        translation_key="freeze_protection_event_count",
        block="ts_data",
        field="num_of_frost_protect_burn",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="dry_fire_event_count",
        translation_key="dry_fire_event_count",
        block="ts_data",
        field="cumulated_occ_num_dry_fire",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="condensate_overflow_event_count",
        translation_key="condensate_overflow_event_count",
        block="ts_data",
        field="cumulated_occ_num_con_ovr_flow",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="water_leak_event_count",
        translation_key="water_leak_event_count",
        block="ts_data",
        field="cumulated_occ_num_wtr_ovr_flow",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="heat_pump_error_count",
        translation_key="heat_pump_error_count",
        block="ts_data",
        field="cumulated_occ_num_hpo",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="abnormal_discharge_temperature_count",
        translation_key="abnormal_discharge_temperature_count",
        block="ts_data",
        field="cumulated_occ_num_ab_dis_tmp",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    NWP500InstallerDiagnosticsDescription(
        key="abnormal_suction_temperature_count",
        translation_key="abnormal_suction_temperature_count",
        block="ts_data",
        field="cumulated_occ_num_ab_suc_tmp",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


class NWP500InstallerDiagnosticsSensor(NWP500DiagnosticSensor):
    """One lifetime counter from the installer diagnostics query.

    The NaviLink app shows these on its installer screen: lifetime energy
    per heat source, component run times and start counts, fault event
    counts. The device reports them only when asked, so the value comes
    from the coordinator's `device_diagnostics` store, which setup and the
    periodic schedule refresh fill. Unknown until the first reply, so
    "not read yet" stays distinguishable from a counter at zero.
    """

    entity_description: NWP500InstallerDiagnosticsDescription  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        description: NWP500InstallerDiagnosticsDescription,
    ) -> None:
        """Initialize the sensor from its description."""
        super().__init__(coordinator, mac_address, device, description.key)
        self.entity_description = description  # pyright: ignore[reportIncompatibleVariableOverride]
        # The headline counters are not diagnostic: the description says.
        self._attr_entity_category = description.entity_category

    @property
    def native_value(self) -> int | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the counter, or None until diagnostics have been read."""
        diagnostics = self.coordinator.device_diagnostics.get(self.mac_address)
        if diagnostics is None:
            return None
        block = diagnostics.get(self.entity_description.block)
        if not isinstance(block, dict):
            return None
        value = block.get(self.entity_description.field)
        if value is None:
            return None
        try:
            return int(value)
        except TypeError, ValueError:
            return None


class NWP500CloudMetadataSensor(NWP500DiagnosticSensor):
    """Base for sensors fed by the REST device list rather than by MQTT.

    `NWP500Entity.available` goes False once the device stops answering
    over MQTT, which is the right rule for a sensor reading device status
    and the wrong one here: these values come from the cloud, which keeps
    answering while the device is offline. That is exactly when the
    recorded fault is worth reading, so availability follows the
    coordinator alone.
    """

    @property
    def available(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return True while the coordinator itself is healthy."""
        return self.coordinator.last_update_success


class NWP500CloudErrorSensor(NWP500CloudMetadataSensor):
    """The device's last recorded fault, as the cloud reports it.

    Distinct from the `error_code` sensor, which reflects the live MQTT
    status and so goes unavailable with the device: the cloud keeps this
    independently and still answers while the device is offline, reporting
    the fault as of the last time the device was heard from.
    """

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device, "cloud_error_code")

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the recorded error code by name, or None if not reported."""
        error = self.coordinator.get_device_error(self.mac_address)
        code = getattr(error, "error_code", None) if error else None
        if code is None:
            return None
        # An unknown code stays a plain int rather than an ErrorCode member.
        return str(getattr(code, "name", code))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return when the recorded fault occurred."""
        error = self.coordinator.get_device_error(self.mac_address)
        return {
            "occurred_at": getattr(error, "error_occurred_time", None)
            if error
            else None
        }


class NWP500DescalingSensor(NWP500CloudMetadataSensor):
    """One end of the descaling window the cloud records for the device.

    Reported as the cloud sends it. Both ends are unset on a device with no
    descaling scheduled or recorded, which is the common case, so these are
    disabled by default.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        field: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device, field)
        self._field = field

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the recorded timestamp, or None if none is recorded."""
        descaling = self.coordinator.get_device_descaling(self.mac_address)
        if not descaling:
            return None
        value = getattr(descaling, self._field, None)
        return str(value) if value is not None else None
