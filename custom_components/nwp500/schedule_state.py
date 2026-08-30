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

import copy
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
    """Return the raw reservation/TOU entries from a stored schedule.

    Deep-copied: `model_dump()` includes computed fields such as the `days`
    list, so a shallow copy would still share those nested objects with the
    coordinator's stored schedule and let an attribute reader corrupt it.
    """
    entries = schedule.get("reservation") or []
    return [copy.deepcopy(entry) for entry in entries]


def is_enabled(schedule: dict[str, Any]) -> bool:
    """Whether the schedule system is switched on at the device."""
    return schedule.get("reservation_use") == _DEVICE_BOOL_ON


# REST interval keys (camelCase, as `/device/tou` returns them) mapped to the
# snake_case names `TOUPeriod.model_dump()` produces, so a plan read over REST
# and a write confirmation read over MQTT are stored in the same shape.
_TOU_INTERVAL_KEYS = (
    ("week", "week"),
    ("start_hour", "startHour"),
    ("start_min", "startMinute"),
    ("end_hour", "endHour"),
    ("end_min", "endMinute"),
    ("price_min", "priceMin"),
    ("price_max", "priceMax"),
)

# TOUPeriod's own default, used when the API omits the field.
_DEFAULT_DECIMAL_POINT = 5


def tou_info_to_schedule(
    info: dict[str, Any], *, enabled: bool | None
) -> dict[str, Any]:
    """Convert a REST `TOUInfo` dump into the stored TOU schedule shape.

    The device has no MQTT read for its TOU program, so the plan is read
    from `/device/tou`, which nests intervals under a per-season block and
    keeps the raw camelCase protocol keys. The MQTT write confirmation
    (`TOUReservationSchedule`) is a flat list of snake_case periods, and the
    TOU schedule sensor reads that shape, so flatten the REST plan into it:
    each interval becomes one period carrying its season.

    `/device/tou` reports the programmed plan but not whether TOU is
    switched on -- that lives in the device status -- so `enabled` is passed
    in. `None` (status not yet received) is stored as "off", matching the
    device's own 0 value for "not enabled".

    Args:
        info: `TOUInfo.model_dump()` output.
        enabled: Whether TOU is currently switched on at the device.

    Returns:
        A schedule dict in the same shape the MQTT path stores, with the
        plan's identity (name, utility, ZIP code) kept alongside it.
    """
    entries: list[dict[str, Any]] = []
    for season_block in info.get("schedule") or []:
        if not isinstance(season_block, dict):
            continue
        season = int(season_block.get("season", 0) or 0)
        for interval in season_block.get("intervals") or []:
            if not isinstance(interval, dict):
                continue
            entry: dict[str, Any] = {"season": season}
            for name, alias in _TOU_INTERVAL_KEYS:
                entry[name] = int(interval.get(alias, 0) or 0)
            decimal_point = int(
                interval.get("decimalPoint", _DEFAULT_DECIMAL_POINT)
                or _DEFAULT_DECIMAL_POINT
            )
            entry["decimal_point"] = decimal_point
            # Computed fields TOUPeriod.model_dump() also emits, so an
            # attribute reader sees the same keys from either source.
            divisor = 10.0**decimal_point
            entry["start_time"] = (
                f"{entry['start_hour']:02d}:{entry['start_min']:02d}"
            )
            entry["end_time"] = (
                f"{entry['end_hour']:02d}:{entry['end_min']:02d}"
            )
            entry["decoded_price_min"] = entry["price_min"] / divisor
            entry["decoded_price_max"] = entry["price_max"] / divisor
            entries.append(entry)

    return {
        "reservation_use": _DEVICE_BOOL_ON if enabled else 0,
        "reservation": entries,
        "enabled": bool(enabled),
        "name": info.get("name", ""),
        "utility": info.get("utility", ""),
        "zip_code": info.get("zip_code", 0),
    }
