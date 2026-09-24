"""The owner's program: what disabling the feature restores (section 5.1).

Declared the first time `live` is chosen. In shadow the feature uses a
provisional snapshot of the device, taken once and kept.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any

from nwp500.temperature import HalfCelsius

from .entries import firings
from .observed import (
    DEVICE_BOOL_OFF,
    DEVICE_BOOL_ON,
    Observed,
    observe,
    raw_entry,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

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

    def restore(
        self, others: Iterable[tuple[dict[str, int], bool]]
    ) -> dict[str, Any]:
        """The list disabling writes (section 6.6).

        `others` are the device's entries the feature does not own, each
        with whether it is one of the owner's. The owner's get their own
        enable flag back, anyone else's are kept as read, and the
        reservation switch is set as the owner had it.
        """
        reservation: list[dict[str, int]] = []
        for entry, is_owner in others:
            kept = dict(entry)
            if is_owner:
                original = self._original(kept)
                if original is not None:
                    kept["enable"] = int(original.get("enable", kept["enable"]))
            reservation.append(kept)
        return {
            "reservation_use": DEVICE_BOOL_ON
            if self.reservations_enabled
            else DEVICE_BOOL_OFF,
            "reservation": reservation,
        }

    def _original(self, entry: Mapping[str, int]) -> Mapping[str, int] | None:
        key = tuple(int(entry.get(f, 0)) for f in _MATCH_FIELDS)
        return next(
            (
                own
                for own in self.entries
                if tuple(int(own.get(f, 0)) for f in _MATCH_FIELDS) == key
            ),
            None,
        )

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


_DAYS = (
    (128, "Sun"),
    (64, "Mon"),
    (32, "Tue"),
    (16, "Wed"),
    (8, "Thu"),
    (4, "Fri"),
    (2, "Sat"),
)
_MODE_LABELS = {
    "heat_pump": "Heat Pump",
    "electric": "Electric",
    "energy_saver": "Energy Saver",
    "high_demand": "High Demand",
    "vacation": "Vacation",
    "power_off": "Power off",
}


def _temperature(raw: int, celsius: bool) -> str:
    value = HalfCelsius(raw)
    if celsius:
        return f"{value.to_celsius():.1f} °C"
    return f"{value.to_fahrenheit():.1f} °F"


def describe(program: OwnerProgram, *, celsius: bool) -> str:
    """The owner's program as the going-live step shows it (6.3)."""
    lines = [
        f"- Mode: {_MODE_LABELS.get(program.mode, program.mode)}",
        f"- Setpoint: {_temperature(program.setpoint_raw, celsius)}",
        "- Reservations: " + ("on" if program.reservations_enabled else "off"),
    ]
    if not program.entries:
        lines.append("- Entries: none")
    for entry in program.entries:
        days = " ".join(
            name for bit, name in _DAYS if int(entry.get("week", 0)) & bit
        )
        mode = _DHW_ID_TO_MODE.get(int(entry.get("mode", 0)), "?")
        state = "on" if entry.get("enable") == DEVICE_BOOL_ON else "off"
        switched = (
            ", switched off while live"
            if entry.get("enable") == DEVICE_BOOL_ON
            else ""
        )
        lines.append(
            f"- Entry {days or '(no days)'} "
            f"{int(entry.get('hour', 0)):02d}:{int(entry.get('min', 0)):02d}, "
            f"{_MODE_LABELS.get(mode, mode)} "
            f"{_temperature(int(entry.get('param', 0)), celsius)}, "
            f"{state}{switched}"
        )
    return "\n".join(lines)


def declare_owner_programs(
    hass: HomeAssistant, entry: ConfigEntry, *, celsius: bool
) -> tuple[dict[str, dict[str, Any]] | None, str]:
    """Snapshot every heater of the entry as its declared owner's program.

    A heater whose device already holds the feature's list keeps the
    program declared before: a snapshot now would take the feature's own
    entries for the owner's. Returns the programs by MAC address, or None
    if a heater cannot be snapshotted yet (its mode, setpoint or list not
    read, or it is in vacation or powered off), and a summary to show.
    """
    # Imported here: the options flow calls this only while going live.
    from ..const import CONF_CONTROL_OWNER_PROGRAM, control_feature

    coordinator = entry.runtime_data
    feature = control_feature(hass, entry)
    previous = entry.options.get(CONF_CONTROL_OWNER_PROGRAM) or {}
    programs: dict[str, dict[str, Any]] = {}
    sections: list[str] = []
    complete = True
    for mac_address, data in (coordinator.data or {}).items():
        control = feature.devices.get(mac_address) if feature else None
        program: OwnerProgram | None = None
        if control is not None and control.holds_device:
            program = OwnerProgram.from_document(previous.get(mac_address, {}))
        if program is None:
            program = OwnerProgram.from_observed(
                observe(
                    data.get("status"),
                    coordinator.reservation_schedules.get(mac_address),
                )
            )
        if program is None:
            complete = False
            sections.append(
                f"**{mac_address}**: not readable yet (the mode, setpoint "
                "or reservation list has not been read, or the heater is "
                "in vacation or powered off)."
            )
            continue
        program = OwnerProgram(
            mode=program.mode,
            setpoint_raw=program.setpoint_raw,
            reservations_enabled=program.reservations_enabled,
            entries=program.entries,
            declared=True,
        )
        programs[mac_address] = program.as_document()
        sections.append(
            f"**{mac_address}**\n{describe(program, celsius=celsius)}"
        )
    if not programs:
        complete = False
    return (programs if complete else None), "\n\n".join(sections)
