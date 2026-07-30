"""Canonical representations of the programmed schedules.

An external scheduler that programs the device needs to check what is
currently programmed without diffing entry lists by hand. These helpers
reduce a schedule to a stable, order-independent form and hash it, so a
consumer can compare a desired program against the device read-back with a
single string equality.

The canonical form mirrors `ReservationSchedule.canonical()` /
`TOUReservationSchedule.canonical()` in nwp500-python: `(enabled, sorted
tuple of raw protocol field tuples)`. It is computed here from the raw
dicts the coordinator stores, which are `model_dump()` output rather than
model instances.
"""

import hashlib
import json
from typing import Any

# Raw protocol fields per entry, in the order the library's canonical_key()
# uses. Keeping the order identical keeps the two representations aligned.
_RESERVATION_FIELDS = ("enable", "week", "hour", "min", "mode", "param")
_TOU_FIELDS = (
    "season",
    "week",
    "start_hour",
    "start_min",
    "end_hour",
    "end_min",
    "price_min",
    "price_max",
    "decimal_point",
)

# Device bool convention: 2 = on, 1 = off.
_DEVICE_BOOL_ON = 2


def _entry_key(
    entry: dict[str, Any], fields: tuple[str, ...]
) -> tuple[int, ...]:
    """Reduce one entry to its raw protocol fields."""
    return tuple(int(entry.get(field, 0) or 0) for field in fields)


def _canonical(
    schedule: dict[str, Any], fields: tuple[str, ...]
) -> tuple[bool, tuple[tuple[int, ...], ...]]:
    """Return `(enabled, sorted entry keys)` for a stored schedule."""
    enabled = schedule.get("reservation_use") == _DEVICE_BOOL_ON
    entries = schedule.get("reservation") or []
    keys = sorted(_entry_key(entry, fields) for entry in entries)
    return enabled, tuple(keys)


def reservation_canonical(
    schedule: dict[str, Any],
) -> tuple[bool, tuple[tuple[int, ...], ...]]:
    """Canonical form of a reservation schedule."""
    return _canonical(schedule, _RESERVATION_FIELDS)


def tou_canonical(
    schedule: dict[str, Any],
) -> tuple[bool, tuple[tuple[int, ...], ...]]:
    """Canonical form of a TOU schedule."""
    return _canonical(schedule, _TOU_FIELDS)


def schedule_hash(
    canonical: tuple[bool, tuple[tuple[int, ...], ...]],
) -> str:
    """Hash a canonical schedule into a short, stable identifier.

    Two schedules with the same entries hash the same regardless of the
    order the device reported them in, so a consumer can check whether what
    it wants is already programmed without comparing entry by entry.
    """
    payload = json.dumps(canonical, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def reservation_entries(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the raw reservation/TOU entries from a stored schedule."""
    entries = schedule.get("reservation") or []
    return [dict(entry) for entry in entries]


def is_enabled(schedule: dict[str, Any]) -> bool:
    """Whether the schedule system is switched on at the device."""
    return schedule.get("reservation_use") == _DEVICE_BOOL_ON
