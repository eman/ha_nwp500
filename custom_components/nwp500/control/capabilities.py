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
    CONF_CONTROL_ALLOWED_MODES,
    CONF_CONTROL_ASSISTED_MODE,
    CONF_CONTROL_LIVE_GRANTS,
    CONF_CONTROL_LIVE_SEGMENTS,
    CONF_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
    CONF_CONTROL_MODE,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    CONF_CONTROL_SETPOINT_MAX_F,
    CONF_CONTROL_SETPOINT_MIN_F,
    CONF_CONTROL_SURPLUS_ENTITY,
    DEFAULT_CONTROL_ALLOWED_MODES,
    DEFAULT_CONTROL_ASSISTED_MODE,
    DEFAULT_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
)
from .intent import SUPPORTED_PROTOCOLS

# The device's setpoint resolution: reservation params and setpoints are
# whole half-degrees Celsius.
SETPOINT_RESOLUTION_C = 0.5

# How far ahead an entry may be programmed. A weekly entry cannot express a
# date, so an entry seven or more days away would fire a week early; this
# leaves a day's margin (spec section 5.3).
HORIZON = timedelta(hours=144)

# How far ahead a change needed now is written as an entry (section 5.2).
NEAR_TERM_LEAD = timedelta(minutes=2)

# Surplus grant timing (section 5.7).
SURPLUS_ON_BEFORE_RAISE = timedelta(minutes=10)
SURPLUS_OFF_BEFORE_LOWER = timedelta(minutes=15)

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
    live_grants: bool
    setpoint_min_raw: int | None
    setpoint_max_raw: int | None
    allowed_modes: tuple[str, ...]
    assisted_mode: str
    entry_limit: int
    entry_reserve: int
    grants_supported: bool
    min_run_before_lower_min: int
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
            "feature_version": self.feature_version,
            "mode": self.mode,
            "live": {
                "segments": self.live_segments,
                "grants": self.live_grants,
            },
            "setpoint_resolution_c": SETPOINT_RESOLUTION_C,
            "allowed_modes": list(self.allowed_modes),
            "assisted_mode": self.assisted_mode,
            "horizon_h": int(HORIZON.total_seconds() // 3600),
            "near_term_lead_min": int(NEAR_TERM_LEAD.total_seconds() // 60),
            "entry_limit": self.entry_limit,
            "entry_reserve": self.entry_reserve,
            "grants_supported": self.grants_supported,
            "grant_rules": {
                "surplus_on_before_raise_min": int(
                    SURPLUS_ON_BEFORE_RAISE.total_seconds() // 60
                ),
                "surplus_off_before_lower_min": int(
                    SURPLUS_OFF_BEFORE_LOWER.total_seconds() // 60
                ),
                "min_run_before_lower_min": self.min_run_before_lower_min,
            },
            "owner_program": self.owner_program,
            "lower_trigger_f": LOWER_TRIGGER_F,
            "setpoint_write_starts_recovery": True,
            "setpoint_write_stops_compressor": True,
            "entry_mode_in_tou_window": "held",
            # Measured on the unit tested (spec section 8).
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


def _raw_from_option_f(value: Any) -> int | None:
    if value is None:
        return None
    return int(HalfCelsius.from_fahrenheit(float(value)).raw_value)


def _raw_from_feature(features: Any, name: str) -> int | None:
    raw = getattr(features, name, None) if features is not None else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _bounds(
    options: Mapping[str, Any], features: Any
) -> tuple[int | None, int | None]:
    """The setpoint bounds: the device's range, tightened by the options.

    An option can only narrow the range. A minimum below the device's, or a
    maximum above it, would let a plan carry setpoints the heater cannot
    apply, so the device's own limit wins. If the options leave no range at
    all within the device's, they are ignored.
    """
    device_min = _raw_from_feature(features, "dhw_temperature_min_raw")
    device_max = _raw_from_feature(features, "dhw_temperature_max_raw")
    option_min = _raw_from_option_f(options.get(CONF_CONTROL_SETPOINT_MIN_F))
    option_max = _raw_from_option_f(options.get(CONF_CONTROL_SETPOINT_MAX_F))

    low = (
        option_min
        if device_min is None
        else max(
            device_min, option_min if option_min is not None else device_min
        )
    )
    high = (
        option_max
        if device_max is None
        else min(
            device_max, option_max if option_max is not None else device_max
        )
    )
    if low is not None and high is not None and low > high:
        return device_min, device_max
    return low, high


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
    setpoint_min_raw, setpoint_max_raw = _bounds(options, features)

    return Capabilities(
        mode=str(options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)),
        live_segments=options.get(CONF_CONTROL_LIVE_SEGMENTS, False) is True,
        live_grants=options.get(CONF_CONTROL_LIVE_GRANTS, False) is True,
        setpoint_min_raw=setpoint_min_raw,
        setpoint_max_raw=setpoint_max_raw,
        allowed_modes=tuple(
            options.get(
                CONF_CONTROL_ALLOWED_MODES, DEFAULT_CONTROL_ALLOWED_MODES
            )
        ),
        assisted_mode=str(
            options.get(
                CONF_CONTROL_ASSISTED_MODE, DEFAULT_CONTROL_ASSISTED_MODE
            )
        ),
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
        grants_supported=bool(options.get(CONF_CONTROL_SURPLUS_ENTITY)),
        min_run_before_lower_min=int(
            options.get(
                CONF_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
                DEFAULT_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
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
