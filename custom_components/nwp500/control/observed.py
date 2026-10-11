"""What the feature reads from the device (spec section 4.3).

One immutable snapshot per evaluation, built from the coordinator's status
and the stored reservation schedule, so the planner never touches Home
Assistant objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..const import MODE_TO_DHW_ID, get_enum_value

_DHW_ID_TO_MODE = {v: k for k, v in MODE_TO_DHW_ID.items()}

# Device booleans are 2 = on, 1 = off in the reservation payloads.
DEVICE_BOOL_ON = 2
DEVICE_BOOL_OFF = 1

ENTRY_FIELDS = ("enable", "week", "hour", "min", "mode", "param")


def raw_entry(entry: Mapping[str, Any]) -> dict[str, int]:
    """Only the protocol fields of a reservation entry."""
    return {field: int(entry.get(field, 0) or 0) for field in ENTRY_FIELDS}


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(get_enum_value(value))


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def mode_name(setting: Any) -> str | None:
    """The spec's name for a DHW operation setting value."""
    value = _int(get_enum_value(setting))
    return _DHW_ID_TO_MODE.get(value) if value is not None else None


@dataclass(frozen=True)
class Observed:
    """The device's state as far as the feature reads it."""

    # The DHW operation setting, as a spec mode name (heat_pump,
    # energy_saver, high_demand, electric, vacation, power_off).
    mode: str | None = None
    # The setpoint, in half-degrees Celsius.
    setpoint_raw: int | None = None
    tou_on: bool | None = None
    # The reservation list as the device last reported it; None until read.
    reservations_enabled: bool | None = None
    reservations: tuple[dict[str, int], ...] | None = None
    # What device commands set (section 3.7), to read them back.
    vacation_days: int | None = None
    anti_legionella_on: bool | None = None
    anti_legionella_period: int | None = None

    @property
    def schedule(self) -> dict[str, Any] | None:
        """The device's list in the stored-schedule shape, or None."""
        if self.reservations is None:
            return None
        return {
            "reservation_use": DEVICE_BOOL_ON
            if self.reservations_enabled
            else DEVICE_BOOL_OFF,
            "reservation": [dict(e) for e in self.reservations],
        }


def observe(
    status: Any,
    schedule: Mapping[str, Any] | None,
) -> Observed:
    """Build a snapshot from the coordinator's data."""
    mode = setpoint_raw = tou_on = None
    vacation_days = anti_legionella_on = anti_legionella_period = None
    if status is not None:
        mode = mode_name(getattr(status, "dhw_operation_setting", None))
        setpoint_raw = _int(
            getattr(status, "dhw_target_temperature_setting_raw", None)
        )
        tou_on = _bool(getattr(status, "tou_status", None))
        vacation_days = _int(getattr(status, "vacation_day_setting", None))
        anti_legionella_on = _bool(getattr(status, "anti_legionella_use", None))
        anti_legionella_period = _int(
            getattr(status, "anti_legionella_period", None)
        )

    reservations_enabled: bool | None = None
    reservations: tuple[dict[str, int], ...] | None = None
    if schedule is not None:
        reservations_enabled = schedule.get("reservation_use") == DEVICE_BOOL_ON
        reservations = tuple(
            raw_entry(e) for e in schedule.get("reservation") or []
        )

    return Observed(
        mode=mode,
        setpoint_raw=setpoint_raw,
        tou_on=tou_on,
        reservations_enabled=reservations_enabled,
        reservations=reservations,
        vacation_days=vacation_days,
        anti_legionella_on=anti_legionella_on,
        anti_legionella_period=anti_legionella_period,
    )
