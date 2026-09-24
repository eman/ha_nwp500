"""Reservation entries: the ones the feature owns, and weekly timing.

A device entry is a single moment: at its weekday and minute it sets a mode
and a setpoint once. It repeats every week on the weekdays its bitfield
names. The feature's entries each name one weekday, the one of the date
they are meant for (spec section 5.2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .. import schedule_state
from ..const import MODE_TO_DHW_ID
from .observed import DEVICE_BOOL_OFF, DEVICE_BOOL_ON

# What an owned entry is for.
KIND_PLAN = "plan"
KIND_NEAR_TERM = "near_term"
KIND_PRECEDENCE_EXIT = "precedence_exit"
KIND_GRANT_RAISE = "grant_raise"
KIND_GRANT_LOWER = "grant_lower"
KIND_GUARD = "guard"
NEAR_TERM_KINDS = frozenset(
    {KIND_NEAR_TERM, KIND_PRECEDENCE_EXIT, KIND_GRANT_RAISE, KIND_GRANT_LOWER}
)

# The owner label the program entity reports for each kind (section 4.2).
OWNER_LABELS = {
    KIND_PLAN: "plan",
    KIND_NEAR_TERM: "near_term",
    KIND_PRECEDENCE_EXIT: "near_term",
    KIND_GRANT_RAISE: "near_term",
    KIND_GRANT_LOWER: "near_term",
    KIND_GUARD: "guard",
}

# Reservation bitfield: Sunday is bit 7, Saturday bit 1. Python's weekday()
# has Monday as 0 and Sunday as 6.
_WEEK_BITS = (64, 32, 16, 8, 4, 2, 128)


def week_bit(local: datetime) -> int:
    """The weekday bit for a local date."""
    return _WEEK_BITS[local.weekday()]


def near_term_minute(now: datetime, lead: timedelta) -> datetime:
    """The first minute that starts at least `lead` from now."""
    earliest = now + lead
    minute = earliest.replace(second=0, microsecond=0)
    return minute if minute >= earliest else minute + timedelta(minutes=1)


@dataclass(frozen=True)
class OwnedEntry:
    """A reservation entry the feature wrote, or would write."""

    kind: str
    serves: str | None
    fires_at: datetime
    mode: str
    setpoint_raw: int
    # False while the heater is powered off: entries fire then and would
    # power it back on, so the feature switches its own off (section 5.9).
    enabled: bool = True

    @property
    def slot(self) -> tuple[int, int, int]:
        """The device slot: (weekday bit, hour, minute), local time."""
        return (
            week_bit(self.fires_at),
            self.fires_at.hour,
            self.fires_at.minute,
        )

    def localised(self, tz: tzinfo) -> OwnedEntry:
        """The same entry with its time in the device's time zone."""
        return replace(self, fires_at=self.fires_at.astimezone(tz))

    def as_entry(self) -> dict[str, int]:
        """The device payload."""
        week, hour, minute = self.slot
        return {
            "enable": DEVICE_BOOL_ON if self.enabled else DEVICE_BOOL_OFF,
            "week": week,
            "hour": hour,
            "min": minute,
            "mode": MODE_TO_DHW_ID[self.mode],
            "param": self.setpoint_raw,
        }

    def as_document(self) -> dict[str, Any]:
        """For storage."""
        return {
            "kind": self.kind,
            "serves": self.serves,
            "fires_at": self.fires_at.isoformat(),
            "mode": self.mode,
            "setpoint_raw": self.setpoint_raw,
            "enabled": self.enabled,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> OwnedEntry:
        """The inverse of `as_document`."""
        return cls(
            kind=str(document["kind"]),
            serves=document.get("serves"),
            fires_at=datetime.fromisoformat(document["fires_at"]),
            mode=str(document["mode"]),
            setpoint_raw=int(document["setpoint_raw"]),
            enabled=bool(document.get("enabled", True)),
        )


def entry_slot(entry: Mapping[str, int]) -> tuple[int, int, int]:
    """The slot of a raw device entry. Its weekday bits may be several."""
    return (int(entry["week"]), int(entry["hour"]), int(entry["min"]))


def slots_collide(a: tuple[int, int, int], b: tuple[int, int, int]) -> bool:
    """Whether two slots share a weekday and a minute."""
    return bool(a[0] & b[0]) and a[1:] == b[1:]


def firings(
    entry: Mapping[str, int], after: datetime, until: datetime, tz: tzinfo
) -> list[datetime]:
    """Each time a weekly device entry fires in (after, until]."""
    result: list[datetime] = []
    local_after = after.astimezone(tz)
    local_until = until.astimezone(tz)
    day = local_after.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= local_until:
        if int(entry.get("week", 0)) & week_bit(day):
            fire = day.replace(
                hour=int(entry["hour"]), minute=int(entry["min"])
            )
            if local_after < fire <= local_until:
                result.append(fire)
        day += timedelta(days=1)
    return result


def schedule_hash(schedule: Mapping[str, Any]) -> str:
    """The same hash the Reservation Schedule sensor reports."""
    return schedule_state.schedule_hash(
        schedule_state.reservation_canonical(dict(schedule))
    )


def compose_program(
    others: Iterable[tuple[dict[str, int], bool]],
    owned: Iterable[OwnedEntry],
) -> dict[str, Any]:
    """The list the feature wants on the device while live.

    `others` are the entries the feature does not own, each with whether it
    is one of the owner's: the owner's are switched off by their own enable
    flag (section 5.1), anyone else's are kept as read. The reservation
    switch is on.
    """
    reservation: list[dict[str, int]] = []
    for entry, is_owner in others:
        kept = dict(entry)
        if is_owner:
            kept["enable"] = 1
        reservation.append(kept)
    reservation.extend(e.as_entry() for e in owned)
    return {"reservation_use": DEVICE_BOOL_ON, "reservation": reservation}
