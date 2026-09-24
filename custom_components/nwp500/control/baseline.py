"""The baseline: the configuration the heater is restored to.

Spec sections 4.1 and 6 (issue #158). The user declares it the first time
`live` is chosen. Until then shadow execution needs one to compute the
wanted state against, so a provisional baseline is taken from the device's
current settings and marked as such.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nwp500.temperature import HalfCelsius

from .observed import Observed

_ENTRY_FIELDS = ("enable", "week", "hour", "min", "mode", "param")


def raw_entry(entry: Mapping[str, Any]) -> dict[str, int]:
    """Only the protocol fields of a reservation entry."""
    return {field: int(entry.get(field, 0) or 0) for field in _ENTRY_FIELDS}


@dataclass(frozen=True)
class Baseline:
    """What the heater runs on when no directive is in force."""

    mode: str
    setpoint_raw: int
    tou_enabled: bool
    reservations_enabled: bool
    reservations: tuple[dict[str, int], ...] = ()
    provisional: bool = False

    @property
    def version(self) -> str:
        """A short hash; changes when the declared configuration does."""
        payload = json.dumps(self.as_document(), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:8]

    def as_document(self) -> dict[str, Any]:
        """The values themselves, as stored in the options."""
        return {
            "mode": self.mode,
            "setpoint_raw": self.setpoint_raw,
            "tou_enabled": self.tou_enabled,
            "reservations_enabled": self.reservations_enabled,
            "reservations": [dict(e) for e in self.reservations],
        }

    def as_attributes(self) -> dict[str, Any]:
        """The `baseline` attribute of the capability entity."""
        setpoint = HalfCelsius(self.setpoint_raw)
        return {
            "version": self.version,
            "provisional": self.provisional,
            "mode": self.mode,
            "setpoint_f": round(setpoint.to_fahrenheit(), 1),
            "setpoint_c": round(setpoint.to_celsius(), 1),
            "tou_enabled": self.tou_enabled,
            "reservations_enabled": self.reservations_enabled,
            "reservations": [dict(e) for e in self.reservations],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Baseline | None:
        """A declared baseline from the options, or None if unusable."""
        try:
            return cls(
                mode=str(document["mode"]),
                setpoint_raw=int(document["setpoint_raw"]),
                tou_enabled=bool(document["tou_enabled"]),
                reservations_enabled=bool(document["reservations_enabled"]),
                reservations=tuple(
                    raw_entry(e) for e in document.get("reservations", [])
                ),
            )
        except KeyError, TypeError, ValueError:
            return None

    @classmethod
    def from_observed(cls, observed: Observed) -> Baseline | None:
        """A provisional baseline from the device's current settings.

        None until the mode, setpoint and TOU state have been seen. A device
        in vacation or power-off is not a baseline anybody wants restored,
        so those wait for a normal mode.
        """
        if (
            observed.mode is None
            or observed.mode in ("vacation", "power_off")
            or observed.setpoint_raw is None
            or observed.tou_on is None
        ):
            return None
        return cls(
            mode=observed.mode,
            setpoint_raw=observed.setpoint_raw,
            tou_enabled=observed.tou_on,
            reservations_enabled=bool(observed.reservations_enabled),
            reservations=tuple(observed.reservations or ()),
            provisional=True,
        )
