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

from .capabilities import (
    HORIZON,
    NEAR_TERM_LEAD,
    Capabilities,
)
from .entries import (
    KIND_NEAR_TERM,
    KIND_PLAN,
    KIND_PRECEDENCE_EXIT,
    NEAR_TERM_KINDS,
    OwnedEntry,
    compose_program,
    entry_slot,
    near_term_minute,
    schedule_hash,
    slots_collide,
)
from .evaluate import (
    REASON_BEYOND_HORIZON,
    REASON_ENTRY_BUDGET,
    REASON_NOT_APPLIED,
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
    WARNING_MOVED,
    Ack,
    ItemAck,
)
from .intent import Plan, Segment
from .observed import Observed

_LOGGER = logging.getLogger(__name__)

# An entry's minute is over this long after it starts.
FIRED_GRACE = timedelta(minutes=1)
# Read-back of an entry is skipped once it is this far past its window: the
# device's state then says nothing about that entry (a restart, an outage).
READBACK_STALE = timedelta(minutes=15)
# How many unconfirmed writes are kept for reconciling.
UNCONFIRMED_KEPT = 5

# Why a list write was made (the last write entity's `reason`).
WRITE_PLAN = "plan"
WRITE_CLEANUP = "cleanup"
WRITE_NEAR_TERM = "near_term"
# Surplus grant writes, which the feature no longer makes; a stored last
# write from an earlier version may carry them.
WRITE_GRANT_RAISE = "grant_raise"
WRITE_GRANT_LOWER = "grant_lower"
# Writes the feature no longer makes; a stored last write from an earlier
# version may carry them.
WRITE_PRECEDENCE_EXIT = "precedence_exit"
WRITE_POWER_OFF = "power_off"
WRITE_DISABLE = "disable"
# The first live write: the reservation switch on (5.1).
WRITE_TAKEOVER = "takeover"

# People's changes, as the override entity reports them (section 5.10).
REPORT_SWITCHED_OFF = "reservations_switched_off"
REPORT_REMOVED = "removed"
# Reported by an earlier version only; dropped when its state loads.
REPORT_FOREIGN = "foreign_entry"
_EARLIER_REPORTS = frozenset({REPORT_FOREIGN, "setpoint", "mode"})

# The reason a write reports, by the most telling kind of entry it adds.
_WRITE_REASONS = (
    (KIND_NEAR_TERM, WRITE_NEAR_TERM),
    (KIND_PLAN, WRITE_PLAN),
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


def _current_shape(report: Report) -> Report:
    """A stored removed entry's report, in the current entry item shape."""
    if report.field != REPORT_REMOVED or not isinstance(report.value, Mapping):
        return report
    try:
        entry = OwnedEntry.from_document(report.value)
    except KeyError, TypeError, ValueError:
        return report
    return replace(report, value=entry.as_attributes())


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
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Write:
        """The inverse of `as_document`."""
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
        # How long after an entry's minute its read-back is made: the poll
        # interval plus a minute (section 5.11).
        self.explain_window = explain_window
        self.plan: Plan | None = None
        # What the feature believes is on the device, of its own.
        self.owned: list[OwnedEntry] = []
        # Near-term entries wanted until they fire.
        self.extra: list[OwnedEntry] = []
        # Segments whose state an entry has put, or will put, in force.
        self.asserted: set[str] = set()
        self.reports: dict[str, Report] = {}
        # Segments of this plan whose entry a person deleted: nothing is
        # written for them again (section 5.10). A new plan starts with
        # none: it is the scheduler's answer (section 5.6).
        self.removed_segments: set[str] = set()
        self.last_write: Write | None = None
        # Live: whether the feature holds the list (section 5.1), segments
        # whose write failed after its retry (section 5.4), and the
        # read-back of each served item's latest entry (section 5.11).
        self.took_over = False
        # A live write sent but not confirmed, with the hash of the list
        # sent; and until when live writes are paused.
        # Every write sent since the last confirmed one: any of them may have
        # landed with its confirmation lost.
        self.unconfirmed: list[tuple[Write, str | None]] = []
        self.hold_until: datetime | None = None
        self.failed: dict[str, str] = {}
        self.readback: dict[str, str] = {}
        self._checked: set[str] = set()
        self.next_event_at: datetime | None = None
        self.last_step: datetime | None = None
        self._info: dict[str, _SegmentInfo] = {}
        self._programmed_until: datetime | None = None
        self._complete = False
        self._scheduled = 0
        self._reservations_on: bool | None = None
        # The owned plan entries that are the plan in force's, by check key:
        # written for it, or found at its adoption to be what it wants. An
        # entry is recognised by this, not by where it would be placed now:
        # the entry that moved it past a collision may have gone since.
        self._plan_entries: set[str] = set()

    # -- persistence -------------------------------------------------------

    def as_document(self) -> dict[str, Any]:
        """The state worth keeping across a restart."""
        return {
            "owned": [e.as_document() for e in self.owned],
            "extra": [e.as_document() for e in self.extra],
            "asserted": sorted(self.asserted),
            "plan_entries": sorted(self._plan_entries),
            "reports": [r.as_document() for r in self.reports.values()],
            "removed_segments": sorted(self.removed_segments),
            "last_write": self.last_write.as_document()
            if self.last_write
            else None,
            "reservations_on": self._reservations_on,
            "took_over": self.took_over,
            "unconfirmed": [
                {"write": w.as_document(), "hash": h}
                for w, h in self.unconfirmed
            ],
            # Whether the entries below were simulated: going live must not
            # take shadow's simulated entries for entries on the heater.
            "shadow": self.shadow,
            "failed": dict(self.failed),
            "readback": dict(self.readback),
            "checked": sorted(self._checked),
        }

    def load_document(self, document: Mapping[str, Any]) -> None:
        """The inverse of `as_document`; tolerant of anything missing."""
        self.owned = [
            OwnedEntry.from_document(e) for e in document.get("owned", [])
        ]
        # A surplus grant's entries, stored by an earlier version, are no
        # longer wanted: the next write removes any on the heater.
        self.extra = [
            entry
            for entry in (
                OwnedEntry.from_document(e) for e in document.get("extra", [])
            )
            if entry.kind in NEAR_TERM_KINDS
        ]
        self.asserted = set(document.get("asserted", []))
        # Stored before this was kept: every owned plan entry is taken for
        # the plan in force's, as it was then.
        self._plan_entries = set(
            document.get(
                "plan_entries",
                [self._check_key(e) for e in self.owned if e.kind == KIND_PLAN],
            )
        )
        self.reports = {}
        for raw in document.get("reports", []):
            report = _current_shape(Report.from_document(raw))
            if report.field in _EARLIER_REPORTS:
                # Judged by what an earlier version inferred.
                continue
            self.reports[self._report_key(report)] = report
        self.removed_segments = set(document.get("removed_segments", []))
        if raw_write := document.get("last_write"):
            self.last_write = Write.from_document(raw_write)
        self._reservations_on = document.get("reservations_on")
        self.took_over = bool(document.get("took_over", False))
        raw_unconfirmed = document.get("unconfirmed") or []
        if isinstance(raw_unconfirmed, Mapping):
            raw_unconfirmed = [raw_unconfirmed]
        self.unconfirmed = [
            (Write.from_document(raw["write"]), raw.get("hash"))
            for raw in raw_unconfirmed
        ]
        self.failed = {
            str(k): str(v) for k, v in document.get("failed", {}).items()
        }
        self.readback = {
            str(k): str(v) for k, v in document.get("readback", {}).items()
        }
        self._checked = set(document.get("checked", []))

    @staticmethod
    def _report_key(report: Report) -> str:
        """One report per key; a newer one replaces it (section 4.2)."""
        if report.field == REPORT_REMOVED and isinstance(report.value, Mapping):
            # A segment's plan and near-term entries can both be removed:
            # the kind tells them apart.
            kind = report.value.get("kind")
            return f"{report.field}:{kind}:{report.segment}"
        if report.field == REPORT_REMOVED:
            return f"{report.field}:{report.segment or report.value}"
        return report.field

    def forget_simulated(self) -> None:
        """Drop what shadow simulated, on going live.

        Shadow's entries were never written. Kept, they would be taken for
        entries a person removed from the heater. The plan is programmed
        afresh from what the heater holds; the segment in force is asserted.
        """
        self.owned = []
        self._plan_entries = set()
        self.extra = []
        self.asserted = set()
        self.removed_segments = set()
        self.readback = {}
        self._checked = set()
        self.unconfirmed = []
        self.last_write = None
        self.reports = {
            k: v for k, v in self.reports.items() if v.field != REPORT_REMOVED
        }

    # -- the timeline ------------------------------------------------------

    def resolve(self, segment: Segment) -> State | None:
        """A segment's state."""
        return State(segment.mode, segment.setpoint_raw)

    def _timeline(self) -> list[tuple[Segment, State | None, bool]]:
        """Each segment, its state, and whether it merges into the last."""
        result: list[tuple[Segment, State | None, bool]] = []
        previous: State | None = None
        for segment in self.plan.segments if self.plan else ():
            state = self.resolve(segment)
            # A segment that repeats the state before it needs no entry,
            # unless it asks to re-assert it (protocol 1.1).
            merged = (
                state is not None and state == previous and not segment.reassert
            )
            result.append((segment, state, merged))
            if state is not None:
                previous = state
        return result

    def _anchor(self, now: datetime) -> tuple[Segment, State | None] | None:
        """The plan's segment in force: the last one that has started.

        Merged segments are taken back to the first of their run. None
        before the first segment starts, or without a plan.
        """
        anchor: tuple[Segment, State | None] | None = None
        for segment, state, merged in self._timeline():
            if segment.start > now:
                break
            if not merged or anchor is None:
                anchor = (segment, state)
        return anchor

    def segment_in_force(self, now: datetime) -> str | None:
        """The id of the plan's segment in force, if any."""
        anchor = self._anchor(now)
        return anchor[0].id if anchor is not None else None

    def wanted_state(self, now: datetime) -> State | None:
        """The state the plan's segment in force sets; None before it."""
        anchor = self._anchor(now)
        return anchor[1] if anchor is not None else None

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

        A new plan is the scheduler's answer to everything reported so far,
        people's changes included: it is programmed as it stands, and what
        people removed from the plan before it is no longer held against
        it. Its segment in force gets a near-term entry when the heater
        reports another state. `restoring` is start-up re-adopting the
        stored plan (section 6.5): the same plan, so what was removed from
        it stays removed, and its segment in force was put in force before
        the restart and gets no near-term entry.
        """
        restored = restoring and self.plan is None
        self.plan = plan
        ids = {s.id for s in plan.segments}
        if restored:
            self.removed_segments &= ids
        else:
            # The removals were reported against the old plan; the new plan
            # is the scheduler's answer to them.
            self.removed_segments = set()
            self.reports = {
                k: r
                for k, r in self.reports.items()
                if r.field != REPORT_REMOVED
            }
            # The entries on the heater that are what the new plan wants,
            # judged against the list they were placed on.
            self._plan_entries = {
                self._check_key(e)
                for e in self.owned
                if e.kind == KIND_PLAN and self._placed_as_planned(e, observed)
            }
        self.failed = {}
        # A near-term entry still serves the new plan if the new plan has
        # the same segment in force now, in the same state. Any other served
        # the old plan only.
        current = self._anchor(now)
        keep = (
            (current[0].id, current[1])
            if current is not None and current[1] is not None
            else None
        )
        self.extra = [
            e
            for e in self.extra
            if e.kind not in NEAR_TERM_KINDS
            or (e.serves, State(e.mode, e.setpoint_raw)) == keep
        ]
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

        if keep is None or keep[0] in self.removed_segments:
            # Nothing of the plan's is in force yet, or a person deleted
            # the entry that puts it in force.
            return
        segment_id, state = keep
        self.asserted.add(segment_id)
        if restoring or any(
            e.kind in NEAR_TERM_KINDS and e.serves == segment_id
            for e in self.extra
        ):
            # In force before the restart, or an entry kept for it will put
            # it in force.
            return
        if (observed.mode, observed.setpoint_raw) != (
            state.mode,
            state.setpoint_raw,
        ):
            self._near_term(KIND_NEAR_TERM, segment_id, state, now)

    # -- a planning pass ---------------------------------------------------

    def step(self, now: datetime, observed: Observed) -> Write | None:
        """Update from what the device reports; return the write it needs."""
        self.last_step = now
        self.reconcile_unconfirmed(observed)
        self._track_list_changes(now, observed)
        self._track_readback(now, observed)
        self._assert_in_force(now)
        self._hold(now)
        self.extra = [e for e in self.extra if e.fires_at + FIRED_GRACE > now]

        desired = self._desired(now, observed)
        self.next_event_at = self._next_event(now)
        write = self._diff(desired, now)
        if (
            write is None
            and not self.shadow
            and not self.took_over
            and self.plan is not None
        ):
            # Going live with nothing of its own to add yet still takes the
            # list over: the reservation switch must be on for its entries.
            write = Write(
                reason=WRITE_TAKEOVER,
                at=now,
                added=(),
                removed=(),
                result=tuple(self.owned),
                simulated=False,
            )
        return write

    def commit(
        self, write: Write, *, confirmed_at: datetime | None = None
    ) -> None:
        """Record a write as done: confirmed in live, simulated in shadow.

        A near-term entry confirmed only after its minute never fired; it is
        issued again for the next minute it can make (section 5.2).
        """
        self.owned = list(write.result)
        self.last_write = write
        # A planning write's plan entries are the plan in force's; the
        # power-off write only switches entries off, whatever plan they are.
        self._plan_entries = {
            self._check_key(e)
            for e in self.owned
            if e.kind == KIND_PLAN
            and (
                write.reason != WRITE_POWER_OFF
                or self._check_key(e) in self._plan_entries
            )
        }
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
        the next write. An entry that must fire soon (a near-term or a
        precedence exit) is moved to the first minute it can still make
        after `retry_at`, so the retry does not write an entry whose minute
        has passed. After the retry has failed too (`final`), what it served
        is `failed`. A write that was never sent (the list could not be read
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
            self._retime(entry.kind, entry.serves, retry_at)

    def _retime(self, kind: str, serves: str, when: datetime) -> None:
        """Move a pending near-term entry to the first minute after `when`.

        Matched by kind and what it serves, not by equality: the copy that
        was written may have been moved past a collision. Dropped if the
        next segment starts by then, and a segment's entry once that segment
        is no longer the one in force (#172): it would put an ended
        segment's state back.
        """
        earliest = near_term_minute(when, NEAR_TERM_LEAD)
        for entry in [
            e for e in self.extra if e.kind == kind and e.serves == serves
        ]:
            if entry.fires_at >= earliest:
                continue
            self.extra = [e for e in self.extra if e != entry]
            if kind in (KIND_NEAR_TERM, KIND_PRECEDENCE_EXIT):
                anchor = self._anchor(when)
                if anchor is None or anchor[0].id != serves:
                    continue
            self._near_term(
                kind, serves, State(entry.mode, entry.setpoint_raw), when
            )

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
                    if entry.kind == KIND_PLAN and self._placed_as_planned(
                        entry, observed
                    ):
                        self._plan_entries.add(self._check_key(entry))
            for entry in write.removed:
                if entry in self.owned and entry.as_entry() not in on_device:
                    self.owned.remove(entry)

    # People's changes (section 5.10)

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

        if self.shadow:
            # In shadow nothing of the feature's is on the device.
            return
        on_device = [dict(e) for e in observed.reservations]
        for owned_entry in list(self.owned):
            # One whose minute is over is past: its deletion removes nothing.
            if (
                owned_entry.fires_at + FIRED_GRACE <= now
                or owned_entry.as_entry() in on_device
            ):
                continue
            serves = owned_entry.serves
            if serves is not None and self._serves_plan(owned_entry):
                # Nothing is written for its segment again (section 5.10).
                self.removed_segments.add(serves)
                self.extra = [
                    e
                    for e in self.extra
                    if not (e.kind in NEAR_TERM_KINDS and e.serves == serves)
                ]
            # The entry as the last write entity reported it (section 4.2).
            report = Report(
                REPORT_REMOVED,
                owned_entry.as_attributes(),
                now,
                owned_entry.serves,
            )
            self.reports[self._report_key(report)] = report
            # A person removed it: it is not written again (section 5.4).
            self.owned = [e for e in self.owned if e != owned_entry]
            self.extra = [e for e in self.extra if e != owned_entry]

    def _serves_plan(self, entry: OwnedEntry) -> bool:
        """Whether a deleted entry was one the plan in force wants.

        A near-term entry, or a plan entry of the plan in force: not one a
        newer plan replaced, not yet removed from the heater.
        """
        if entry.kind == KIND_PLAN:
            return self._check_key(entry) in self._plan_entries
        return entry.kind in NEAR_TERM_KINDS

    def _placed_as_planned(self, entry: OwnedEntry, observed: Observed) -> bool:
        """Whether a plan entry is where the plan in force places it now.

        Its segment's state, at the segment's start or moved from there
        only past minutes other entries on the heater occupy (section 5.2).
        """
        segment = next(
            (
                s
                for s in (self.plan.segments if self.plan else ())
                if s.id == entry.serves
            ),
            None,
        )
        if segment is None or self.resolve(segment) != State(
            entry.mode, entry.setpoint_raw
        ):
            return False
        occupied = self._occupied(self.others(observed))
        probe = replace(entry, fires_at=segment.start.astimezone(self.tz))
        while probe.fires_at < entry.fires_at:
            if not any(slots_collide(probe.slot, slot) for slot in occupied):
                return False
            probe = replace(
                probe, fires_at=probe.fires_at + timedelta(minutes=1)
            )
        return probe.fires_at == entry.fires_at

    # Read-back (section 5.11)

    @staticmethod
    def _check_key(entry: OwnedEntry) -> str:
        return f"{entry.kind}:{entry.serves}:{entry.fires_at.isoformat()}"

    def _track_readback(self, now: datetime, observed: Observed) -> None:
        """Compare the device's state with the latest entry that fired.

        Live only. Checked once per entry, after the poll interval plus a
        minute. A setpoint or mode that differs is `not_applied_on_device`.
        """
        if self.shadow:
            return
        keys = {self._check_key(e) for e in self.owned}
        self._checked &= keys
        if observed.mode is None or observed.setpoint_raw is None:
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
        if (observed.mode, observed.setpoint_raw) == (
            latest.mode,
            latest.setpoint_raw,
        ):
            self.readback.pop(latest.serves, None)
            return
        self.readback[latest.serves] = REASON_NOT_APPLIED
        _LOGGER.warning(
            "Entry for %s at %s: the heater reports %s at %d half-degrees, "
            "not %s at %d",
            latest.serves,
            latest.fires_at.isoformat(),
            observed.mode,
            observed.setpoint_raw,
            latest.mode,
            latest.setpoint_raw,
        )

    def _next_change_after(self, when: datetime) -> Segment | None:
        """The first segment after `when` whose entry changes the state.

        A merged segment repeats the state before it and has no entry, and
        a segment a person removed puts nothing on the heater.
        """
        for segment, _state, merged in self._timeline():
            if (
                segment.start > when
                and not merged
                and segment.id not in self.removed_segments
            ):
                return segment
        return None

    def _swap_extra(self, old: OwnedEntry, new: OwnedEntry) -> None:
        self.extra = [new if e == old else e for e in self.extra]

    # Entries

    def _near_term(
        self, kind: str, serves: str, state: State, now: datetime
    ) -> OwnedEntry | None:
        """An entry for the first minute at least the lead time away.

        None if a segment changing the state starts by then: its own entry
        makes this one pointless. A merged segment has none.
        """
        fires_at = near_term_minute(now, NEAR_TERM_LEAD).astimezone(self.tz)
        nxt = self._next_change_after(now)
        if nxt is not None and nxt.start <= fires_at:
            return None
        entry = OwnedEntry(
            kind, serves, fires_at, state.mode, state.setpoint_raw
        )
        # An exit re-asserts the segment: it replaces a near-term entry
        # still pending for it (#172).
        replaced = (
            (kind, KIND_NEAR_TERM) if kind == KIND_PRECEDENCE_EXIT else (kind,)
        )
        self.extra = [
            e
            for e in self.extra
            if not (e.kind in replaced and e.serves == serves)
        ]
        self.extra.append(entry)
        return entry

    def _assert_in_force(self, now: datetime) -> None:
        """A segment in force that no entry put in force gets a near-term.

        Not one whose entry a person deleted (section 5.10).
        """
        anchor = self._anchor(now)
        if anchor is None:
            return
        segment, state = anchor
        if (
            state is None
            or segment.id in self.asserted
            or segment.id in self.removed_segments
        ):
            return
        self.asserted.add(segment.id)
        self._near_term(KIND_NEAR_TERM, segment.id, state, now)

    def others(self, observed: Observed) -> list[dict[str, int]]:
        """Entries on the device that the feature does not own."""
        owned = [e.as_entry() for e in self.owned]
        result: list[dict[str, int]] = []
        for entry in observed.reservations or ():
            raw = dict(entry)
            if raw in owned:
                owned.remove(raw)
                continue
            result.append(raw)
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
        for segment, state, merged in self._timeline():
            warnings: list[str] = []
            if (
                merged
                or segment.start <= now
                or segment.id in self.removed_segments
            ):
                info[segment.id] = _SegmentInfo(False, warnings=tuple(warnings))
                continue
            if state is None:
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
            if placed == entry:
                occupied.append(placed.slot)
                desired.append(placed)
                continue
            # A near-term entry moved to, or past, the start of the next
            # segment changing the state would undo that segment. The
            # segment's own entry supersedes it there, so it is dropped.
            nxt = self._next_change_after(entry.fires_at)
            if nxt is not None and placed.fires_at >= nxt.start:
                continue
            # Kept at the minute it was moved to, so it is not taken for
            # fired, and withdrawn, at its original minute.
            self._swap_extra(entry, placed)
            occupied.append(placed.slot)
            desired.append(placed)
        self._info = info
        scheduled = [
            s
            for s, _state, merged in self._timeline()
            if not merged
            and s.start > now
            and not info[s.id].candidate
            # A person removed its entry: it is not waiting to be written.
            and s.id not in self.removed_segments
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
        others: list[dict[str, int]],
    ) -> list[tuple[int, int, int]]:
        """Slots a plan entry must not share (section 5.2.6).

        Only enabled entries count: the device stores two entries in one
        minute and fires only the enabled one (section 8, test 10).
        """
        return [
            entry_slot(entry) for entry in others if entry.get("enable") == 2
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
        # An entry whose minute has come stays for its read-back (section
        # 5.11), unless another write removes it first.
        if (
            not added
            and not self.shadow
            and all(e.fires_at <= now for e in removed)
            and any(now < e.fires_at + self.explain_window for e in removed)
        ):
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
        times.extend(e.fires_at + FIRED_GRACE for e in self.owned + self.extra)
        if not self.shadow:
            times.extend(
                e.fires_at + self.explain_window
                for e in self.owned
                if self._check_key(e) not in self._checked
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
        for segment, _state, merged in self._timeline():
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
            # Not one whose entry a person deleted (section 5.10).
            in_force = (
                anchor is not None
                and anchor[0].id == segment.id
                and segment.id not in self.removed_segments
            )
            detail: dict[str, Any] = {
                "fires_at": fires_at.isoformat() if fires_at else None,
                "in_force": in_force,
            }
            reason = info.reason
            if segment.id in self.removed_segments:
                status = STATUS_REMOVED
            elif merged:
                status = STATUS_MERGED
            elif segment.start <= now:
                # Begun, and not the plan's segment in force: a later one
                # has started.
                if not in_force:
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
        )


def owned_documents(entries: Iterable[OwnedEntry]) -> list[dict[str, Any]]:
    """Owned entries for an entity's attributes."""
    return [e.as_document() for e in entries]
