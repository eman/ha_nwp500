"""The planner: from a plan to the reservation entries that carry it out.

Spec section 5 of issue #158. The planner is pure: it takes the time and an
`Observed` snapshot, and returns the list write the device should receive.
Nothing here touches Home Assistant or the device. In shadow mode the device
controller commits each write without sending it, so the planner's view of
"what is on the device" is a simulation. In live mode it commits only what
the device confirmed, and a write the device did not take is handed back
through `reject`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from typing import Any

from ..const import MODE_TO_DHW_ID
from .capabilities import (
    HORIZON,
    NEAR_TERM_LEAD,
    SURPLUS_OFF_BEFORE_LOWER,
    SURPLUS_ON_BEFORE_RAISE,
    Capabilities,
)
from .entries import (
    KIND_GRANT_LOWER,
    KIND_GRANT_RAISE,
    KIND_GUARD,
    KIND_NEAR_TERM,
    KIND_PLAN,
    KIND_PRECEDENCE_EXIT,
    NEAR_TERM_KINDS,
    OwnedEntry,
    compose_program,
    entry_slot,
    firings,
    near_term_minute,
    schedule_hash,
    slots_collide,
)
from .evaluate import (
    GRANT_ENDED,
    GRANT_FAILED,
    GRANT_RAISED,
    GRANT_REJECTED,
    GRANT_SHADOW,
    GRANT_WAITING,
    REASON_BEYOND_HORIZON,
    REASON_BOUNDS_UNKNOWN,
    REASON_ENTRY_BUDGET,
    REASON_HELD_IN_TOU_WINDOW,
    REASON_NOT_APPLIED,
    REASON_NOT_LIVE,
    REASON_WRITE_NOT_CONFIRMED,
    STATE_PARTLY_PROGRAMMED,
    STATE_PENDING,
    STATE_PROGRAMMED,
    STATE_SHADOW,
    STATUS_ENDED,
    STATUS_FAILED,
    STATUS_IN_FORCE,
    STATUS_MERGED,
    STATUS_PENDING,
    STATUS_PROGRAMMED,
    STATUS_REMOVED,
    STATUS_SCHEDULED,
    STATUS_SHADOW,
    WARNING_MODE_IN_TOU_WINDOW,
    WARNING_MOVED,
    Ack,
    ItemAck,
    check_grants,
    grants_live,
)
from .intent import Grant, Plan, Segment
from .observed import Observed
from .owner import OwnerProgram
from .tou import in_tou_window

_LOGGER = logging.getLogger(__name__)

MODE_ID_TO_NAME = {v: k for k, v in MODE_TO_DHW_ID.items()}

# An entry has fired once its minute is over.
FIRED_GRACE = timedelta(minutes=1)
# Removing a fired entry is batched with other writes, but never left for
# longer than this, so it cannot repeat a week later while the feature runs.
CLEANUP_DEFER = timedelta(hours=24)
# Read-back of an entry is skipped once it is this far past its window: the
# device's state then says nothing about that entry (a restart, an outage).
READBACK_STALE = timedelta(minutes=15)
# How many unconfirmed writes are kept for reconciling.
UNCONFIRMED_KEPT = 5
# The heater's clock runs a few seconds ahead of Home Assistant's: on the
# unit tested an entry's change was reported 4 s before its minute. A change
# this far ahead of an entry's minute is still that entry's.
CLOCK_SKEW = timedelta(seconds=60)

# Why a list write was made (the last write entity's `reason`).
WRITE_PLAN = "plan"
WRITE_CLEANUP = "cleanup"
WRITE_NEAR_TERM = "near_term"
WRITE_GRANT_RAISE = "grant_raise"
WRITE_GRANT_LOWER = "grant_lower"
WRITE_PRECEDENCE_EXIT = "precedence_exit"
WRITE_POWER_OFF = "power_off"
WRITE_DISABLE = "disable"
# The first live write: owner entries switched off, reservations on (5.1).
WRITE_TAKEOVER = "takeover"

# People's changes, as the override entity reports them (section 5.10).
REPORT_SETPOINT = "setpoint"
REPORT_MODE = "mode"
REPORT_SWITCHED_OFF = "reservations_switched_off"
REPORT_FOREIGN = "foreign_entry"
REPORT_REMOVED = "removed"

_PRECEDENCE_WITH_EXIT = ("vacation", "power_off")

# The reason a write reports, by the most telling kind of entry it adds.
_WRITE_REASONS = (
    (KIND_GRANT_RAISE, WRITE_GRANT_RAISE),
    (KIND_GRANT_LOWER, WRITE_GRANT_LOWER),
    (KIND_PRECEDENCE_EXIT, WRITE_PRECEDENCE_EXIT),
    (KIND_NEAR_TERM, WRITE_NEAR_TERM),
    (KIND_PLAN, WRITE_PLAN),
    (KIND_GUARD, WRITE_GRANT_RAISE),
)


@dataclass(frozen=True)
class State:
    """A mode and a setpoint, in half-degrees Celsius."""

    mode: str
    setpoint_raw: int


@dataclass(frozen=True)
class Report:
    """A person's change the feature is reporting."""

    field: str
    value: Any
    detected_at: datetime
    segment: str | None

    def as_document(self) -> dict[str, Any]:
        """For storage and the override entity's attributes."""
        return {
            "field": self.field,
            "value": self.value,
            "detected_at": self.detected_at.isoformat(),
            "segment": self.segment,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Report:
        """The inverse of `as_document`."""
        return cls(
            field=str(document["field"]),
            value=document.get("value"),
            detected_at=datetime.fromisoformat(document["detected_at"]),
            segment=document.get("segment"),
        )


@dataclass(frozen=True)
class RaiseState:
    """A surplus raise in force (section 5.7)."""

    grant_id: str
    raised_at: datetime
    entry: OwnedEntry
    guard: OwnedEntry | None


@dataclass(frozen=True)
class Write:
    """One whole-list write, sent or simulated."""

    reason: str
    at: datetime
    added: tuple[OwnedEntry, ...]
    removed: tuple[OwnedEntry, ...]
    result: tuple[OwnedEntry, ...]
    simulated: bool = True
    confirmed: bool | None = None
    # Disabling's direct write of the owner's state.
    owner_state: tuple[str, int] | None = None

    def as_document(self) -> dict[str, Any]:
        """For storage and the last write entity's attributes."""
        return {
            "reason": self.reason,
            "at": self.at.isoformat(),
            "added": [e.as_document() for e in self.added],
            "removed": [e.as_document() for e in self.removed],
            "result": [e.as_document() for e in self.result],
            "simulated": self.simulated,
            "confirmed": self.confirmed,
            "owner_state": list(self.owner_state) if self.owner_state else None,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Write:
        """The inverse of `as_document`."""
        owner_state = document.get("owner_state")
        return cls(
            reason=str(document["reason"]),
            at=datetime.fromisoformat(document["at"]),
            added=tuple(
                OwnedEntry.from_document(e) for e in document.get("added", [])
            ),
            removed=tuple(
                OwnedEntry.from_document(e) for e in document.get("removed", [])
            ),
            result=tuple(
                OwnedEntry.from_document(e) for e in document.get("result", [])
            ),
            simulated=bool(document.get("simulated", True)),
            confirmed=document.get("confirmed"),
            owner_state=(str(owner_state[0]), int(owner_state[1]))
            if owner_state
            else None,
        )


@dataclass(frozen=True)
class _SegmentInfo:
    """What the last planning pass found for one segment."""

    candidate: bool
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    fires_at: datetime | None = None


class Planner:
    """The planner for one heater."""

    def __init__(
        self,
        capabilities: Capabilities,
        tz: tzinfo,
        *,
        shadow: bool = True,
        explain_window: timedelta = timedelta(seconds=90),
    ) -> None:
        """Start with no plan and nothing programmed."""
        self.capabilities = capabilities
        self.tz = tz
        self.shadow = shadow
        # How long after an entry's minute a matching change counts as that
        # entry firing: the poll interval plus a minute.
        self.explain_window = explain_window
        self.plan: Plan | None = None
        self.owner: OwnerProgram | None = None
        # What the feature believes is on the device, of its own.
        self.owned: list[OwnedEntry] = []
        # Near-term, grant and guard entries wanted until they fire.
        self.extra: list[OwnedEntry] = []
        # Segments whose state an entry has put, or will put, in force.
        self.asserted: set[str] = set()
        # Until a plan's first segment starts, what was in force continues.
        self.carry_state: State | None = None
        self.grant_rejections: dict[str, str] = {}
        self.reports: dict[str, Report] = {}
        self.removed_segments: set[str] = set()
        self.last_write: Write | None = None
        self.raise_state: RaiseState | None = None
        self.suspended_by: str | None = None
        # Live: whether the feature holds the list (section 5.1), segments or
        # grants whose write failed after its retry (section 5.4), and the
        # read-back of each served item's latest entry (section 5.11).
        self.took_over = False
        # A live write sent but not confirmed, with the hash of the list
        # sent; and until when live writes are paused.
        # Every write sent since the last confirmed one: any of them may have
        # landed with its confirmation lost.
        self.unconfirmed: list[tuple[Write, str | None]] = []
        # When the device's state was last read, to explain changes made by
        # entries that fired while it could not be read.
        self._state_seen_at: datetime | None = None
        self.hold_until: datetime | None = None
        self.failed: dict[str, str] = {}
        self.readback: dict[str, str] = {}
        self._checked: set[str] = set()
        # Served items whose mode the heater's behaviour has confirmed.
        self.mode_confirmed: set[str] = set()
        self.next_event_at: datetime | None = None
        self.last_step: datetime | None = None
        self._info: dict[str, _SegmentInfo] = {}
        self._programmed_until: datetime | None = None
        self._complete = False
        self._scheduled = 0
        self._device_state: tuple[str, int] | None = None
        self._reservations_on: bool | None = None
        self._compressor_on: bool | None = None
        self._cycle_started_at: datetime | None = None
        self._raised_in_cycle = False
        self._surplus_on_since: datetime | None = None
        self._surplus_off_since: datetime | None = None

    # -- persistence -------------------------------------------------------

    def as_document(self) -> dict[str, Any]:
        """The state worth keeping across a restart."""
        rs = self.raise_state
        return {
            "owned": [e.as_document() for e in self.owned],
            "extra": [e.as_document() for e in self.extra],
            "asserted": sorted(self.asserted),
            "carry_state": [
                self.carry_state.mode,
                self.carry_state.setpoint_raw,
            ]
            if self.carry_state
            else None,
            "reports": [r.as_document() for r in self.reports.values()],
            "removed_segments": sorted(self.removed_segments),
            "last_write": self.last_write.as_document()
            if self.last_write
            else None,
            "raise": {
                "grant_id": rs.grant_id,
                "raised_at": rs.raised_at.isoformat(),
                "entry": rs.entry.as_document(),
                "guard": rs.guard.as_document() if rs.guard else None,
            }
            if rs
            else None,
            "device_state": list(self._device_state)
            if self._device_state
            else None,
            "reservations_on": self._reservations_on,
            # The compressor cycle and whether it was raised in: a restart
            # mid-cycle must not allow a second raise (section 5.7).
            "compressor_on": self._compressor_on,
            "cycle_started_at": self._cycle_started_at.isoformat()
            if self._cycle_started_at
            else None,
            "raised_in_cycle": self._raised_in_cycle,
            "took_over": self.took_over,
            "unconfirmed": [
                {"write": w.as_document(), "hash": h}
                for w, h in self.unconfirmed
            ],
            "state_seen_at": self._state_seen_at.isoformat()
            if self._state_seen_at
            else None,
            # Whether the entries below were simulated: going live must not
            # take shadow's simulated entries for entries on the heater.
            "shadow": self.shadow,
            "failed": dict(self.failed),
            "readback": dict(self.readback),
            "checked": sorted(self._checked),
            "mode_confirmed": sorted(self.mode_confirmed),
        }

    def load_document(self, document: Mapping[str, Any]) -> None:
        """The inverse of `as_document`; tolerant of anything missing."""
        self.owned = [
            OwnedEntry.from_document(e) for e in document.get("owned", [])
        ]
        self.extra = [
            OwnedEntry.from_document(e) for e in document.get("extra", [])
        ]
        self.asserted = set(document.get("asserted", []))
        if carry := document.get("carry_state"):
            self.carry_state = State(str(carry[0]), int(carry[1]))
        self.reports = {}
        for raw in document.get("reports", []):
            report = Report.from_document(raw)
            self.reports[self._report_key(report)] = report
        self.removed_segments = set(document.get("removed_segments", []))
        if raw_write := document.get("last_write"):
            self.last_write = Write.from_document(raw_write)
        if raw_raise := document.get("raise"):
            self.raise_state = RaiseState(
                grant_id=str(raw_raise["grant_id"]),
                raised_at=datetime.fromisoformat(raw_raise["raised_at"]),
                entry=OwnedEntry.from_document(raw_raise["entry"]),
                guard=OwnedEntry.from_document(raw_raise["guard"])
                if raw_raise.get("guard")
                else None,
            )
        if device_state := document.get("device_state"):
            self._device_state = (str(device_state[0]), int(device_state[1]))
        self._reservations_on = document.get("reservations_on")
        self._compressor_on = document.get("compressor_on")
        if started := document.get("cycle_started_at"):
            self._cycle_started_at = datetime.fromisoformat(started)
        self._raised_in_cycle = bool(document.get("raised_in_cycle", False))
        self.took_over = bool(document.get("took_over", False))
        raw_unconfirmed = document.get("unconfirmed") or []
        if isinstance(raw_unconfirmed, Mapping):
            raw_unconfirmed = [raw_unconfirmed]
        self.unconfirmed = [
            (Write.from_document(raw["write"]), raw.get("hash"))
            for raw in raw_unconfirmed
        ]
        if seen := document.get("state_seen_at"):
            self._state_seen_at = datetime.fromisoformat(seen)
        self.failed = {
            str(k): str(v) for k, v in document.get("failed", {}).items()
        }
        self.readback = {
            str(k): str(v) for k, v in document.get("readback", {}).items()
        }
        self._checked = set(document.get("checked", []))
        self.mode_confirmed = set(document.get("mode_confirmed", []))

    @staticmethod
    def _report_key(report: Report) -> str:
        if report.field in (REPORT_FOREIGN, REPORT_REMOVED):
            return f"{report.field}:{report.segment or report.value}"
        return report.field

    def forget_simulated(self) -> None:
        """Drop what shadow simulated, on going live.

        Shadow's entries were never written. Kept, they would be taken for
        entries a person removed from the heater. The plan is programmed
        afresh from what the heater holds; the segment in force is asserted.
        """
        self.owned = []
        self.extra = []
        self.asserted = set()
        self.removed_segments = set()
        self.readback = {}
        self._checked = set()
        self.mode_confirmed = set()
        self.unconfirmed = []
        self.raise_state = None
        self.last_write = None
        self.reports = {
            k: v for k, v in self.reports.items() if v.field != REPORT_REMOVED
        }

    # -- the timeline ------------------------------------------------------

    def resolve(self, segment: Segment) -> State | None:
        """A segment's state, with `"min"` resolved; None if unknown yet."""
        raw = segment.setpoint_raw
        if raw is None:
            raw = self.capabilities.setpoint_min_raw
        if raw is None:
            return None
        return State(segment.mode, raw)

    def _timeline(self) -> list[tuple[Segment, State | None, bool]]:
        """Each segment, its state, and whether it merges into the last."""
        result: list[tuple[Segment, State | None, bool]] = []
        previous: State | None = None
        for segment in self.plan.segments if self.plan else ():
            state = self.resolve(segment)
            merged = state is not None and state == previous
            result.append((segment, state, merged))
            if state is not None:
                previous = state
        return result

    def _anchor(self, now: datetime) -> tuple[Segment, State | None] | None:
        """The segment in force, taking merged segments back to their first."""
        if self.plan is None:
            return None
        anchor: tuple[Segment, State | None] | None = None
        for segment, state, merged in self._timeline():
            if segment.start > now:
                break
            if not merged or anchor is None:
                anchor = (segment, state)
        return anchor

    def wanted_state(self, now: datetime) -> State | None:
        """The state the plan puts the heater in now, with a surplus raise."""
        anchor = self._anchor(now)
        if anchor is None:
            return self.carry_state if self.plan is not None else None
        state = anchor[1]
        rs = self.raise_state
        if state is not None and rs is not None and rs.entry.fires_at <= now:
            state = State(rs.entry.mode, rs.entry.setpoint_raw)
        return state

    # -- plans -------------------------------------------------------------

    def set_plan(
        self,
        plan: Plan,
        now: datetime,
        observed: Observed,
        *,
        restoring: bool = False,
    ) -> None:
        """Replace the plan in force from now (section 5.6).

        `restoring` is start-up re-adopting the stored plan (section 6.5):
        its segment in force was put in force before the restart, so no
        near-term entry is written for it, and a device state that differs
        is a person's change, not something to re-assert.
        """
        previous = self.plan
        old_anchor = self._anchor(now)
        old_state = (
            old_anchor[1]
            if old_anchor is not None
            else (self.carry_state if previous is not None else None)
        )

        # A segment is unchanged if its start and state are: one moved to a
        # new start is a new entry, not the one a person removed.
        old_states = {
            s.id: (s.start, self.resolve(s))
            for s in (previous.segments if previous else ())
        }
        self.plan = plan
        self.grant_rejections = check_grants(plan, self.capabilities, now=now)
        # A segment a person removed stays removed while the new plan has it
        # unchanged: a restart, or the same plan published again, must not
        # undo a person's change (sections 5.4 and 5.6).
        self.removed_segments = {
            s.id
            for s in plan.segments
            if s.id in self.removed_segments
            and (
                # At start-up the stored plan is the one they were removed
                # from, and nothing was adopted before it.
                (restoring and previous is None)
                or (
                    s.id in old_states
                    and old_states[s.id] == (s.start, self.resolve(s))
                )
            )
        }
        self.failed = {}
        # A near-term entry still serves the new plan if the new plan wants
        # the same state from the same segment now; any other served the old
        # plan only.
        new_anchor = self._anchor(now)
        keep = (
            (new_anchor[0].id, new_anchor[1])
            if new_anchor is not None and new_anchor[1] is not None
            else None
        )
        self.extra = [
            e
            for e in self.extra
            if e.kind not in (KIND_NEAR_TERM, KIND_PRECEDENCE_EXIT)
            or (e.serves, State(e.mode, e.setpoint_raw)) == keep
        ]
        self._reconcile_raise(previous, now, restoring=restoring)
        # A plan entry already programmed with the same state serves the
        # new plan's segment of the same id too.
        states = {s.id: self.resolve(s) for s in plan.segments}
        self.asserted = {
            e.serves
            for e in self.owned
            if e.kind == KIND_PLAN
            and e.serves is not None
            and states.get(e.serves) == State(e.mode, e.setpoint_raw)
        }
        self.readback = {
            k: v for k, v in self.readback.items() if k in self.asserted
        }
        # A confirmation carries over only for the segment in force that the
        # new plan keeps with the same state; another segment of the same id
        # is a new one.
        unchanged = (
            keep is not None
            and old_anchor is not None
            and (old_anchor[0].id, old_anchor[1]) == keep
        )
        self.mode_confirmed &= {keep[0]} if unchanged and keep else set()

        anchor = self._anchor(now)
        if anchor is None:
            if restoring:
                return
            if previous is None and observed.mode and observed.setpoint_raw:
                self.carry_state = State(observed.mode, observed.setpoint_raw)
            else:
                self.carry_state = old_state
            return
        self.carry_state = None
        segment, state = anchor
        if state is None:
            return
        if restoring:
            self.asserted.add(segment.id)
            return
        reference = old_state
        if previous is None and observed.mode and observed.setpoint_raw:
            reference = State(observed.mode, observed.setpoint_raw)
        self.asserted.add(segment.id)
        if reference != state:
            self._near_term(KIND_NEAR_TERM, segment.id, state, now)

    def _reconcile_raise(
        self, previous: Plan | None, now: datetime, *, restoring: bool = False
    ) -> None:
        """Keep a raise whose grant the new plan still has, unchanged.

        At start-up the stored plan is re-adopted with nothing before it;
        its raise is kept if its grant is still accepted.
        """
        rs = self.raise_state
        if rs is None:
            return
        if (
            restoring
            and previous is None
            and rs.grant_id not in self.grant_rejections
            and any(g.id == rs.grant_id for g in self.plan.grants)
            if self.plan is not None
            else False
        ):
            return

        def grant_of(plan: Plan | None) -> Grant | None:
            if plan is None:
                return None
            return next((g for g in plan.grants if g.id == rs.grant_id), None)

        kept = grant_of(self.plan)
        if (
            kept is not None
            and kept == grant_of(previous)
            and rs.grant_id not in self.grant_rejections
        ):
            return
        self._lower(now)

    def disable(
        self,
        now: datetime,
        *,
        simulated: bool | None = None,
        confirmed: bool | None = None,
        state_written: bool = True,
    ) -> Write:
        """Section 6.6: remove every owned entry, restore the owner's state.

        In live the device controller has already written the owner's list
        and state; this records it. `state_written` is False when the
        owner's state was not written (the heater was in vacation or
        powered off, which take precedence).
        """
        owner_state = (
            self.owner.state_now(now, self.tz)
            if self.owner is not None and state_written
            else None
        )
        write = Write(
            reason=WRITE_DISABLE,
            at=now,
            added=(),
            removed=tuple(self.owned),
            result=(),
            simulated=self.shadow if simulated is None else simulated,
            confirmed=confirmed,
            owner_state=owner_state,
        )
        self.failed = {}
        self.readback = {}
        self._checked = set()
        self.mode_confirmed = set()
        self.unconfirmed = []
        self.hold_until = None
        self.plan = None
        self.extra = []
        self.asserted = set()
        self.carry_state = None
        self.raise_state = None
        self.grant_rejections = {}
        self.commit(write)
        # The owner's program is back: the feature holds nothing.
        self.took_over = False
        return write

    # -- a planning pass ---------------------------------------------------

    def step(self, now: datetime, observed: Observed) -> Write | None:
        """Update from what the device reports; return the write it needs."""
        self.last_step = now
        self.reconcile_unconfirmed(observed)
        self._track_precedence(now, observed)
        self._track_people(now, observed)
        self._track_cycle(now, observed)
        self._track_surplus(now, observed)
        self._track_grant(now, observed)
        self._track_readback(now, observed)
        self._track_in_force(now, observed)
        self._assert_in_force(now)
        self._hold(now)
        self.extra = [e for e in self.extra if e.fires_at + FIRED_GRACE > now]

        desired = self._desired(now, observed)
        self.next_event_at = self._next_event(now)
        if self.suspended_by == "power_off":
            return self._switch_off_owned(now)
        if self.suspended_by:
            return None
        write = self._diff(desired, now)
        if (
            write is None
            and not self.shadow
            and not self.took_over
            and self.plan is not None
        ):
            # Going live with nothing of its own to add yet still takes the
            # list over: the owner's entries must not fire against the plan.
            write = Write(
                reason=WRITE_TAKEOVER,
                at=now,
                added=(),
                removed=(),
                result=tuple(self.owned),
                simulated=False,
            )
        return write

    def _switch_off_owned(self, now: datetime) -> Write | None:
        """Section 5.9: switch the feature's entries off while powered off.

        The device fires entries while the heater is powered off, and an
        entry powers it back on. So the feature's own unfired entries are
        switched off by their enable flag, unchanged otherwise. This is the
        one list write made under precedence. When power returns, the next
        pass writes them enabled again and re-asserts the segment in force.
        """
        unfired = [e for e in self.owned if e.fires_at + FIRED_GRACE > now]
        desired = [replace(e, enabled=False) for e in unfired]
        if all(not e.enabled for e in unfired):
            return None
        return Write(
            reason=WRITE_POWER_OFF,
            at=now,
            added=tuple(e for e in desired if e not in self.owned),
            removed=tuple(e for e in self.owned if e not in desired),
            result=tuple(desired),
            simulated=self.shadow,
        )

    def commit(
        self, write: Write, *, confirmed_at: datetime | None = None
    ) -> None:
        """Record a write as done: confirmed in live, simulated in shadow.

        A near-term entry confirmed only after its minute never fired; it is
        issued again for the next minute it can make (section 5.2).
        """
        self.owned = list(write.result)
        self.last_write = write
        for entry in write.added:
            if entry.kind == KIND_PLAN and entry.serves:
                self.asserted.add(entry.serves)
        if not write.simulated:
            # The device holds the whole wanted list now.
            self.took_over = True
            self.unconfirmed = []
            self.failed = {
                k: v
                for k, v in self.failed.items()
                if v != REASON_WRITE_NOT_CONFIRMED
            }
        if confirmed_at is not None:
            for entry in write.added:
                if (
                    entry.kind in NEAR_TERM_KINDS
                    and entry.serves is not None
                    and entry.fires_at <= confirmed_at
                ):
                    _LOGGER.info(
                        "The %s entry for %s was confirmed after its minute; "
                        "issuing it again",
                        entry.kind,
                        entry.serves,
                    )
                    self._checked.add(self._check_key(entry))
                    self._retime(entry.kind, entry.serves, confirmed_at)

    def reject(
        self,
        write: Write,
        now: datetime,
        *,
        retry_at: datetime,
        final: bool,
        written_hash: str | None = None,
        sent: bool = True,
    ) -> None:
        """A live write the device did not confirm (section 5.4).

        What it added may or may not be on the device: a write can land
        with its confirmation lost. It is kept as unconfirmed, with the hash
        of the list sent, and reconciled against the next read of the list
        (`reconcile_unconfirmed`). Plan entries stay candidates and go in
        the next write. An entry that must fire soon (a near-term, a
        precedence exit, a raise or a lowering) is moved to the first minute
        it can still make after `retry_at`, so the retry does not write an
        entry whose minute has passed. After the retry has failed too
        (`final`), what it served is `failed`, and a surplus raise is
        lowered. A write that was never sent (the list could not be read
        first) cannot have landed, and is not recorded as unconfirmed.
        """
        self.last_write = replace(write, simulated=False, confirmed=False)
        if sent:
            self.unconfirmed = [*self.unconfirmed, (write, written_hash)][
                -UNCONFIRMED_KEPT:
            ]
        for entry in write.added:
            if entry in self.owned:
                continue
            if final and entry.serves:
                self.failed[entry.serves] = REASON_WRITE_NOT_CONFIRMED
            if entry.kind not in NEAR_TERM_KINDS or entry.serves is None:
                continue
            rs = self.raise_state
            if (
                final
                and entry.kind == KIND_GRANT_RAISE
                and rs is not None
                and rs.grant_id == entry.serves
            ):
                # It may have landed: lower it, rather than forget it.
                self._lower(now)
                continue
            self._retime(entry.kind, entry.serves, retry_at)

    def _retime(self, kind: str, serves: str, when: datetime) -> None:
        """Move a pending near-term entry to the first minute after `when`.

        Matched by kind and what it serves, not by equality: the copy that
        was written may have been moved past a collision. Dropped if the
        next segment starts by then; a raise moves with its entry.
        """
        earliest = near_term_minute(when, NEAR_TERM_LEAD)
        for entry in [
            e for e in self.extra if e.kind == kind and e.serves == serves
        ]:
            if entry.fires_at >= earliest:
                continue
            self.extra = [e for e in self.extra if e != entry]
            moved = self._near_term(
                kind, serves, State(entry.mode, entry.setpoint_raw), when
            )
            rs = self.raise_state
            if rs is not None and rs.entry == entry:
                if moved is None:
                    self._drop_raise_entries(when)
                    self.raise_state = None
                else:
                    self.raise_state = replace(rs, entry=moved)

    def _hold(self, now: datetime) -> None:
        """In live, keep unwritten near-term entries makeable.

        A near-term entry that has not reached the device must not be
        dropped when its minute passes, or the segment it asserts is never
        put in force. While writes are paused it is moved past the pause;
        otherwise, once its minute has passed unwritten, to the next minute
        it can make.
        """
        if self.shadow:
            return
        until = self.hold_until
        if until is not None and now >= until:
            until = None
        for entry in [e for e in self.extra if e.kind in NEAR_TERM_KINDS]:
            if entry.serves is None or self._written(entry):
                continue
            if until is not None:
                self._retime(entry.kind, entry.serves, until)
            elif entry.fires_at + FIRED_GRACE <= now:
                self._retime(entry.kind, entry.serves, now)

    def _written(self, entry: OwnedEntry) -> bool:
        """Whether a near-term entry is on the device.

        Its copy on the device may have been moved past a collision.
        """
        return any(
            o.kind == entry.kind
            and o.serves == entry.serves
            and o.mode == entry.mode
            and o.setpoint_raw == entry.setpoint_raw
            and o.fires_at >= entry.fires_at
            for o in self.owned
        )

    def reconcile_unconfirmed(self, observed: Observed) -> None:
        """Settle a write that was not confirmed, against the device's list.

        If the device holds the whole list that was sent, the write landed
        and is committed. Otherwise any of its added entries found on the
        device are the feature's, and any of its removed entries gone from
        it were removed by the feature: neither is a person's doing.
        """
        schedule = observed.schedule
        if not self.unconfirmed or schedule is None:
            return
        device_hash = schedule_hash(schedule)
        for write, written_hash in reversed(self.unconfirmed):
            if written_hash is not None and device_hash == written_hash:
                _LOGGER.info("An unconfirmed %s write had landed", write.reason)
                self.commit(replace(write, simulated=False, confirmed=True))
                return
        on_device = [dict(e) for e in schedule["reservation"]]
        for write, _hash in self.unconfirmed:
            for entry in write.added:
                if entry not in self.owned and entry.as_entry() in on_device:
                    self.owned.append(entry)
            for entry in write.removed:
                if entry in self.owned and entry.as_entry() not in on_device:
                    self.owned.remove(entry)

    # Precedence and people's changes

    def _track_precedence(self, now: datetime, observed: Observed) -> None:
        suspended = observed.suspended_by
        if self.suspended_by in _PRECEDENCE_WITH_EXIT and suspended is None:
            anchor = self._anchor(now)
            if anchor is not None:
                segment, state = anchor
                if state is not None:
                    # Entries were skipped while it lasted; re-assert the
                    # segment in force.
                    self._near_term(
                        KIND_PRECEDENCE_EXIT, segment.id, state, now
                    )
        self.suspended_by = suspended

    def _device_entries(self, observed: Observed) -> list[Mapping[str, int]]:
        """The entries that can fire on the device now."""
        if not observed.reservations_enabled or observed.reservations is None:
            return []
        return [e for e in observed.reservations if e.get("enable") == 2]

    def _explained(
        self,
        now: datetime,
        observed: Observed,
        state: tuple[str, int],
        seen_before: datetime | None = None,
    ) -> bool:
        """Whether an entry that just fired set this state.

        Or an entry whose mode a TOU window held: the heater applies it when
        the window ends, with no entry firing then (section 8, test 6).
        """
        held = [
            e
            for e in self.owned
            if e.enabled
            and e.fires_at <= now
            and self.readback.get(e.serves or "") == REASON_HELD_IN_TOU_WINDOW
        ]
        if held:
            latest = max(held, key=lambda e: e.fires_at)
            if (latest.mode, latest.setpoint_raw) == state:
                return True
        # Since the state was last read, if that was longer ago than the
        # window: entries that fired while Home Assistant or the heater was
        # unreachable explain the change found afterwards.
        since = now - self.explain_window
        if seen_before is not None and seen_before < since:
            since = seen_before
        for entry in self._device_entries(observed):
            if not firings(entry, since, now + CLOCK_SKEW, self.tz):
                continue
            if (
                MODE_ID_TO_NAME.get(int(entry["mode"])) == state[0]
                and int(entry["param"]) == state[1]
            ):
                return True
        return False

    def _track_people(self, now: datetime, observed: Observed) -> None:
        self._track_list_changes(now, observed)
        if observed.mode is None or observed.setpoint_raw is None:
            return
        current = (observed.mode, observed.setpoint_raw)
        previous = self._device_state
        self._device_state = current
        seen_before = self._state_seen_at
        self._state_seen_at = now

        for name in (REPORT_SETPOINT, REPORT_MODE):
            report = self.reports.get(name)
            if report is not None and any(
                firings(entry, report.detected_at, now, self.tz)
                for entry in self._device_entries(observed)
            ):
                # It lasted until the next entry fired.
                del self.reports[name]

        if (
            previous is None
            or previous == current
            or previous[0] in _PRECEDENCE_WITH_EXIT
            or current[0] in _PRECEDENCE_WITH_EXIT
            or self._explained(now, observed, current, seen_before)
        ):
            return
        anchor = self._anchor(now)
        segment = anchor[0].id if anchor else None
        if previous[1] != current[1]:
            self.reports[REPORT_SETPOINT] = Report(
                REPORT_SETPOINT, current[1], now, segment
            )
        if previous[0] != current[0]:
            self.reports[REPORT_MODE] = Report(
                REPORT_MODE, current[0], now, segment
            )
        _LOGGER.info(
            "A person changed the heater to %s at %d half-degrees", *current
        )

    def _track_list_changes(self, now: datetime, observed: Observed) -> None:
        on = observed.reservations_enabled
        if on is not None:
            if self._reservations_on is True and on is False:
                self.reports[REPORT_SWITCHED_OFF] = Report(
                    REPORT_SWITCHED_OFF, False, now, None
                )
            elif on:
                self.reports.pop(REPORT_SWITCHED_OFF, None)
            self._reservations_on = on
        if observed.reservations is None:
            return

        foreign_keys: set[str] = set()
        if self.owner is not None:
            for entry, is_owner in self.others(observed):
                if is_owner:
                    continue
                key = f"{REPORT_FOREIGN}:{entry_slot(entry)}"
                foreign_keys.add(key)
                if key not in self.reports:
                    self.reports[key] = Report(
                        REPORT_FOREIGN, dict(entry), now, str(entry_slot(entry))
                    )
        for key in [k for k in self.reports if k.startswith(REPORT_FOREIGN)]:
            if key not in foreign_keys:
                del self.reports[key]

        if self.shadow:
            # In shadow nothing of the feature's is on the device.
            return
        on_device = [dict(e) for e in observed.reservations]
        for owned_entry in list(self.owned):
            if (
                owned_entry.fires_at + FIRED_GRACE <= now
                or owned_entry.as_entry() in on_device
            ):
                continue
            if owned_entry.kind == KIND_PLAN and owned_entry.serves:
                self.removed_segments.add(owned_entry.serves)
            report = Report(
                REPORT_REMOVED,
                owned_entry.as_document(),
                now,
                owned_entry.serves,
            )
            self.reports[self._report_key(report)] = report
            # A person removed it: it is not written again (section 5.4).
            self.owned = [e for e in self.owned if e != owned_entry]
            self.extra = [e for e in self.extra if e != owned_entry]

    # Read-back (section 5.11)

    @staticmethod
    def _check_key(entry: OwnedEntry) -> str:
        return f"{entry.kind}:{entry.serves}:{entry.fires_at.isoformat()}"

    def _track_readback(self, now: datetime, observed: Observed) -> None:
        """Compare the device's state with the latest entry that fired.

        Live only. Checked once per entry, after the poll interval plus a
        minute. A setpoint that differs is `not_applied_on_device`; a mode
        that differs is that too, unless the entry fired inside a TOU
        window, where the device can hold its mode (`held_in_tou_window`).
        A person's change since the entry fired explains any difference.
        """
        if self.shadow:
            return
        keys = {self._check_key(e) for e in self.owned}
        self._checked &= keys
        if (
            observed.mode is None
            or observed.setpoint_raw is None
            or observed.suspended_by is not None
        ):
            return
        due = [
            e
            for e in self.owned
            if e.enabled
            and e.fires_at + self.explain_window <= now
            and self._check_key(e) not in self._checked
        ]
        if not due:
            return
        self._checked.update(self._check_key(e) for e in due)
        latest = max(due, key=lambda e: e.fires_at)
        if latest.serves is None or (
            now - (latest.fires_at + self.explain_window) > READBACK_STALE
        ):
            return
        if any(
            r.detected_at >= latest.fires_at
            for r in self.reports.values()
            if r.field in (REPORT_SETPOINT, REPORT_MODE)
        ):
            return
        reason: str | None = None
        if observed.setpoint_raw != latest.setpoint_raw:
            reason = REASON_NOT_APPLIED
        elif observed.mode != latest.mode:
            tou_periods = observed.tou_periods if observed.tou_on else ()
            reason = (
                REASON_HELD_IN_TOU_WINDOW
                if in_tou_window(
                    tou_periods, latest.fires_at.astimezone(self.tz)
                )
                else REASON_NOT_APPLIED
            )
        if reason is None:
            self.readback.pop(latest.serves, None)
            return
        self.readback[latest.serves] = reason
        _LOGGER.warning(
            "Entry for %s at %s: the heater reports %s at %d half-degrees, "
            "not %s at %d (%s)",
            latest.serves,
            latest.fires_at.isoformat(),
            observed.mode,
            observed.setpoint_raw,
            latest.mode,
            latest.setpoint_raw,
            reason,
        )

    def _track_in_force(self, now: datetime, observed: Observed) -> None:
        """Follow the state in force after its read-back (section 5.11).

        A mode counts as applied only once the heater's behaviour confirms
        it: Heat Pump by running without an element, a mode that uses an
        element by an element running. Behaviour that contradicts the mode
        (an element in Heat Pump, the compressor in Electric) makes it
        `not_applied_on_device`. A mode held by a TOU window is applied when
        the heater reports it, and not applied if the window ends first.
        """
        if (
            self.shadow
            or observed.mode is None
            or observed.suspended_by is not None
        ):
            return
        fired = [
            e
            for e in self.owned
            if e.enabled
            and e.fires_at <= now
            and self._check_key(e) in self._checked
        ]
        latest: OwnedEntry | None = (
            max(fired, key=lambda e: e.fires_at) if fired else None
        )
        anchor = self._anchor(now)
        anchor_state = anchor[1] if anchor is not None else None
        if (
            anchor is not None
            and anchor_state is not None
            and (latest is None or latest.fires_at < anchor[0].start)
        ):
            # The segment in force needed no entry: the heater already held
            # its state when the plan arrived. Its behaviour is still read.
            latest = OwnedEntry(
                KIND_PLAN,
                anchor[0].id,
                anchor[0].start,
                anchor_state.mode,
                anchor_state.setpoint_raw,
            )
        if latest is None:
            return
        served = latest.serves
        if served is None or any(
            r.detected_at >= latest.fires_at
            for r in self.reports.values()
            if r.field in (REPORT_SETPOINT, REPORT_MODE)
        ):
            return
        found = self.readback.get(served)
        if found == REASON_HELD_IN_TOU_WINDOW:
            tou_periods = observed.tou_periods if observed.tou_on else ()
            if observed.mode == latest.mode:
                del self.readback[served]
                found = None
            elif not in_tou_window(tou_periods, now.astimezone(self.tz)):
                self.readback[served] = REASON_NOT_APPLIED
                _LOGGER.warning(
                    "The TOU window ended and %s is still not in %s",
                    served,
                    latest.mode,
                )
                return
        if found is not None or observed.mode != latest.mode:
            return
        contradicted = (
            latest.mode == "heat_pump"
            and observed.elements_on is True
            and not observed.anti_legionella_busy
        ) or (latest.mode == "electric" and observed.compressor_on is True)
        if contradicted:
            self.readback[served] = REASON_NOT_APPLIED
            self.mode_confirmed.discard(served)
            _LOGGER.warning(
                "The heater's behaviour contradicts %s for %s",
                latest.mode,
                served,
            )
            return
        if latest.mode == "heat_pump":
            confirmed = observed.elements_on is False
        else:
            confirmed = observed.elements_on is True
        if confirmed:
            self.mode_confirmed.add(served)

    # Surplus grants

    def _track_cycle(self, now: datetime, observed: Observed) -> None:
        on = observed.compressor_on
        if on is None:
            return
        if on and not self._compressor_on:
            # Started; or first seen running, in which case the start is
            # unknown and taken as now, the safe assumption.
            self._cycle_started_at = now
            self._raised_in_cycle = False
        elif not on and self._compressor_on:
            self._cycle_started_at = None
        self._compressor_on = on

    def _track_surplus(self, now: datetime, observed: Observed) -> None:
        if observed.surplus_on:
            if self._surplus_on_since is None:
                self._surplus_on_since = now
            self._surplus_off_since = None
        else:
            # Unknown counts as no surplus (section 5.7).
            if self._surplus_off_since is None:
                self._surplus_off_since = now
            self._surplus_on_since = None

    def _accepted_grants(self) -> list[Grant]:
        if self.plan is None:
            return []
        return [
            g for g in self.plan.grants if g.id not in self.grant_rejections
        ]

    def _track_grant(self, now: datetime, observed: Observed) -> None:
        rs = self.raise_state
        if rs is not None:
            segment_started = self.plan is not None and any(
                rs.raised_at < s.start <= now for s in self.plan.segments
            )
            guard_fired = rs.guard is not None and rs.guard.fires_at <= now
            if self.plan is None or segment_started or guard_fired:
                # The device ended it on its own.
                self._drop_raise_entries(now)
                self.raise_state = None
                return
            grant_kept = any(
                g.id == rs.grant_id for g in self._accepted_grants()
            )
            run_time = (
                now - self._cycle_started_at if self._cycle_started_at else None
            )
            surplus_gone = (
                run_time is not None
                and run_time
                >= timedelta(minutes=self.capabilities.min_run_before_lower_min)
                and self._surplus_off_since is not None
                and now - self._surplus_off_since >= SURPLUS_OFF_BEFORE_LOWER
            )
            if (
                observed.compressor_on is False
                or not grant_kept
                or surplus_gone
            ):
                self._lower(now)
            return

        grant = next(
            (g for g in self._accepted_grants() if g.contains(now)), None
        )
        anchor = self._anchor(now)
        if grant is None or anchor is None or anchor[1] is None:
            return
        if not self.shadow and not grants_live(self.capabilities):
            # Live for segments only: a grant is evaluated as in shadow,
            # and a raise would be written, so none is made.
            return
        state = anchor[1]
        if (
            state.mode != "heat_pump"
            or not observed.compressor_on
            or self._raised_in_cycle
            or self._surplus_on_since is None
            or now - self._surplus_on_since < SURPLUS_ON_BEFORE_RAISE
            or grant.end - now >= HORIZON
        ):
            return
        ceiling = self.capabilities.setpoint_max_raw
        target = min(grant.max_raw, ceiling) if ceiling else grant.max_raw
        if target <= state.setpoint_raw:
            return
        if near_term_minute(now, NEAR_TERM_LEAD) >= grant.end:
            # The raise would fire at or after the grant's end, with or
            # after its guard, and nothing on the device would end it.
            return
        entry = self._near_term(
            KIND_GRANT_RAISE, grant.id, State("heat_pump", target), now
        )
        if entry is None:
            return
        guard: OwnedEntry | None = None
        segments = self.plan.segments if self.plan is not None else ()
        if not any(entry.fires_at < s.start <= grant.end for s in segments):
            guard = OwnedEntry(
                KIND_GUARD,
                grant.id,
                grant.end.astimezone(self.tz),
                state.mode,
                state.setpoint_raw,
            )
            self.extra.append(guard)
        self.raise_state = RaiseState(grant.id, now, entry, guard)
        self._raised_in_cycle = True
        _LOGGER.info(
            "Surplus raise under grant %s to %d half-degrees", grant.id, target
        )

    def _drop_raise_entries(self, now: datetime) -> None:
        rs = self.raise_state
        if rs is None:
            return
        unfired = {
            e
            for e in (rs.entry, rs.guard)
            if e is not None and e.fires_at > now
        }
        self.extra = [e for e in self.extra if e not in unfired]

    def _lower(self, now: datetime) -> None:
        rs = self.raise_state
        if rs is None:
            return
        if rs.entry.fires_at > now:
            # The raise has not fired: withdrawing it is enough.
            self._drop_raise_entries(now)
        else:
            self._drop_raise_entries(now)
            anchor = self._anchor(now)
            state = anchor[1] if anchor is not None else None
            if state is None and rs.guard is not None:
                # No segment in force (a plan that stops): lower to the
                # state the guard would have restored.
                state = State(rs.guard.mode, rs.guard.setpoint_raw)
            if state is not None:
                self._near_term(KIND_GRANT_LOWER, rs.grant_id, state, now)
        self.raise_state = None

    # Entries

    def _near_term(
        self, kind: str, serves: str, state: State, now: datetime
    ) -> OwnedEntry | None:
        """An entry for the first minute at least the lead time away.

        None if the next segment starts by then: its own entry makes this
        one pointless.
        """
        fires_at = near_term_minute(now, NEAR_TERM_LEAD).astimezone(self.tz)
        if self.plan is not None:
            nxt = self.plan.next_segment_after(now)
            if nxt is not None and nxt.start <= fires_at:
                return None
        entry = OwnedEntry(
            kind, serves, fires_at, state.mode, state.setpoint_raw
        )
        self.extra = [
            e for e in self.extra if not (e.kind == kind and e.serves == serves)
        ]
        self.extra.append(entry)
        return entry

    def _assert_in_force(self, now: datetime) -> None:
        """A segment in force that no entry put in force gets a near-term."""
        anchor = self._anchor(now)
        if anchor is None:
            return
        segment, state = anchor
        if state is None or segment.id in self.asserted:
            return
        self.asserted.add(segment.id)
        self._near_term(KIND_NEAR_TERM, segment.id, state, now)

    def others(self, observed: Observed) -> list[tuple[dict[str, int], bool]]:
        """Entries on the device that the feature does not own."""
        owned = [e.as_entry() for e in self.owned]
        result: list[tuple[dict[str, int], bool]] = []
        for entry in observed.reservations or ():
            raw = dict(entry)
            if raw in owned:
                owned.remove(raw)
                continue
            is_owner = self.owner is not None and self.owner.is_owner_entry(raw)
            result.append((raw, is_owner))
        return result

    def _desired(self, now: datetime, observed: Observed) -> list[OwnedEntry]:
        """The owned entries the device should hold now (sections 5.2-5.3)."""
        cap = self.capabilities
        others = self.others(observed)
        occupied = self._occupied(others)
        extra = [e.localised(self.tz) for e in self.extra]
        owned_now = {e for e in self.owned if e.fires_at + FIRED_GRACE > now}

        info: dict[str, _SegmentInfo] = {}
        candidates: list[tuple[Segment, OwnedEntry]] = []
        tou_periods = observed.tou_periods if observed.tou_on else ()
        previous_mode: str | None = observed.mode
        for segment, state, merged in self._timeline():
            warnings: list[str] = []
            if state is not None:
                if (
                    not merged
                    and previous_mode is not None
                    and state.mode != previous_mode
                    and in_tou_window(
                        tou_periods, segment.start.astimezone(self.tz)
                    )
                ):
                    warnings.append(WARNING_MODE_IN_TOU_WINDOW)
                previous_mode = state.mode
            if (
                merged
                or segment.start <= now
                or segment.id in self.removed_segments
            ):
                info[segment.id] = _SegmentInfo(False, warnings=tuple(warnings))
                continue
            if state is None:
                info[segment.id] = _SegmentInfo(False, REASON_BOUNDS_UNKNOWN)
                continue
            entry = OwnedEntry(
                KIND_PLAN,
                segment.id,
                segment.start.astimezone(self.tz),
                state.mode,
                state.setpoint_raw,
            )
            # Placed now, past any collision, so that an entry already on
            # the device is reused only if it is this entry exactly: same
            # segment, state and minute (section 5.6). A plan that moves a
            # segment's start must not keep the old minute's entry.
            placed = self._place(entry, occupied)
            if placed != entry:
                warnings.append(WARNING_MOVED)
            if placed not in owned_now and segment.start < now + NEAR_TERM_LEAD:
                # Too close to program; it is asserted when it begins.
                info[segment.id] = _SegmentInfo(False, warnings=tuple(warnings))
                continue
            if segment.start - now >= HORIZON:
                info[segment.id] = _SegmentInfo(
                    False, REASON_BEYOND_HORIZON, tuple(warnings)
                )
                continue
            occupied.append(placed.slot)
            candidates.append((segment, placed))
            info[segment.id] = _SegmentInfo(
                True, warnings=tuple(warnings), fires_at=placed.fires_at
            )

        room = min(
            cap.entry_limit - len(others) - cap.entry_reserve,
            cap.entry_limit - len(others) - len(extra),
        )
        room = max(room, 0)
        chosen = candidates[:room]
        for segment, _entry in candidates[room:]:
            previous = info[segment.id]
            info[segment.id] = replace(
                previous, candidate=False, reason=REASON_ENTRY_BUDGET
            )

        desired: list[OwnedEntry] = [e for _, e in chosen]
        # Slots of the plan entries that will not be written go free again.
        occupied = self._occupied(others) + [e.slot for e in desired]
        for entry in extra:
            placed = self._place(entry, occupied)
            # A near-term or guard entry moved to, or past, the next
            # segment's start would undo that segment. The segment's own
            # entry supersedes it there, so it is dropped instead.
            nxt = (
                self.plan.next_segment_after(entry.fires_at)
                if self.plan is not None
                else None
            )
            if (
                placed != entry
                and nxt is not None
                and placed.fires_at >= nxt.start
            ):
                continue
            occupied.append(placed.slot)
            desired.append(placed)

        self._info = info
        scheduled = [
            s
            for s, _state, merged in self._timeline()
            if not merged and s.start > now and not info[s.id].candidate
        ]
        self._scheduled = len(scheduled)
        self._complete = not scheduled
        if scheduled:
            self._programmed_until = scheduled[0].start
        elif self.plan is not None and self.plan.segments:
            self._programmed_until = self.plan.segments[-1].start
        else:
            self._programmed_until = None
        return desired

    @staticmethod
    def _occupied(
        others: list[tuple[dict[str, int], bool]],
    ) -> list[tuple[int, int, int]]:
        """Slots a plan entry must not share (section 5.2.6).

        Only enabled entries count. The device stores two entries in one
        minute and fires only the enabled one (section 8, test 10), and the
        owner's entries are switched off by their own flag while live.
        """
        return [
            entry_slot(entry)
            for entry, is_owner in others
            if not is_owner and entry.get("enable") == 2
        ]

    @staticmethod
    def _place(
        entry: OwnedEntry, occupied: list[tuple[int, int, int]]
    ) -> OwnedEntry:
        """The entry, moved a minute at a time past any occupied slot."""
        placed = entry
        while any(slots_collide(placed.slot, slot) for slot in occupied):
            placed = replace(
                placed, fires_at=placed.fires_at + timedelta(minutes=1)
            )
        return placed

    def _diff(self, desired: list[OwnedEntry], now: datetime) -> Write | None:
        added = [e for e in desired if e not in self.owned]
        removed = [e for e in self.owned if e not in desired]
        if not added and not removed:
            return None
        # An entry whose minute has come is fired, or firing: removing it
        # gains nothing and costs a write, and its read-back needs it kept.
        # So it goes with a later write, or once it is a day old.
        if not added and all(e.fires_at <= now for e in removed):
            oldest = min(e.fires_at for e in removed)
            if now - oldest < CLEANUP_DEFER:
                return None
        kinds = {e.kind for e in added}
        reason = next(
            (
                write_reason
                for kind, write_reason in _WRITE_REASONS
                if kind in kinds
            ),
            WRITE_PLAN
            if any(e.fires_at > now for e in removed)
            else WRITE_CLEANUP,
        )
        return Write(
            reason=reason,
            at=now,
            added=tuple(added),
            removed=tuple(removed),
            result=tuple(desired),
            simulated=self.shadow,
        )

    def _next_event(self, now: datetime) -> datetime | None:
        times: list[datetime] = []
        if self.plan is not None:
            times.extend(s.start for s in self.plan.segments)
            times.extend(
                s.start - HORIZON
                for s in self.plan.segments
                if s.id in self._info
                and self._info[s.id].reason == REASON_BEYOND_HORIZON
            )
            for grant in self._accepted_grants():
                times.extend((grant.start, grant.end))
        times.extend(e.fires_at + FIRED_GRACE for e in self.owned + self.extra)
        if not self.shadow:
            times.extend(
                e.fires_at + self.explain_window
                for e in self.owned
                if self._check_key(e) not in self._checked
            )
        fired = [
            e.fires_at for e in self.owned if e.fires_at + FIRED_GRACE <= now
        ]
        if fired:
            times.append(min(fired) + CLEANUP_DEFER)
        if self._surplus_on_since:
            times.append(self._surplus_on_since + SURPLUS_ON_BEFORE_RAISE)
        if self._surplus_off_since and self.raise_state:
            times.append(self._surplus_off_since + SURPLUS_OFF_BEFORE_LOWER)
        if self._cycle_started_at and self.raise_state:
            times.append(
                self._cycle_started_at
                + timedelta(minutes=self.capabilities.min_run_before_lower_min)
            )
        future = [t for t in times if t > now]
        return min(future) if future else None

    # -- what the entities read -------------------------------------------

    @property
    def programmed_until(self) -> datetime | None:
        """How far the device's copy of the plan reaches."""
        return self._programmed_until

    @property
    def programmed_complete(self) -> bool:
        """Whether every segment is programmed."""
        return self._complete

    @property
    def scheduled_count(self) -> int:
        """Segments waiting for the horizon or for room."""
        return self._scheduled

    def next_entry(self, now: datetime) -> OwnedEntry | None:
        """The next owned entry to fire."""
        upcoming = [e for e in self.owned if e.fires_at > now]
        return min(upcoming, key=lambda e: e.fires_at) if upcoming else None

    def reservations_on(self, observed: Observed) -> bool:
        """The reservation switch the feature wants.

        On, to take the list over. Once it holds the list, as the device
        reports it: a person who turned it off keeps it off (section 5.10).
        """
        if not self.took_over or observed.reservations_enabled is None:
            return True
        return observed.reservations_enabled

    def program(
        self, observed: Observed, owned: Iterable[OwnedEntry] | None = None
    ) -> dict[str, Any]:
        """The whole list the feature wants on the device."""
        return compose_program(
            self.others(observed),
            self.owned if owned is None else owned,
            reservations_on=self.reservations_on(observed),
        )

    def program_hash(self, observed: Observed) -> str:
        """The program's `schedule_hash`."""
        return schedule_hash(self.program(observed))

    def _begun_status(self, item: str, now: datetime) -> str:
        """A begun segment's status in live, by the entry that asserts it.

        `pending` while its near-term entry is not on the device,
        `programmed` while it is but has not fired, else `in_force`.
        """
        asserting = [
            e
            for e in self.extra
            if e.kind in (KIND_NEAR_TERM, KIND_PRECEDENCE_EXIT)
            and e.serves == item
        ]
        if any(not self._written(e) for e in asserting):
            return STATUS_PENDING
        if any(e.fires_at > now for e in asserting):
            return STATUS_PROGRAMMED
        return STATUS_IN_FORCE

    def _live_status(
        self, item: str, status: str, reason: str | None
    ) -> tuple[str, str | None]:
        """A segment's status with live write and read-back failures."""
        if status in (STATUS_PENDING, STATUS_IN_FORCE) and item in self.failed:
            return STATUS_FAILED, self.failed[item]
        if status == STATUS_IN_FORCE and item in self.readback:
            found = self.readback[item]
            if found == REASON_NOT_APPLIED:
                return STATUS_FAILED, found
            return status, found
        return status, reason

    def ack(self, intent_id: str | None) -> Ack:
        """The acknowledgement of the plan in force."""
        now = self.last_step
        plan = self.plan
        if plan is None or now is None:
            return Ack(intent_id=intent_id, state="none")
        owned = set(self.owned)
        anchor = self._anchor(now)
        segments: list[ItemAck] = []
        statuses: list[str] = []
        timeline = self._timeline()
        for index, (segment, _state, merged) in enumerate(timeline):
            info = self._info.get(segment.id, _SegmentInfo(False))
            fires_at = info.fires_at
            if fires_at is None:
                # A segment put in force by a near-term entry.
                fires_at = next(
                    (
                        e.fires_at
                        for e in (*self.owned, *self.extra)
                        if e.kind in (KIND_NEAR_TERM, KIND_PRECEDENCE_EXIT)
                        and e.serves == segment.id
                    ),
                    None,
                )
            detail: dict[str, Any] = {
                "fires_at": fires_at.isoformat() if fires_at else None,
                "in_force": anchor is not None and anchor[0].id == segment.id,
                # Only the segment in force has a mode to confirm.
                "mode_confirmed": segment.id in self.mode_confirmed
                if not self.shadow
                and anchor is not None
                and anchor[0].id == segment.id
                else None,
            }
            reason = info.reason
            later_started = any(
                s.start <= now for s, _, _ in timeline[index + 1 :]
            )
            if segment.id in self.removed_segments:
                status = STATUS_REMOVED
            elif merged:
                status = STATUS_MERGED
            elif segment.start <= now:
                if later_started:
                    status = STATUS_ENDED
                elif self.shadow:
                    status = STATUS_SHADOW
                else:
                    status = self._begun_status(segment.id, now)
            elif info.candidate:
                programmed = any(
                    e.kind == KIND_PLAN and e.serves == segment.id
                    for e in owned
                )
                if self.shadow:
                    status = STATUS_SHADOW
                else:
                    status = STATUS_PROGRAMMED if programmed else STATUS_PENDING
            else:
                status = STATUS_SCHEDULED
            if not self.shadow:
                status, reason = self._live_status(segment.id, status, reason)
            statuses.append(status)
            segments.append(
                ItemAck(
                    id=segment.id,
                    status=status,
                    reason=reason,
                    warnings=info.warnings,
                    extra=segment.extra,
                    detail=detail,
                )
            )

        grants: list[ItemAck] = []
        live_grants = grants_live(self.capabilities)
        for grant in plan.grants:
            reason = self.grant_rejections.get(grant.id)
            if reason is not None:
                status = GRANT_REJECTED
            elif self.raise_state and self.raise_state.grant_id == grant.id:
                status = GRANT_RAISED
            elif grant.end <= now:
                status = GRANT_ENDED
            else:
                status = GRANT_WAITING
            if reason is None and not self.shadow and not live_grants:
                status, reason = GRANT_SHADOW, REASON_NOT_LIVE
            elif reason is None and not self.shadow:
                if grant.id in self.failed:
                    status, reason = GRANT_FAILED, self.failed[grant.id]
                elif (
                    status == GRANT_RAISED
                    and self.readback.get(grant.id) == REASON_NOT_APPLIED
                ):
                    status, reason = GRANT_FAILED, REASON_NOT_APPLIED
            grants.append(
                ItemAck(
                    id=grant.id, status=status, reason=reason, extra=grant.extra
                )
            )

        if self.shadow:
            state = STATE_SHADOW
        elif STATUS_PENDING in statuses:
            state = STATE_PENDING
        elif STATUS_REMOVED in statuses or STATUS_FAILED in statuses:
            state = STATE_PARTLY_PROGRAMMED
        else:
            state = STATE_PROGRAMMED
        return Ack(
            intent_id=intent_id,
            state=state,
            segments=tuple(segments),
            grants=tuple(grants),
        )


def owned_documents(entries: Iterable[OwnedEntry]) -> list[dict[str, Any]]:
    """Owned entries for an entity's attributes."""
    return [e.as_document() for e in entries]
