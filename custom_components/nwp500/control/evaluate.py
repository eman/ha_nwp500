"""Per-directive checks against the declaration (spec section 3.3).

An accepted document's directives are checked one by one. A directive that
does not fit is rejected on its own, with a reason, while the rest proceed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..const import CONTROL_MODE_LIVE, CONTROL_MODE_SHADOW
from .capabilities import Capabilities
from .intent import Directive, Intent

STATUS_APPLIED = "applied"
STATUS_PENDING = "pending"
STATUS_PARTLY_APPLIED = "partly_applied"
STATUS_REJECTED = "rejected"
STATUS_SHADOW = "shadow"
STATUS_NONE = "none"

REASON_TYPE_UNSUPPORTED = "type_unsupported"
REASON_TYPE_NOT_LIVE = "type_not_live"
REASON_OUT_OF_BOUNDS = "out_of_bounds"
REASON_MODE_NOT_ALLOWED = "mode_not_allowed"
REASON_TOO_SHORT = "too_short"
REASON_IN_PAST = "in_past"


@dataclass(frozen=True)
class DirectiveAck:
    """What the acknowledgement entity says about one directive."""

    id: str
    type: str
    status: str
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # What execution knows beyond the status: a charge's completion, a
    # hold-off's fixed setpoint.
    detail: dict[str, Any] = field(default_factory=dict)

    def as_attribute(self) -> dict[str, Any]:
        """The directive's entry in the ack entity's attributes."""
        return {
            **self.extra,
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "reason": self.reason,
            **self.detail,
        }


@dataclass(frozen=True)
class Ack:
    """The acknowledgement of the most recent document."""

    intent_id: str | None
    state: str
    reason: str | None = None
    detail: str | None = None
    directives: tuple[DirectiveAck, ...] = ()

    def as_attributes(self) -> dict[str, Any]:
        """The ack entity's attributes."""
        return {
            "intent_id": self.intent_id,
            "reason": self.reason,
            "detail": self.detail,
            "directives": [d.as_attribute() for d in self.directives],
        }


NO_ACK = Ack(intent_id=None, state=STATUS_NONE)


def rejected_ack(
    intent_id: str | None, reason: str, detail: str | None = None
) -> Ack:
    """An ack for a document rejected whole."""
    return Ack(
        intent_id=intent_id, state=STATUS_REJECTED, reason=reason, detail=detail
    )


def check_directive(
    directive: Directive, capabilities: Capabilities, *, now: datetime
) -> str | None:
    """The reason a directive does not fit the declaration, or None.

    The live switch is checked last: a directive whose only fault is that
    its type is not live is still evaluated, as in shadow.
    """
    if directive.type not in capabilities.supported_directives:
        return REASON_TYPE_UNSUPPORTED
    if directive.end <= now:
        return REASON_IN_PAST
    if directive.temperature_raw is not None:
        low = capabilities.setpoint_min_raw
        high = capabilities.setpoint_max_raw
        if (low is not None and directive.temperature_raw < low) or (
            high is not None and directive.temperature_raw > high
        ):
            return REASON_OUT_OF_BOUNDS
    if directive.type == "mode" and directive.mode not in (
        capabilities.allowed_modes
    ):
        return REASON_MODE_NOT_ALLOWED
    if directive.type == "charge" and directive.duration < timedelta(
        minutes=capabilities.min_run_before_stop_min
    ):
        return REASON_TOO_SHORT
    if (
        capabilities.mode == CONTROL_MODE_LIVE
        and directive.type not in capabilities.live_types
    ):
        return REASON_TYPE_NOT_LIVE
    return None


def evaluate_intent(
    intent: Intent, capabilities: Capabilities, *, now: datetime
) -> Ack:
    """Check every directive and summarise the document's status.

    Until execution exists, a directive that fits is `shadow` in shadow
    mode and `pending` in live mode. The summary is `rejected` when every
    directive was, `partly_applied` when some were, and otherwise the
    mode's own status.
    """
    fits_status = (
        STATUS_SHADOW
        if capabilities.mode == CONTROL_MODE_SHADOW
        else STATUS_PENDING
    )
    acks: list[DirectiveAck] = []
    for directive in intent.directives:
        reason = check_directive(directive, capabilities, now=now)
        if reason is None:
            status = fits_status
        elif reason == REASON_TYPE_NOT_LIVE:
            status = STATUS_SHADOW
        else:
            status = STATUS_REJECTED
        acks.append(
            DirectiveAck(
                id=directive.id,
                type=directive.type,
                status=status,
                reason=reason,
                extra=directive.extra,
            )
        )

    rejected = sum(1 for a in acks if a.status == STATUS_REJECTED)
    if acks and rejected == len(acks):
        state = STATUS_REJECTED
    elif rejected:
        state = STATUS_PARTLY_APPLIED
    else:
        state = fits_status
    return Ack(intent_id=intent.intent_id, state=state, directives=tuple(acks))
