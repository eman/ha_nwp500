"""Checks against the declaration, and the acknowledgement (section 3.5).

A segment is part of a timeline, so a segment that does not fit rejects the
whole plan. A grant that does not fit is rejected on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..const import CONTROL_MODE_LIVE
from .capabilities import Capabilities
from .intent import (
    REASON_MODE_NOT_ALLOWED,
    REASON_OUT_OF_BOUNDS,
    Grant,
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

# Grant statuses.
GRANT_WAITING = "waiting"
GRANT_RAISED = "raised"
GRANT_ENDED = "ended"
GRANT_REJECTED = "rejected"
GRANT_SHADOW = "shadow"
GRANT_FAILED = "failed"

# Reasons a segment is scheduled rather than programmed.
REASON_BEYOND_HORIZON = "beyond_horizon"
REASON_ENTRY_BUDGET = "entry_budget"
REASON_BOUNDS_UNKNOWN = "bounds_unknown"

# Grant rejection reasons.
REASON_GRANTS_UNSUPPORTED = "grants_unsupported"
REASON_INVALID_WINDOW = "invalid_window"
REASON_OVERLAPPING_GRANT = "overlapping_grant"
REASON_IN_PAST = "in_past"
REASON_NOT_LIVE = "not_live"
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

    def as_attributes(self) -> dict[str, Any]:
        """The ack entity's attributes."""
        return {
            "intent_id": self.intent_id,
            "reason": self.reason,
            "detail": self.detail,
            "segments": [s.as_attribute() for s in self.segments],
            "grants": [g.as_attribute() for g in self.grants],
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
    """Reject the plan if a segment does not fit the declaration.

    Bounds not yet known (the device's feature data has not arrived and no
    option sets them) are not checked; `"min"` then waits to be resolved.
    """
    low = capabilities.setpoint_min_raw
    high = capabilities.setpoint_max_raw
    for segment in plan.segments:
        if not mode_is_valid(segment.mode) or (
            segment.mode not in capabilities.allowed_modes
        ):
            raise IntentRejected(
                REASON_MODE_NOT_ALLOWED,
                f"segment {segment.id!r} mode {segment.mode!r} is not one of "
                f"{', '.join(capabilities.allowed_modes)}",
            )
        raw = segment.setpoint_raw
        if raw is not None and (
            (low is not None and raw < low) or (high is not None and raw > high)
        ):
            raise IntentRejected(
                REASON_OUT_OF_BOUNDS,
                f"segment {segment.id!r} setpoint is outside the bounds",
            )


def check_grants(
    plan: Plan, capabilities: Capabilities, *, now: datetime
) -> dict[str, str]:
    """The reason each rejected grant was rejected, by grant id."""
    rejected: dict[str, str] = {}
    accepted: list[Grant] = []
    high = capabilities.setpoint_max_raw
    low = capabilities.setpoint_min_raw
    for grant in plan.grants:
        reason: str | None = None
        if not capabilities.grants_supported:
            reason = REASON_GRANTS_UNSUPPORTED
        elif grant.end <= grant.start:
            reason = REASON_INVALID_WINDOW
        elif any(
            grant.start < other.end and other.start < grant.end
            for other in accepted
        ):
            reason = REASON_OVERLAPPING_GRANT
        elif (high is not None and grant.max_raw > high) or (
            low is not None and grant.max_raw < low
        ):
            reason = REASON_OUT_OF_BOUNDS
        elif grant.end <= now:
            reason = REASON_IN_PAST
        if reason is None:
            accepted.append(grant)
        else:
            rejected[grant.id] = reason
    return rejected


def grants_live(capabilities: Capabilities) -> bool:
    """Whether grants would be written, rather than evaluated as in shadow."""
    return capabilities.mode == CONTROL_MODE_LIVE and capabilities.live_grants
