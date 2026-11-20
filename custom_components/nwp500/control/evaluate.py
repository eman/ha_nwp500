"""Checks against the declaration, and the acknowledgement (section 3.5).

A segment is part of a timeline, so a segment that does not fit rejects the
whole plan.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nwp500.temperature import HalfCelsius

from ..vocabulary import CONTROL_MODE_NAMES
from .intent import (
    REASON_MODE_NOT_ALLOWED,
    REASON_SETPOINT_OUT_OF_RANGE,
    IntentRejected,
    Plan,
    mode_is_valid,
)


class AckState(StrEnum):
    """The ack entity's state (reference section 8.1)."""

    NONE = "none"
    SHADOW = "shadow"
    REJECTED = "rejected"
    PENDING = "pending"
    PROGRAMMED = "programmed"
    PARTLY_PROGRAMMED = "partly_programmed"


class SegmentStatus(StrEnum):
    """A segment's status on the acknowledgement (reference section 8.2)."""

    SHADOW = "shadow"
    SCHEDULED = "scheduled"
    PENDING = "pending"
    PROGRAMMED = "programmed"
    MERGED = "merged"
    IN_FORCE = "in_force"
    ENDED = "ended"
    FAILED = "failed"
    REMOVED = "removed"


class SegmentReason(StrEnum):
    """Why a segment is scheduled, or failed (reference section 8.2)."""

    BEYOND_HORIZON = "beyond_horizon"
    ENTRY_BUDGET = "entry_budget"
    # Live only; commands fail for these too (section 8.3).
    WRITE_NOT_CONFIRMED = "write_not_confirmed"
    NOT_APPLIED = "not_applied_on_device"


class ItemWarning(StrEnum):
    """A segment's warnings (reference section 8.2)."""

    MOVED = "moved_1_min"


STATE_NONE = AckState.NONE
STATE_SHADOW = AckState.SHADOW
STATE_REJECTED = AckState.REJECTED
STATE_PENDING = AckState.PENDING
STATE_PROGRAMMED = AckState.PROGRAMMED
STATE_PARTLY_PROGRAMMED = AckState.PARTLY_PROGRAMMED

STATUS_SHADOW = SegmentStatus.SHADOW
STATUS_SCHEDULED = SegmentStatus.SCHEDULED
STATUS_PENDING = SegmentStatus.PENDING
STATUS_PROGRAMMED = SegmentStatus.PROGRAMMED
STATUS_MERGED = SegmentStatus.MERGED
STATUS_IN_FORCE = SegmentStatus.IN_FORCE
STATUS_ENDED = SegmentStatus.ENDED
STATUS_FAILED = SegmentStatus.FAILED
STATUS_REMOVED = SegmentStatus.REMOVED

REASON_BEYOND_HORIZON = SegmentReason.BEYOND_HORIZON
REASON_ENTRY_BUDGET = SegmentReason.ENTRY_BUDGET
REASON_WRITE_NOT_CONFIRMED = SegmentReason.WRITE_NOT_CONFIRMED
REASON_NOT_APPLIED = SegmentReason.NOT_APPLIED

WARNING_MOVED = ItemWarning.MOVED


@dataclass(frozen=True)
class ItemAck:
    """What the ack entity says about one segment."""

    id: str
    status: str
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_attribute(self) -> dict[str, Any]:
        """The item's entry in the ack entity's attributes."""
        return {
            **self.extra,
            "id": self.id,
            "status": self.status,
            "reason": self.reason,
            "warnings": list(self.warnings),
            **self.detail,
        }


@dataclass(frozen=True)
class Ack:
    """The acknowledgement of the plan in force or the last rejected one."""

    intent_id: str | None
    state: str
    reason: str | None = None
    detail: str | None = None
    segments: tuple[ItemAck, ...] = ()
    # Device commands (section 3.7), in the plan's order.
    commands: tuple[ItemAck, ...] = ()
    # The latest document rejected while this plan stayed in force.
    rejection: Ack | None = None

    def as_attributes(self) -> dict[str, Any]:
        """The ack entity's attributes."""
        rejection = self.rejection
        return {
            "intent_id": self.intent_id,
            "reason": self.reason,
            "detail": self.detail,
            "segments": [s.as_attribute() for s in self.segments],
            "commands": [c.as_attribute() for c in self.commands],
            "rejected": {
                "intent_id": rejection.intent_id,
                "reason": rejection.reason,
                "detail": rejection.detail,
            }
            if rejection is not None
            else None,
        }


NO_ACK = Ack(intent_id=None, state=STATE_NONE)


def rejected_ack(
    intent_id: str | None, reason: str, detail: str | None = None
) -> Ack:
    """An ack for a document rejected whole."""
    return Ack(
        intent_id=intent_id, state=STATE_REJECTED, reason=reason, detail=detail
    )


def check_plan(
    plan: Plan, setpoint_range: tuple[int, int] | None = None
) -> None:
    """Reject the plan if a segment names what an entry cannot set.

    A mode an entry cannot set, or a setpoint outside `setpoint_range`,
    the heater's own (raw half-degrees Celsius, inclusive). Unknown until
    the heater's features are read, and then not checked.
    """
    for segment in plan.segments:
        if not mode_is_valid(segment.mode):
            raise IntentRejected(
                REASON_MODE_NOT_ALLOWED,
                f"segment {segment.id!r} mode {segment.mode!r} is not one of "
                f"{', '.join(CONTROL_MODE_NAMES)}",
            )
        if setpoint_range is None:
            continue
        low, high = setpoint_range
        if not low <= segment.setpoint_raw <= high:
            raise IntentRejected(
                REASON_SETPOINT_OUT_OF_RANGE,
                f"segment {segment.id!r} setpoint "
                f"{_shown(segment.setpoint_raw)} is outside the heater's "
                f"{_shown(low)} to {_shown(high)}",
            )


def _shown(raw: int) -> str:
    value = HalfCelsius(raw)
    return f"{value.to_fahrenheit():.1f} °F ({value.to_celsius():.1f} °C)"
