"""The capability declaration (spec section 4.1, issue #158).

Built from the options, the device's feature data and the entity registry.
The declaration is what a scheduler reads to know what it may ask for, and
what the per-directive checks are made against.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nwp500.temperature import HalfCelsius

from ..const import (
    CONF_CONTROL_ALLOWED_MODES,
    CONF_CONTROL_ASSISTED_MODE,
    CONF_CONTROL_BASELINE,
    CONF_CONTROL_DAILY_REVERT_TIME,
    CONF_CONTROL_HOLD_OFF_MARGIN_F,
    CONF_CONTROL_HOLD_OFF_SUPPORTED,
    CONF_CONTROL_LIVE_TYPES,
    CONF_CONTROL_MIN_RUN_BEFORE_STOP_MIN,
    CONF_CONTROL_MODE,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    CONF_CONTROL_SETPOINT_MAX_F,
    CONF_CONTROL_SETPOINT_MIN_F,
    CONF_CONTROL_SURPLUS_ENTITY,
    CONF_CONTROL_TOU_OFF_FOR_MODE,
    CONTROL_MODE_LIVE,
    DEFAULT_CONTROL_ALLOWED_MODES,
    DEFAULT_CONTROL_ASSISTED_MODE,
    DEFAULT_CONTROL_DAILY_REVERT_TIME,
    DEFAULT_CONTROL_HOLD_OFF_MARGIN_F,
    DEFAULT_CONTROL_MIN_RUN_BEFORE_STOP_MIN,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
)
from .intent import SUPPORTED_PROTOCOLS

# The device's setpoint resolution: reservation params and setpoints are
# whole half-degrees Celsius.
SETPOINT_RESOLUTION_C = 0.5

# The transient dip the upper probe shows during a draw without the tank
# being depleted, measured on the NWP500: 3.4 degF sustained for about
# 3 minutes. A consumer must not read it as depletion.
DELIVERY_TEMPERATURE_DIP_F = 3.4
DELIVERY_TEMPERATURE_DIP_MIN = 3

# Whether a setpoint write is used to stop or extend a running cycle. True
# on the NWP500: a setpoint lowered well below the upper tank stopped the
# compressor within 5 s (nwp500-python, "What starts a recovery").
SETPOINT_CHANGE_MID_CYCLE = True


@dataclass(frozen=True)
class Capabilities:
    """The declaration, with the bounds in device resolution for checks."""

    mode: str
    live_types: tuple[str, ...]
    supported_directives: tuple[str, ...]
    setpoint_min_raw: int | None
    setpoint_max_raw: int | None
    hold_off_margin_f: float
    allowed_modes: tuple[str, ...]
    assisted_mode: str
    tou_off_for_mode: bool
    min_run_before_stop_min: int
    reservation_entry_limit: int
    reservation_entry_reserve: int
    daily_revert_time: str
    feature_version: str
    telemetry: dict[str, Any]
    baseline: dict[str, Any] | None

    def as_attributes(self) -> dict[str, Any]:
        """The declaration as the capability entity's attributes."""
        attributes: dict[str, Any] = {
            "protocols": list(SUPPORTED_PROTOCOLS),
            "feature_version": self.feature_version,
            "mode": self.mode,
            "live_types": list(self.live_types),
            "supported_directives": list(self.supported_directives),
            "hold_off_margin_f": self.hold_off_margin_f,
            "setpoint_resolution_c": SETPOINT_RESOLUTION_C,
            "allowed_modes": list(self.allowed_modes),
            "assisted_mode": self.assisted_mode,
            "telemetry": dict(self.telemetry),
            "tou_off_for_mode": self.tou_off_for_mode,
            "min_run_before_stop_min": self.min_run_before_stop_min,
            "reservation_entry_limit": self.reservation_entry_limit,
            "reservation_entry_reserve": self.reservation_entry_reserve,
            "daily_revert_time": self.daily_revert_time,
            "setpoint_change_mid_cycle": SETPOINT_CHANGE_MID_CYCLE,
            "baseline": self.baseline,
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
        attributes["version"] = self.version
        return attributes

    @property
    def version(self) -> str:
        """A short hash of the declaration; changes whenever it does."""
        attributes = self.as_attributes_without_version()
        payload = json.dumps(attributes, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:8]

    def as_attributes_without_version(self) -> dict[str, Any]:
        """The attributes the version is computed over."""
        attributes = dict(self.__dict__)
        attributes.pop("telemetry")
        # Entity ids are presentation, and change with a rename; the
        # constants they sit beside do not vary either.
        return attributes


def _raw_from_option_f(value: Any) -> int | None:
    if value is None:
        return None
    return int(HalfCelsius.from_fahrenheit(float(value)).raw_value)


def _raw_from_feature(features: Any, name: str) -> int | None:
    raw = getattr(features, name, None) if features is not None else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def build_capabilities(
    options: Mapping[str, Any],
    *,
    features: Any,
    feature_version: str,
    telemetry: Mapping[str, str | None],
) -> Capabilities:
    """Build the declaration from options, feature data and entity ids.

    `features` is the device's `DeviceFeature`, or None before it arrives.
    `telemetry` maps `delivery_temperature`, `compressor_running` and
    `power` to their entity ids, or None where the entity is not
    registered.
    """
    mode = str(options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE))
    live_types = tuple(options.get(CONF_CONTROL_LIVE_TYPES, ()))
    if mode != CONTROL_MODE_LIVE:
        live_types = ()

    supported = ["charge", "mode"]
    if options.get(CONF_CONTROL_HOLD_OFF_SUPPORTED, False):
        supported.append("hold_off")
    if options.get(CONF_CONTROL_SURPLUS_ENTITY):
        supported.append("surplus_grant")

    setpoint_min_raw = _raw_from_option_f(
        options.get(CONF_CONTROL_SETPOINT_MIN_F)
    )
    if setpoint_min_raw is None:
        setpoint_min_raw = _raw_from_feature(
            features, "dhw_temperature_min_raw"
        )
    setpoint_max_raw = _raw_from_option_f(
        options.get(CONF_CONTROL_SETPOINT_MAX_F)
    )
    if setpoint_max_raw is None:
        setpoint_max_raw = _raw_from_feature(
            features, "dhw_temperature_max_raw"
        )

    allowed_modes = tuple(
        options.get(CONF_CONTROL_ALLOWED_MODES, DEFAULT_CONTROL_ALLOWED_MODES)
    )
    assisted_mode = str(
        options.get(CONF_CONTROL_ASSISTED_MODE, DEFAULT_CONTROL_ASSISTED_MODE)
    )

    return Capabilities(
        mode=mode,
        live_types=live_types,
        supported_directives=tuple(supported),
        setpoint_min_raw=setpoint_min_raw,
        setpoint_max_raw=setpoint_max_raw,
        hold_off_margin_f=float(
            options.get(
                CONF_CONTROL_HOLD_OFF_MARGIN_F,
                DEFAULT_CONTROL_HOLD_OFF_MARGIN_F,
            )
        ),
        allowed_modes=allowed_modes,
        assisted_mode=assisted_mode,
        tou_off_for_mode=bool(
            options.get(CONF_CONTROL_TOU_OFF_FOR_MODE, False)
        ),
        min_run_before_stop_min=int(
            options.get(
                CONF_CONTROL_MIN_RUN_BEFORE_STOP_MIN,
                DEFAULT_CONTROL_MIN_RUN_BEFORE_STOP_MIN,
            )
        ),
        reservation_entry_limit=int(
            options.get(
                CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
                DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
            )
        ),
        reservation_entry_reserve=int(
            options.get(
                CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
                DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
            )
        ),
        daily_revert_time=str(
            options.get(
                CONF_CONTROL_DAILY_REVERT_TIME,
                DEFAULT_CONTROL_DAILY_REVERT_TIME,
            )
        ),
        feature_version=feature_version,
        telemetry={
            **dict(telemetry),
            "delivery_temperature_dip_f": DELIVERY_TEMPERATURE_DIP_F,
            "delivery_temperature_dip_min": DELIVERY_TEMPERATURE_DIP_MIN,
        },
        baseline=options.get(CONF_CONTROL_BASELINE),
    )
