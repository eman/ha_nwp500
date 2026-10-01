"""Device commands: applied once, as sent, and reported (spec section 3.7).

A plan's commands are settings a reservation entry cannot set. Each is
applied once, when its plan is adopted, after the list is written; the same
id with the same content is not applied again, by a new plan or a restart.
A status reports the application only: a person changing the setting later
does not change it, and nothing here undoes their change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .evaluate import REASON_NOT_APPLIED, REASON_WRITE_NOT_CONFIRMED, ItemAck
from .intent import Command
from .observed import Observed

STATUS_SHADOW = "shadow"
STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"


def reads_back(command: Command, observed: Observed) -> bool | None:
    """Whether the heater reports what the command set.

    None for a command whose setting the heater does not report: demand
    response, of which it reports only the utility's events.
    """
    params = command.params
    match command.command:
        case "vacation":
            return (
                observed.mode == "vacation"
                and observed.vacation_days == params["days"]
            )
        case "power":
            if observed.mode is None:
                return False
            return (observed.mode != "power_off") is params["on"]
        case "anti_legionella":
            if not params["enabled"]:
                return observed.anti_legionella_on is False
            return (
                observed.anti_legionella_on is True
                and observed.anti_legionella_period == params["period_days"]
            )
        case "tou":
            return observed.tou_on is params["enabled"]
    return None


@dataclass
class _Record:
    content: str
    status: str
    reason: str | None = None
    detail: str | None = None
    # When it was sent; a sent command is never sent again.
    sent_at: datetime | None = None

    def as_document(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> _Record:
        sent_at = doc.get("sent_at")
        return cls(
            content=str(doc["content"]),
            status=str(doc["status"]),
            reason=doc.get("reason"),
            detail=doc.get("detail"),
            sent_at=dt_util.parse_datetime(sent_at) if sent_at else None,
        )


class Commands:
    """The plan's commands and what became of each."""

    def __init__(self, window: timedelta) -> None:
        """`window`: how long the heater has to report a sent command."""
        self.window = window
        self.commands: tuple[Command, ...] = ()
        self._records: dict[str, _Record] = {}

    def set_plan(self, commands: tuple[Command, ...], *, writes: bool) -> None:
        """Take a plan's commands, keeping what became of ones seen before.

        A command evaluated in shadow was not applied, so going live
        applies it.
        """
        records: dict[str, _Record] = {}
        for command in commands:
            old = self._records.get(command.id)
            if command.rejection is not None:
                records[command.id] = _Record(
                    command.content,
                    STATUS_REJECTED,
                    command.rejection,
                    command.detail,
                )
            elif (
                old is not None
                and old.content == command.content
                and not (writes and old.status == STATUS_SHADOW)
            ):
                records[command.id] = old
            else:
                records[command.id] = _Record(
                    command.content,
                    STATUS_PENDING if writes else STATUS_SHADOW,
                )
        self.commands = commands
        self._records = records

    def to_send(self) -> list[Command]:
        """The commands still to send, in list order."""
        return [
            c
            for c in self.commands
            if (r := self._records[c.id]).status == STATUS_PENDING
            and r.sent_at is None
        ]

    def sending(self, command: Command, now: datetime) -> None:
        """Mark a command sent before sending it, so it is sent once."""
        self._records[command.id].sent_at = now

    def failed(self, command: Command, detail: str) -> None:
        """The library refused it, or it could not be sent."""
        record = self._records[command.id]
        record.status = STATUS_FAILED
        record.reason = REASON_WRITE_NOT_CONFIRMED
        record.detail = detail

    def check(self, now: datetime, observed: Observed) -> bool:
        """Read sent commands back; True if a status changed."""
        changed = False
        for command in self.commands:
            record = self._records[command.id]
            if record.status != STATUS_PENDING or record.sent_at is None:
                continue
            applied = reads_back(command, observed)
            if applied is None or applied:
                record.status = STATUS_APPLIED
                changed = True
            elif now >= record.sent_at + self.window:
                record.status = STATUS_FAILED
                record.reason = REASON_NOT_APPLIED
                changed = True
        return changed

    @property
    def next_deadline(self) -> datetime | None:
        """When the next sent command must have been reported by."""
        deadlines = [
            r.sent_at + self.window
            for r in self._records.values()
            if r.status == STATUS_PENDING and r.sent_at is not None
        ]
        return min(deadlines, default=None)

    def ack(self) -> tuple[ItemAck, ...]:
        """Each command's entry on the acknowledgement."""
        items: list[ItemAck] = []
        for command in self.commands:
            record = self._records[command.id]
            detail: dict[str, Any] = {"command": command.command}
            if record.detail is not None:
                detail["detail"] = record.detail
            items.append(
                ItemAck(
                    id=command.id,
                    status=record.status,
                    reason=record.reason,
                    extra=command.extra,
                    detail=detail,
                )
            )
        return tuple(items)

    def as_document(self) -> dict[str, Any]:
        """What must survive a restart: what became of each command."""
        return {k: r.as_document() for k, r in self._records.items()}

    def load_document(self, document: dict[str, Any]) -> None:
        """Restore what `as_document` kept; the plan follows on adoption."""
        self._records = {
            k: _Record.from_document(v) for k, v in document.items()
        }
