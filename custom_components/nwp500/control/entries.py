"""Reservation entries the feature owns (spec sections 5.2 to 5.4).

Entries use the device's native weekly reservations, with the weekday bit
of the directive's date. Every entry the feature writes is recorded as its
own, so that on start-up it can delete what is no longer wanted.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import schedule_state
from ..const import MODE_TO_DHW_ID
from .observed import DEVICE_BOOL_ON

KIND_START = "start"
KIND_CLOSING = "closing"
KIND_DAILY_REVERT = "daily_revert"

# Reservation bitfield: Sunday is bit 7, Saturday bit 1. Python's weekday()
# has Monday as 0 and Sunday as 6.
_WEEK_BITS = (64, 32, 16, 8, 4, 2, 128)
ALL_DAYS = 0xFE


def week_bit(local: datetime) -> int:
    """The weekday bit for a local date."""
    return _WEEK_BITS[local.weekday()]


@dataclass(frozen=True)
class OwnedEntry:
    """A reservation entry the feature wrote, or would write."""

    kind: str
    directive_id: str | None
    # When it fires: the local minute, as an aware datetime. The daily
    # revert entry repeats, so this is its next occurrence.
    fires_at: datetime
    mode: str
    param: int
    week: int | None = None

    @property
    def slot(self) -> tuple[int, int, int]:
        """The device slot: (week, hour, minute)."""
        return (self.week_field, self.fires_at.hour, self.fires_at.minute)

    @property
    def week_field(self) -> int:
        """The weekday bitfield."""
        return self.week if self.week is not None else week_bit(self.fires_at)

    def as_entry(self) -> dict[str, int]:
        """The device payload."""
        return {
            "enable": DEVICE_BOOL_ON,
            "week": self.week_field,
            "hour": self.fires_at.hour,
            "min": self.fires_at.minute,
            "mode": MODE_TO_DHW_ID[self.mode],
            "param": self.param,
        }

    def as_document(self) -> dict[str, Any]:
        """For storage and for the wanted-schedule entity's attributes."""
        return {
            "kind": self.kind,
            "directive_id": self.directive_id,
            "fires_at": self.fires_at.isoformat(),
            "mode": self.mode,
            "param": self.param,
            "week": self.week_field,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> OwnedEntry:
        """The inverse of `as_document`."""
        return cls(
            kind=str(document["kind"]),
            directive_id=document.get("directive_id"),
            fires_at=datetime.fromisoformat(document["fires_at"]),
            mode=str(document["mode"]),
            param=int(document["param"]),
            week=int(document["week"]) if document.get("week") else None,
        )


def merge_by_slot(entries: Iterable[OwnedEntry]) -> list[OwnedEntry]:
    """One entry per device slot.

    Two windows can meet at a minute: a hold-off closing as a charge
    starts. The start entry carries the wanted state at that minute, so it
    wins; a closing entry there would restore a baseline the charge is
    about to replace.
    """
    rank = {KIND_START: 0, KIND_DAILY_REVERT: 1, KIND_CLOSING: 2}
    chosen: dict[tuple[int, int, int], OwnedEntry] = {}
    for entry in sorted(entries, key=lambda e: rank.get(e.kind, 9)):
        chosen.setdefault(entry.slot, entry)
    return sorted(chosen.values(), key=lambda e: e.fires_at)


def wanted_schedule(
    baseline_entries: Iterable[dict[str, int]],
    owned: Iterable[OwnedEntry],
    *,
    enabled: bool,
) -> dict[str, Any]:
    """The reservation list the feature wants the device to hold."""
    return {
        "reservation_use": DEVICE_BOOL_ON if enabled else 1,
        "reservation": [dict(e) for e in baseline_entries]
        + [e.as_entry() for e in merge_by_slot(owned)],
    }


def schedule_hash(schedule: dict[str, Any]) -> str:
    """The same hash the Reservation Schedule sensor reports."""
    return schedule_state.schedule_hash(
        schedule_state.reservation_canonical(schedule)
    )
