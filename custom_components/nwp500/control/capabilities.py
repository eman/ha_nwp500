"""The capability declaration (spec section 4.1, issue #158).

Built from the options and the device's feature data: what runs, and how
entries are budgeted.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from nwp500.temperature import HalfCelsius

from ..vocabulary import (
    CONF_CONTROL_MODE,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    CONTROL_MODE_NAMES,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
)
from .intent import (
    COMMAND_KEYS,
    COMMAND_RANGES,
    PROTOCOL_VERSIONS,
    SUPPORTED_PROTOCOLS,
)

# The device's setpoint resolution: reservation params and setpoints are
# whole half-degrees Celsius.
SETPOINT_RESOLUTION_C = 0.5

# How far ahead an entry may be programmed. A weekly entry cannot express a
# date, so an entry seven or more days away would fire a week early; this
# leaves a day's margin (spec section 5.3).
HORIZON = timedelta(hours=144)

# How far ahead a change needed now is written as an entry (section 5.2).
NEAR_TERM_LEAD = timedelta(minutes=2)
# How long after a moment the plan changes on its own the feature wakes
# to act on it: never at the moment itself.
WAKE_AFTER = timedelta(seconds=1)


@dataclass(frozen=True)
class Capabilities:
    """The declaration."""

    mode: str
    entry_limit: int
    entry_reserve: int
    feature_version: str
    # The heater's setpoint range, raw half-degrees Celsius inclusive;
    # None until its features are read.
    setpoint_range: tuple[int, int] | None = None
    # Live, and so outside the version: it changes as entries fire.
    entries_available: int | None = None

    def as_attributes(self) -> dict[str, Any]:
        """The declaration as the capability entity's attributes."""
        attributes = self._declared()
        attributes["entries_available"] = self.entries_available
        attributes["version"] = self.version
        return attributes

    def _declared(self) -> dict[str, Any]:
        """The attributes the version is computed over."""
        return {
            "protocols": list(SUPPORTED_PROTOCOLS),
            "protocol_versions": list(PROTOCOL_VERSIONS),
            "feature_version": self.feature_version,
            "mode": self.mode,
            "setpoint_resolution_c": SETPOINT_RESOLUTION_C,
            "horizon_h": int(HORIZON.total_seconds() // 3600),
            "near_term_lead_min": int(NEAR_TERM_LEAD.total_seconds() // 60),
            "entry_limit": self.entry_limit,
            "entry_reserve": self.entry_reserve,
            "modes": list(CONTROL_MODE_NAMES),
            "setpoint": _setpoint(self.setpoint_range),
            "commands": _commands(),
        }

    @property
    def version(self) -> str:
        """A short hash of the declaration; changes whenever it does.

        The live entry count is left out: it moves with every entry that
        fires.
        """
        payload = json.dumps(self._declared(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:8]


def setpoint_range(features: Any) -> tuple[int, int] | None:
    """The heater's setpoint range, raw half-degrees Celsius, inclusive.

    From its reported features; None until they are read.
    """
    low = getattr(features, "dhw_temperature_min_raw", None)
    high = getattr(features, "dhw_temperature_max_raw", None)
    if (
        isinstance(low, int)
        and isinstance(high, int)
        and not isinstance(low, bool)
        and not isinstance(high, bool)
    ):
        return low, high
    return None


def _setpoint(limits: tuple[int, int] | None) -> dict[str, float] | None:
    """The heater's range in both scales, as a segment may give it."""
    if limits is None:
        return None
    low, high = HalfCelsius(limits[0]), HalfCelsius(limits[1])
    return {
        "min_f": round(low.to_fahrenheit(), 1),
        "max_f": round(high.to_fahrenheit(), 1),
        "min_c": round(low.to_celsius(), 1),
        "max_c": round(high.to_celsius(), 1),
    }


def _commands() -> dict[str, dict[str, dict[str, Any]]]:
    """Each command's keys: their types, and the whole numbers' ranges."""
    commands: dict[str, dict[str, dict[str, Any]]] = {}
    for name, keys in COMMAND_KEYS.items():
        described: dict[str, dict[str, Any]] = {}
        for key, kind in keys.items():
            spec: dict[str, Any] = {
                "type": "integer" if kind is int else "boolean"
            }
            if (limits := COMMAND_RANGES.get((name, key))) is not None:
                spec["min"], spec["max"] = limits
            described[key] = spec
        commands[name] = described
    return commands


def build_capabilities(
    options: Mapping[str, Any],
    *,
    feature_version: str,
    setpoint_range: tuple[int, int] | None = None,
    entries_available: int | None = None,
) -> Capabilities:
    """Build the declaration from the options."""
    return Capabilities(
        mode=str(options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)),
        entry_limit=int(
            options.get(
                CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
                DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
            )
        ),
        entry_reserve=int(
            options.get(
                CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
                DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
            )
        ),
        feature_version=feature_version,
        setpoint_range=setpoint_range,
        entries_available=entries_available,
    )
