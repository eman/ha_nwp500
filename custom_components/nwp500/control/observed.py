"""What the feature reads from the device (spec section 4.3).

One immutable snapshot per evaluation, built from the coordinator's status,
the stored reservation schedule and the surplus entity, so the planning
engine never touches Home Assistant objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..const import MODE_TO_DHW_ID, get_enum_value

_DHW_ID_TO_MODE = {v: k for k, v in MODE_TO_DHW_ID.items()}

# Device booleans are 2 = on, 1 = off in the reservation payloads.
DEVICE_BOOL_ON = 2


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
    # The upper tank temperature, in half-degrees Celsius, for comparison
    # with setpoints.
    upper_tank_raw: int | None = None
    anti_legionella_busy: bool = False
    reservations_enabled: bool | None = None
    reservations: tuple[dict[str, int], ...] | None = None
    surplus_on: bool | None = None

    @property
    def suspended_by(self) -> str | None:
        """Why the feature must not write (spec section 5.10), or None."""
        if self.mode in ("vacation", "power_off"):
            return self.mode
        if self.anti_legionella_busy:
            return "anti_legionella"
        return None


def mode_name(setting: Any) -> str | None:
    """The spec's name for a DHW operation setting value."""
    value = _int(get_enum_value(setting))
    return _DHW_ID_TO_MODE.get(value) if value is not None else None


def observe(
    status: Any,
    schedule: Mapping[str, Any] | None,
    *,
    surplus_on: bool | None,
) -> Observed:
    """Build a snapshot from the coordinator's data."""
    if status is None:
        mode = setpoint_raw = tou_on = compressor_on = upper_tank_raw = None
        anti_legionella = False
    else:
        mode = mode_name(getattr(status, "dhw_operation_setting", None))
        setpoint_raw = _int(
            getattr(status, "dhw_target_temperature_setting_raw", None)
        )
        tou_on = _bool(getattr(status, "tou_status", None))
        compressor_on = _bool(getattr(status, "comp_use", None))
        # The tank probes report in tenths of a degree; setpoints in halves.
        deci = _int(getattr(status, "tank_upper_temperature_raw", None))
        upper_tank_raw = round(deci / 5) if deci is not None else None
        anti_legionella = bool(
            _bool(getattr(status, "anti_legionella_operation_busy", None))
        )

    reservations_enabled: bool | None = None
    reservations: tuple[dict[str, int], ...] | None = None
    if schedule is not None:
        from .baseline import raw_entry

        reservations_enabled = schedule.get("reservation_use") == DEVICE_BOOL_ON
        reservations = tuple(
            raw_entry(e) for e in schedule.get("reservation") or []
        )

    return Observed(
        mode=mode,
        setpoint_raw=setpoint_raw,
        tou_on=tou_on,
        compressor_on=compressor_on,
        upper_tank_raw=upper_tank_raw,
        anti_legionella_busy=anti_legionella,
        reservations_enabled=reservations_enabled,
        reservations=reservations,
        surplus_on=surplus_on,
    )
