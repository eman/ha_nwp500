"""Checks against the declaration, and the acknowledgement (section 3.5).

A segment is part of a timeline, so a segment that does not fit rejects the
whole plan. A grant that does not fit is rejected on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..const import CONTROL_MODE_NAMES
from .capabilities import Capabilities
from .intent import (
    REASON_MODE_NOT_ALLOWED,
    IntentRejected,
    Plan,
    mode_is_valid,
)

# Document states on the ack entity.
STATE_NONE = "none"
STATE_SHADOW = "shadow"
STATE_REJECTED = "rejected"
STATE_PENDING = "pending"
STATE_PROGRAMMED = "programmed"
STATE_PARTLY_PROGRAMMED = "partly_programmed"

# Segment statuses (section 4.2).
STATUS_SHADOW = "shadow"
STATUS_SCHEDULED = "scheduled"
STATUS_PENDING = "pending"
STATUS_PROGRAMMED = "programmed"
STATUS_MERGED = "merged"
STATUS_IN_FORCE = "in_force"
STATUS_ENDED = "ended"
STATUS_FAILED = "failed"
STATUS_REMOVED = "removed"

# A grant's status: surplus grants are not supported, so every one is
# rejected (section 5.7).
GRANT_REJECTED = "rejected"

# Reasons a segment is scheduled rather than programmed.
REASON_BEYOND_HORIZON = "beyond_horizon"
REASON_ENTRY_BUDGET = "entry_budget"
REASON_BOUNDS_UNKNOWN = "bounds_unknown"

# Why a grant is rejected.
REASON_GRANTS_UNSUPPORTED = "grants_unsupported"
# Live only (sections 5.4 and 5.11).
REASON_WRITE_NOT_CONFIRMED = "write_not_confirmed"
REASON_NOT_APPLIED = "not_applied_on_device"
REASON_HELD_IN_TOU_WINDOW = "held_in_tou_window"

# Warnings.
WARNING_MOVED = "moved_1_min"
WARNING_MODE_IN_TOU_WINDOW = "mode_in_tou_window"


@dataclass(frozen=True)
class ItemAck:
    """What the ack entity says about one segment or grant."""

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
    grants: tuple[ItemAck, ...] = ()
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
            "grants": [g.as_attribute() for g in self.grants],
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


def check_plan(plan: Plan, capabilities: Capabilities) -> None:
    """Reject the plan if a segment names a mode the heater does not have.

    Every mode it has is accepted, Vacation and power-off included: what a
    mode does is the scheduler's to know. Setpoints are not checked: the
    heater clamps what it is given, and limits belong in the library.
    """
    del capabilities
    for segment in plan.segments:
        if not mode_is_valid(segment.mode):
            raise IntentRejected(
                REASON_MODE_NOT_ALLOWED,
                f"segment {segment.id!r} mode {segment.mode!r} is not one of "
                f"{', '.join(CONTROL_MODE_NAMES)}",
            )


def check_grants(
    plan: Plan, capabilities: Capabilities, *, now: datetime
) -> dict[str, str]:
    """The reason each grant is rejected, by grant id: all of them.

    Surplus grants are not supported (section 5.7). A grant is rejected on
    its own, and the plan's segments are unaffected.
    """
    del capabilities, now
    return {grant.id: REASON_GRANTS_UNSUPPORTED for grant in plan.grants}
