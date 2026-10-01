"""The capability declaration (spec section 4.1, issue #158).

Built from the options and the device's feature data: what runs, and how
entries are budgeted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..const import (
    CONF_CONTROL_MODE,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
)
from .intent import (
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


@dataclass(frozen=True)
class Capabilities:
    """The declaration, with the bounds in device resolution for checks."""

    mode: str
    # The heater's own range: what `"min"` resolves to. Not declared.
    setpoint_min_raw: int | None
    setpoint_max_raw: int | None
    entry_limit: int
    entry_reserve: int
    feature_version: str
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
        }

    @property
    def version(self) -> str:
        """A short hash of the declaration; changes whenever it does.

        The live entry count is left out: it moves with every entry that
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

    `"min"` resolves to its floor. The adapter checks no setpoint against
    it: the heater clamps what it is given.
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
    entries_available: int | None = None,
) -> Capabilities:
    """Build the declaration from the options and the feature data.

    `features` is the device's `DeviceFeature`, or None before it arrives.
    """
    setpoint_min_raw, setpoint_max_raw = _bounds(features)

    return Capabilities(
        mode=str(options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)),
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
        entries_available=entries_available,
    )
