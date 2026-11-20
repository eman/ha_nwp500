"""The integration's names and ids that need no Home Assistant.

The external control engine and anything else that should run without
Home Assistant import these from here; `const` re-exports them.
"""

from typing import Any, Final

from nwp500.heater import MODES


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


# Friendly mode names to DhwOperationSetting IDs, the mode a status reports.
MODE_TO_DHW_ID: Final = {
    "heat_pump": 1,
    "electric": 2,
    "energy_saver": 3,
    "high_demand": 4,
    "vacation": 5,
    "power_off": 6,
}

# Friendly mode names to reservation entry mode IDs (the library's
# ReservationMode). These differ from the DHW operation setting from 5 on: an
# entry's 5 is Power Off, and an entry has no Vacation mode.
RESERVATION_MODE_IDS: Final = {
    "heat_pump": 1,
    "electric": 2,
    "energy_saver": 3,
    "high_demand": 4,
    "power_off": 5,
}

# The mode names a segment may use (spec section 3.4): the library's
# heater modes (`nwp500.heater.MODES`) a reservation entry can set. An
# entry has no Vacation; a plan's `vacation` command sets it.
CONTROL_MODE_NAMES: Final = tuple(
    mode.name for mode in MODES if mode.name != "vacation"
)

# External control's options, and their values and defaults.
CONF_CONTROL_MODE: Final = "control_mode"
CONF_CONTROL_RESERVATION_ENTRY_LIMIT: Final = "control_reservation_entry_limit"
CONF_CONTROL_RESERVATION_ENTRY_RESERVE: Final = (
    "control_reservation_entry_reserve"
)

CONTROL_MODE_SHADOW: Final = "shadow"
CONTROL_MODE_LIVE: Final = "live"
CONTROL_MODE_DISABLED: Final = "disabled"
DEFAULT_CONTROL_MODE: Final = CONTROL_MODE_SHADOW

# The unit tested accepted and read back a list of 32 entries (spec
# section 8); larger lists are untested. The default stays at the library's
# documented 16, and the option goes no higher than what was measured.
DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT: Final = 16
DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE: Final = 2
