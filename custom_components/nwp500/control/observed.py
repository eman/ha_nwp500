"""What the feature reads from the device (spec section 4.3).

One immutable snapshot per evaluation, built from the coordinator's status,
the stored reservation and TOU schedules and the surplus entity, so the
planner never touches Home Assistant objects.
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

# The heat sources that use an element (the library's HeatSource).
_ELEMENT_SOURCES = (2, 3)

ENTRY_FIELDS = ("enable", "week", "hour", "min", "mode", "param")
_TOU_FIELDS = (
    "season",
    "week",
    "start_hour",
    "start_min",
    "end_hour",
    "end_min",
    "price_max",
)


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
    compressor_on: bool | None = None
    # Either electric element running, or the reported heat source using
    # one; None if the device reports neither.
    elements_on: bool | None = None
    # The upper tank temperature, in half-degrees Celsius.
    upper_tank_raw: int | None = None
    anti_legionella_busy: bool = False
    # The reservation list as the device last reported it; None until read.
    reservations_enabled: bool | None = None
    reservations: tuple[dict[str, int], ...] | None = None
    # The TOU program's periods, each with its price; empty if unknown.
    tou_periods: tuple[dict[str, int], ...] = ()
    surplus_on: bool | None = None

    @property
    def suspended_by(self) -> str | None:
        """Why the feature must not write the list (section 5.9), or None."""
        if self.mode in ("vacation", "power_off"):
            return self.mode
        if self.anti_legionella_busy:
            return "anti_legionella"
        return None

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
    tou_schedule: Mapping[str, Any] | None = None,
    *,
    surplus_on: bool | None = None,
) -> Observed:
    """Build a snapshot from the coordinator's data."""
    mode = setpoint_raw = tou_on = compressor_on = upper_tank_raw = None
    elements_on: bool | None = None
    anti_legionella = False
    if status is not None:
        mode = mode_name(getattr(status, "dhw_operation_setting", None))
        setpoint_raw = _int(
            getattr(status, "dhw_target_temperature_setting_raw", None)
        )
        tou_on = _bool(getattr(status, "tou_status", None))
        compressor_on = _bool(getattr(status, "comp_use", None))
        upper = _bool(getattr(status, "heat_upper_use", None))
        lower = _bool(getattr(status, "heat_lower_use", None))
        source = _int(get_enum_value(getattr(status, "current_heat_use", None)))
        known = [v for v in (upper, lower) if v is not None]
        if known or source in _ELEMENT_SOURCES:
            elements_on = any(known) or source in _ELEMENT_SOURCES
        # The tank probes report in tenths of a degree; setpoints in halves.
        deci = _int(getattr(status, "tank_upper_temperature_raw", None))
        upper_tank_raw = round(deci / 5) if deci is not None else None
        anti_legionella = bool(
            _bool(getattr(status, "anti_legionella_operation_busy", None))
        )

    reservations_enabled: bool | None = None
    reservations: tuple[dict[str, int], ...] | None = None
    if schedule is not None:
        reservations_enabled = schedule.get("reservation_use") == DEVICE_BOOL_ON
        reservations = tuple(
            raw_entry(e) for e in schedule.get("reservation") or []
        )

    tou_periods: tuple[dict[str, int], ...] = ()
    if tou_schedule is not None:
        tou_periods = tuple(
            {k: int(p.get(k, 0) or 0) for k in _TOU_FIELDS}
            for p in tou_schedule.get("reservation") or []
            if isinstance(p, Mapping)
        )

    return Observed(
        mode=mode,
        setpoint_raw=setpoint_raw,
        tou_on=tou_on,
        compressor_on=compressor_on,
        elements_on=elements_on,
        upper_tank_raw=upper_tank_raw,
        anti_legionella_busy=anti_legionella,
        reservations_enabled=reservations_enabled,
        reservations=reservations,
        tou_periods=tou_periods,
        surplus_on=surplus_on,
    )
