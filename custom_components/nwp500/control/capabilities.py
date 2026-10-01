"""The capability declaration (spec section 4.1, issue #158).

Built from the options, the device's feature data, the owner's program and
the entity registry. It is what a scheduler reads to know what it may ask
for, and what the device's facts are.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from nwp500.temperature import HalfCelsius

from ..const import (
    CONF_CONTROL_MODE,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    CONTROL_MODE_NAMES,
    DEFAULT_CONTROL_ASSISTED_MODE,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
    control_follows_plan,
)
from .intent import (
    GRANT_RULE_MIN_RUN,
    GRANT_RULE_SURPLUS_OFF,
    GRANT_RULE_SURPLUS_ON,
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

# Surplus grants are not supported: the adapter operates the water heater,
# and reads nothing about the home's power. Protocol 1 keeps the keys, with
# these fixed values, until grants leave the protocol (section 5.7).
_GRANT_RULES_DECLARED: dict[str, int] = {
    GRANT_RULE_SURPLUS_ON: 10,
    GRANT_RULE_SURPLUS_OFF: 15,
    GRANT_RULE_MIN_RUN: 120,
}
GRANT_RULE_RANGES: dict[str, tuple[int, int]] = {
    GRANT_RULE_SURPLUS_ON: (0, 60),
    GRANT_RULE_SURPLUS_OFF: (0, 60),
    GRANT_RULE_MIN_RUN: (0, 600),
}

# The lower-tank turn-on temperature, measured on the NWP500 at every
# setpoint from 107.6 to 149 degF outside a TOU window. It does not follow
# the setpoint, so a low setpoint cannot prevent this trigger.
LOWER_TRIGGER_F = 104.9

# The transient dip the upper probe shows during a draw without the tank
# being depleted, measured on the NWP500: 3.4 degF sustained for about
# 3 minutes. A consumer must not read it as depletion.
DELIVERY_TEMPERATURE_DIP_F = 3.4
DELIVERY_TEMPERATURE_DIP_MIN = 3


@dataclass(frozen=True)
class Capabilities:
    """The declaration, with the bounds in device resolution for checks."""

    mode: str
    live_segments: bool
    setpoint_min_raw: int | None
    setpoint_max_raw: int | None
    entry_limit: int
    entry_reserve: int
    feature_version: str
    owner_program: dict[str, Any] | None = None
    telemetry: dict[str, Any] = field(default_factory=dict)
    # Live, and so outside the version: it changes as entries fire.
    entries_available: int | None = None

    def as_attributes(self) -> dict[str, Any]:
        """The declaration as the capability entity's attributes."""
        attributes = self._declared()
        attributes["telemetry"] = dict(self.telemetry)
        attributes["entries_available"] = self.entries_available
        attributes["version"] = self.version
        return attributes

    def _declared(self) -> dict[str, Any]:
        """The attributes the version is computed over."""
        attributes: dict[str, Any] = {
            "protocols": list(SUPPORTED_PROTOCOLS),
            "protocol_versions": list(PROTOCOL_VERSIONS),
            "feature_version": self.feature_version,
            "mode": self.mode,
            "live": {
                "segments": self.live_segments,
                "grants": False,
            },
            "setpoint_resolution_c": SETPOINT_RESOLUTION_C,
            # Every mode a segment may name: the heater applies any of them.
            "allowed_modes": list(CONTROL_MODE_NAMES),
            # Not the heater's choice: fixed until it leaves the protocol.
            "assisted_mode": DEFAULT_CONTROL_ASSISTED_MODE,
            "horizon_h": int(HORIZON.total_seconds() // 3600),
            "near_term_lead_min": int(NEAR_TERM_LEAD.total_seconds() // 60),
            "entry_limit": self.entry_limit,
            "entry_reserve": self.entry_reserve,
            "grants_supported": False,
            "grant_rules": dict(_GRANT_RULES_DECLARED),
            "grant_rule_ranges": {
                rule: list(bounds) for rule, bounds in GRANT_RULE_RANGES.items()
            },
            "owner_program": self.owner_program,
            "lower_trigger_f": LOWER_TRIGGER_F,
            "setpoint_write_starts_recovery": True,
            "setpoint_write_stops_compressor": True,
            # Measured on the unit tested (spec section 8). An entry's mode
            # inside a TOU window is held and applied at the window's end.
            "entry_mode_in_tou_window": "held",
            "list_write_starts_recovery": False,
            "unchanged_entry_starts_recovery": False,
            "entries_fire_when_powered_off": True,
            "entries_fire_in_vacation": False,
        }
        for name, raw in (
            ("min", self.setpoint_min_raw),
            ("max", self.setpoint_max_raw),
        ):
            if raw is None:
                continue
            value = HalfCelsius(raw)
            attributes[f"setpoint_{name}_f"] = round(value.to_fahrenheit(), 1)
            attributes[f"setpoint_{name}_c"] = round(value.to_celsius(), 1)
        return attributes

    @property
    def version(self) -> str:
        """A short hash of the declaration; changes whenever it does.

        Entity ids and the live entry count are left out: a rename is not a
        change of declaration, and the count moves with every entry that
        fires.
        """
        payload = json.dumps(self._declared(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _raw_from_feature(features: Any, name: str) -> int | None:
    raw = getattr(features, name, None) if features is not None else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _bounds(features: Any) -> tuple[int | None, int | None]:
    """The heater's own setpoint range, as it reports it; None until then.

    Declared, and the floor `"min"` resolves to. The adapter checks no
    setpoint against it: the heater clamps what it is given.
    """
    return (
        _raw_from_feature(features, "dhw_temperature_min_raw"),
        _raw_from_feature(features, "dhw_temperature_max_raw"),
    )


def build_capabilities(
    options: Mapping[str, Any],
    *,
    features: Any,
    feature_version: str,
    telemetry: Mapping[str, str | None],
    owner_program: Mapping[str, Any] | None = None,
    entries_available: int | None = None,
) -> Capabilities:
    """Build the declaration from options, feature data and entity ids.

    `features` is the device's `DeviceFeature`, or None before it arrives.
    `telemetry` maps `delivery_temperature`, `compressor_running` and
    `power` to their entity ids, or None where the entity is not
    registered.
    """
    setpoint_min_raw, setpoint_max_raw = _bounds(features)

    return Capabilities(
        mode=str(options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)),
        live_segments=control_follows_plan(options),
        setpoint_min_raw=setpoint_min_raw,
        setpoint_max_raw=setpoint_max_raw,
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
        owner_program=dict(owner_program) if owner_program else None,
        telemetry={
            **dict(telemetry),
            "delivery_temperature_dip_f": DELIVERY_TEMPERATURE_DIP_F,
            "delivery_temperature_dip_min": DELIVERY_TEMPERATURE_DIP_MIN,
        },
        entries_available=entries_available,
    )
