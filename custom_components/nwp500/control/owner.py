"""The owner's program: what disabling the feature restores (section 5.1).

Declared the first time `live` is chosen. In shadow the feature uses a
provisional snapshot of the device, taken once and kept.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from nwp500.temperature import HalfCelsius

from .entries import firings
from .observed import Observed, raw_entry

_MATCH_FIELDS = ("week", "hour", "min", "mode", "param")
_DHW_ID_TO_MODE = {
    1: "heat_pump",
    2: "electric",
    3: "energy_saver",
    4: "high_demand",
    5: "vacation",
    6: "power_off",
}


@dataclass(frozen=True)
class OwnerProgram:
    """The owner's entries, reservation switch, mode and setpoint."""

    mode: str
    setpoint_raw: int
    reservations_enabled: bool
    entries: tuple[dict[str, int], ...] = ()
    declared: bool = False

    def is_owner_entry(self, entry: Mapping[str, int]) -> bool:
        """Whether an entry is one of the owner's, whatever its flag."""
        key = tuple(int(entry.get(f, 0)) for f in _MATCH_FIELDS)
        return any(
            key == tuple(int(own.get(f, 0)) for f in _MATCH_FIELDS)
            for own in self.entries
        )

    def state_now(self, now: datetime, tz: tzinfo) -> tuple[str, int]:
        """The state disabling writes.

        That is the state set by the owner's latest enabled entry, else the
        declared mode and setpoint. Owner entries only ran if the owner's
        reservation switch was on.
        """
        if self.reservations_enabled:
            latest: tuple[datetime, Mapping[str, int]] | None = None
            for entry in self.entries:
                if entry.get("enable") != 2:
                    continue
                fired = firings(entry, now - timedelta(days=7), now, tz)
                if fired and (latest is None or fired[-1] > latest[0]):
                    latest = (fired[-1], entry)
            if latest is not None:
                chosen = latest[1]
                mode = _DHW_ID_TO_MODE.get(int(chosen["mode"]), self.mode)
                return mode, int(chosen["param"])
        return self.mode, self.setpoint_raw

    @property
    def version(self) -> str:
        """A short hash of the program."""
        payload = json.dumps(self.as_document(), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:8]

    def as_document(self) -> dict[str, Any]:
        """For storage."""
        return {
            "mode": self.mode,
            "setpoint_raw": self.setpoint_raw,
            "reservations_enabled": self.reservations_enabled,
            "entries": [dict(e) for e in self.entries],
            "declared": self.declared,
        }

    def as_attributes(self) -> dict[str, Any]:
        """The `owner_program` attribute of the capability entity."""
        setpoint = HalfCelsius(self.setpoint_raw)
        return {
            "declared": self.declared,
            "mode": self.mode,
            "setpoint_f": round(setpoint.to_fahrenheit(), 1),
            "setpoint_c": round(setpoint.to_celsius(), 1),
            "reservations_enabled": self.reservations_enabled,
            "entries": [dict(e) for e in self.entries],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> OwnerProgram | None:
        """A stored program, or None if unusable."""
        try:
            return cls(
                mode=str(document["mode"]),
                setpoint_raw=int(document["setpoint_raw"]),
                reservations_enabled=bool(document["reservations_enabled"]),
                entries=tuple(
                    raw_entry(e) for e in document.get("entries", [])
                ),
                declared=bool(document.get("declared", False)),
            )
        except KeyError, TypeError, ValueError:
            return None

    @classmethod
    def from_observed(cls, observed: Observed) -> OwnerProgram | None:
        """A provisional snapshot of the device.

        None until the mode, setpoint and reservation list have been seen. A
        device in vacation or power-off is not a program anybody wants
        restored, so the snapshot waits for a normal mode.
        """
        if (
            observed.mode is None
            or observed.mode in ("vacation", "power_off")
            or observed.setpoint_raw is None
            or observed.reservations is None
        ):
            return None
        return cls(
            mode=observed.mode,
            setpoint_raw=observed.setpoint_raw,
            reservations_enabled=bool(observed.reservations_enabled),
            entries=tuple(observed.reservations),
        )
