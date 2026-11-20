"""Constants for the Navien NWP500 integration."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_ELECTRIC,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
)
from homeassistant.helpers.storage import STORAGE_DIR

from nwp500.enums import CurrentOperationMode, DhwOperationSetting

from .vocabulary import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_DISABLED,
    CONTROL_MODE_LIVE,
    CONTROL_MODE_SHADOW,
    get_enum_value,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


DOMAIN: Final = "nwp500"


# Configuration
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TOKEN_DATA: Final = "token_data"  # Token persistence
# The account's user_seq, kept beside the tokens: a start that restores them
# skips sign-in and gets no user info, and the MQTT client ID needs it.
CONF_USER_SEQ: Final = "user_seq"

# Default values
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

# Minimum seconds between reconnection attempts (prevents rapid retry loops)
MIN_RECONNECT_INTERVAL: Final = 30.0

# Entity staleness threshold
# Number of consecutive update cycles without fresh data before marking unavailable
STALE_DATA_THRESHOLD: Final = 5


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


# Temperature ranges (from nwp500-python documentation)
MIN_TEMPERATURE_F: Final = 80  # °F (minimum safe operating temperature)
MAX_TEMPERATURE_F: Final = 150  # °F (maximum supported by device)
MIN_TEMPERATURE_C: Final = 27  # °C (~80.6°F)
MAX_TEMPERATURE_C: Final = 65  # °C (~149°F)


# External control (issue #158). The option keys live here because the
# options flow is shared code; everything else about the feature is in the
# `control` subpackage, which is imported only when the feature is enabled.
CONF_CONTROL_ENABLED: Final = "control_enabled"
CONF_CONTROL_INTENT_ENTITY: Final = "control_intent_entity"
CONF_CONTROL_LIVE_SEGMENTS: Final = "control_live_segments"

# Options of earlier versions of the feature. They are dropped from an
# entry's options whenever the control form is saved.
CONTROL_OBSOLETE_OPTIONS: Final = (
    "control_live_types",
    "control_hold_off_supported",
    "control_hold_off_margin_f",
    "control_tou_off_for_mode",
    "control_min_run_before_stop_min",
    "control_daily_revert_time",
    "control_baseline",
    "control_live_grants",
    "control_surplus_entity",
    "control_surplus_threshold_kw",
    "control_min_run_before_lower_min",
    "control_live_segments",
    "control_assisted_mode",
    "control_setpoint_min_f",
    "control_setpoint_max_f",
    "control_allowed_modes",
    "control_owner_program",
)

CONTROL_MODES_SELECTABLE: Final = (CONTROL_MODE_SHADOW, CONTROL_MODE_DISABLED)

# Whether live mode can be chosen at all (issue #158, delivery step 5). Live
# writes the heater's reservation list. It was opened after the staged live
# cut-over on a real heater (spec section 8, the live trial, 2026-09-25).
# It is the kill switch: set it to False and the options form stops offering
# `live`, and a `live` option runs as shadow.
CONTROL_LIVE_AVAILABLE: Final = True


MAX_CONTROL_RESERVATION_ENTRY_LIMIT: Final = 32

# The Repairs issue raised while external control is on but failed to start.
ISSUE_CONTROL_START_FAILED: Final = "control_start_failed"
# The Repairs issue raised while external control's plan entity is missing.
ISSUE_CONTROL_PLAN_ENTITY_MISSING: Final = "control_plan_entity_missing"

# What external control leaves behind, known here so the off path can remove
# it without importing the feature: its entities' unique ids carry this
# marker after the MAC, and its state is kept under this storage key.
CONTROL_UNIQUE_ID_MARKER: Final = "_control_"


def control_storage_key(entry_id: str) -> str:
    """The storage key of an entry's external control state."""
    return f"{DOMAIN}.control.{entry_id}"


def control_enabled(entry: Any) -> bool:
    """Whether the external control feature is switched on for an entry.

    Reads the option only. Nothing of the feature is imported or built for
    an entry that has it off. Only the boolean True counts: the feature
    writes to a water heater, so a truthy string or a mock is not enough.
    """
    return entry.options.get(CONF_CONTROL_ENABLED, False) is True


def control_follows_plan(options: Any) -> bool:
    """Whether these options are live: the plan is written to the heater.

    Live writes the plan. An earlier version stored a separate switch for
    it; a `live` stored with that switch off wrote nothing, and still
    writes nothing until the form is saved again.
    """
    return (
        options.get(CONF_CONTROL_MODE) == CONTROL_MODE_LIVE
        and options.get(CONF_CONTROL_LIVE_SEGMENTS, True) is True
    )


def control_feature(entry: Any) -> Any | None:
    """Return the running control feature for an entry, or None.

    None too for an entry not loaded. Typed loosely on purpose: the
    feature's class lives in the `control` subpackage, and naming it here
    would import that package on every path.
    """
    return getattr(getattr(entry, "runtime_data", None), "control", None)


# The hot water simulator (nwp500-python's `nwp500.sim`). Like external
# control, it is optional: its option keys live here because the config and
# options flows are shared code, and everything else is in the `simulator`
# subpackage, imported only when the simulator is on.
CONF_SIMULATOR_ENABLED: Final = "simulator_enabled"
# Per device, keyed by MAC: {"<mac>": {"air_entity": ..., ...}}.
CONF_SIMULATOR: Final = "simulator"
CONF_SIMULATOR_AIR_ENTITY: Final = "air_entity"
CONF_SIMULATOR_HUMIDITY_ENTITY: Final = "humidity_entity"
# A weather entity whose forecast projects the air beside the heater.
CONF_SIMULATOR_WEATHER_ENTITY: Final = "weather_entity"
# Hot water use, each optional: a probe on the cold supply, a binary
# sensor on while the household draws hot water (its circulation's water
# not counted), and the circulation pump's speed.
CONF_SIMULATOR_INLET_ENTITY: Final = "inlet_entity"
CONF_SIMULATOR_DEMAND_ENTITY: Final = "demand_entity"
CONF_SIMULATOR_CIRCULATION_ENTITY: Final = "circulation_speed_entity"
# The household's hot water draw flow, the circulation's water not in it:
# with it, the library replays the tank's layers from its history.
CONF_SIMULATOR_DRAW_FLOW_ENTITY: Final = "draw_flow_entity"
# What the draw flow's gallons count: water delivered at the taps, after
# any mixing valve (the library's default, stored as nothing), or tank
# water, as a meter on the heater's hot outlet counts it.
CONF_SIMULATOR_DRAW_FLOW_VOLUME: Final = "draw_flow_volume"
DRAW_FLOW_DELIVERED: Final = "delivered"
DRAW_FLOW_TANK: Final = "tank"
# A bath's and a shower's hot water, US gallons as stored; the form takes
# them in Home Assistant's volume unit. Without them, the library's.
CONF_SIMULATOR_BATH_GAL: Final = "bath_gal"
CONF_SIMULATOR_SHOWER_GAL: Final = "shower_gal"
# What the heater's mixing valve delivers, °F as stored; the form takes it
# in Home Assistant's temperature unit. Without it, no valve: the hot
# water is reckoned at the tank's temperature.
CONF_SIMULATOR_MIXING_VALVE_F: Final = "mixing_valve_f"

# The longest walk the simulator's actions run: a week. The answers grow
# less certain further out, and a week's walk already takes most of a
# second.
MAX_SIMULATE_HOURS: Final = 168

# The Repairs issue raised while the simulator is on but failed to start.
# One issue, worded by why: its packages are missing and Home Assistant
# installs none, their install failed (retried only after a restart), or
# anything else (retried on a reload).
ISSUE_SIMULATOR_START_FAILED: Final = "simulator_start_failed"
ISSUE_SIMULATOR_PACKAGES_MISSING: Final = "simulator_packages_missing"
ISSUE_SIMULATOR_INSTALL_FAILED: Final = "simulator_install_failed"

# What the simulator leaves behind, known here so the off path can remove
# it without importing the simulator.
SIMULATOR_UNIQUE_ID_MARKER: Final = "_simulator_"

# The simulator's calibration, per config entry: `<key>.<entry_id>`.
SIMULATOR_CALIBRATION_STORAGE_KEY: Final = f"{DOMAIN}.simulator_calibration"
SIMULATOR_CALIBRATION_STORAGE_VERSION: Final = 1
#: The heaters' own records of their statuses, for the calibration: a
#: directory per config entry under `.storage/nwp500/`.
SIMULATOR_RECORD_DIRECTORY: Final = "simulator_record"


def simulator_record_directory(hass: HomeAssistant, entry_id: str) -> Path:
    """Where a config entry's heaters' records are kept."""
    return Path(
        hass.config.path(
            STORAGE_DIR, DOMAIN, SIMULATOR_RECORD_DIRECTORY, entry_id
        )
    )


def simulator_heater_record_directory(
    hass: HomeAssistant, entry_id: str, mac_address: str
) -> Path:
    """Where one heater's record is kept."""
    slug = "".join(ch for ch in mac_address.lower() if ch.isalnum())
    return simulator_record_directory(hass, entry_id) / slug


# The simulator's entities an earlier version made, by domain and key
# (unique ID `<mac>_simulator_<key>`): removed when it starts. Bath ready
# is Hot water for baths above 0, and Hot water for its counts in words.
SIMULATOR_RETIRED_ENTITIES: Final = (
    ("binary_sensor", "bath_ready"),
    ("sensor", "hot_water_capacity"),
)

# The heater's entities the simulator reads the history of, by domain and
# key (unique ID `<mac>_<key>`), known here so setup can enable them
# without importing the simulator. A disabled one records nothing.
SIMULATOR_HEATER_ENTITIES: Final = (
    ("sensor", "last_status"),
    ("sensor", "tank_upper_temperature"),
    ("sensor", "tank_lower_temperature"),
    ("binary_sensor", "comp_use"),
    ("binary_sensor", "heat_upper_use"),
    ("binary_sensor", "heat_lower_use"),
    ("sensor", "current_heat_use"),
    ("binary_sensor", "dhw_use"),
    ("sensor", "hp_lower_off_temp_setting"),
    ("sensor", "operation_mode"),
    ("binary_sensor", "anti_legionella_operation_busy"),
    ("sensor", "error_code"),
    ("sensor", "current_inst_power"),
    ("sensor", "energy_to_setpoint"),
    ("sensor", "ambient_temperature"),
    ("sensor", "dhw_temperature_setting"),
)


def simulator_started_signal(entry_id: str) -> str:
    """The dispatcher signal sent once an entry's simulator runs."""
    return f"{DOMAIN}_simulator_started_{entry_id}"


def simulator_stopped_signal(entry_id: str) -> str:
    """The dispatcher signal sent when an entry's simulator stops."""
    return f"{DOMAIN}_simulator_stopped_{entry_id}"


def simulator_enabled(entry: Any) -> bool:
    """Whether the hot water simulator is switched on for an entry.

    Only the boolean True counts, as for external control.
    """
    return entry.options.get(CONF_SIMULATOR_ENABLED, False) is True


def simulator_device_options(entry: Any, mac_address: str) -> dict[str, Any]:
    """The simulator's options for one heater."""
    devices = entry.options.get(CONF_SIMULATOR) or {}
    return dict(devices.get(mac_address) or {})


def simulator_feature(entry: Any) -> Any | None:
    """Return the running simulator for an entry, or None.

    None too for an entry not loaded; typed loosely, as `control_feature`.
    """
    return getattr(getattr(entry, "runtime_data", None), "simulator", None)
