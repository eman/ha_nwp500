"""The planning engine: what the feature wants the heater to do, and when.

Spec section 5 of issue #158, as far as shadow execution goes: the wanted
state over time (5.2), the entry budget (5.3), expiry and restore (5.4),
the compressor rules (5.5), hold-off (5.6), overrides (5.7), surplus grants
(5.8), the TOU lever's effect on the wanted TOU state (5.9) and precedence
(5.10). It is pure: it takes the time and an `Observed` snapshot and
returns a `Wanted` state. Nothing here touches Home Assistant or the
device; in shadow mode the wanted state is only reported, and in live mode
(a later step) it is what gets written and read back.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from typing import Any

from ..const import MODE_TO_DHW_ID
from .baseline import Baseline
from .capabilities import Capabilities
from .entries import (
    ALL_DAYS,
    KIND_CLOSING,
    KIND_DAILY_REVERT,
    KIND_START,
    OwnedEntry,
    merge_by_slot,
    schedule_hash,
    wanted_schedule,
    week_bit,
)
from .evaluate import (
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SHADOW,
    Ack,
    DirectiveAck,
)
from .intent import Directive, Intent
from .observed import Observed

_LOGGER = logging.getLogger(__name__)

MODE_ID_TO_NAME = {v: k for k, v in MODE_TO_DHW_ID.items()}

REASON_ENTRY_BUDGET = "entry_budget"
REASON_FLOOR_PREVENTS_HOLD_OFF = "floor_prevents_hold_off"

RESTORE_EXPIRY = "expiry"
RESTORE_INTENT_ENDED = "intent_ended"
RESTORE_STALE_INTENT = "stale_intent"
RESTORE_STARTUP = "startup"
RESTORE_OVERRIDE_EXPIRED = "override_expired"
RESTORE_DAILY_REVERT = "daily_revert"
RESTORE_DISABLED = "disabled"

FIELD_SETPOINT = "setpoint"
FIELD_MODE = "mode"
FIELD_TOU = "tou"
FIELD_RESERVATIONS = "reservations"
OVERRIDE_FIELDS = (FIELD_SETPOINT, FIELD_MODE, FIELD_TOU, FIELD_RESERVATIONS)

HOLD_COMPRESSOR_MIN_RUN = "compressor_min_run"
HOLD_REQUEST_CYCLE = "request_cycle"
HOLD_NO_REVERSAL = "no_reversal_in_cycle"

# After the daily revert entry fires, the intent is re-applied within this
# long (spec section 5.4).
DAILY_REVERT_REAPPLY = timedelta(minutes=5)
# Surplus must have been on this long before a raise, and off this long
# before the raise is lowered (spec section 5.8).
SURPLUS_ON_BEFORE_RAISE = timedelta(minutes=10)
SURPLUS_OFF_BEFORE_LOWER = timedelta(minutes=15)
# A change observed this soon after a baseline entry's minute, matching
# the entry, is that entry firing rather than a person's change.
BASELINE_ENTRY_WINDOW = timedelta(minutes=5)


@dataclass
class DirectiveRun:
    """Execution state of one admitted directive."""

    directive: Directive
    status: str
    reason: str | None = None
    # charge: the compressor was seen running in the window, and stopped.
    compressor_seen: bool = False
    complete: bool = False
    # hold_off: the setpoint fixed at start (spec section 5.6).
    hold_setpoint_raw: int | None = None

    @property
    def admitted(self) -> bool:
        """Whether the directive is being carried out."""
        return self.status != STATUS_REJECTED

    def in_force(self, now: datetime) -> bool:
        """Whether the window contains `now` and the directive is admitted."""
        return (
            self.admitted and self.directive.start <= now < self.directive.end
        )


@dataclass(frozen=True)
class Override:
    """A person's change the feature is honouring (spec section 5.7)."""

    field: str
    value: Any
    detected_at: datetime
    expires_at: datetime

    def as_document(self) -> dict[str, Any]:
        """For storage and the override entity's attributes."""
        return {
            "field": self.field,
            "value": self.value,
            "detected_at": self.detected_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class Restore:
    """The last restore to the baseline (spec section 5.4)."""

    reason: str
    at: datetime
    matches_baseline: bool | None

    def as_document(self) -> dict[str, Any]:
        """For storage and the restore entity's attributes."""
        return {
            "reason": self.reason,
            "at": self.at.isoformat(),
            "matches_baseline": self.matches_baseline,
        }


@dataclass(frozen=True)
class Wanted:
    """What the feature wants the heater to be doing now."""

    mode: str | None = None
    setpoint_raw: int | None = None
    tou_on: bool | None = None
    schedule: dict[str, Any] | None = None
    entries: tuple[OwnedEntry, ...] = ()
    holds: tuple[str, ...] = ()
    restore_pending: str | None = None
    suspended_by: str | None = None
    surplus_raised: bool = False

    @property
    def schedule_hash(self) -> str | None:
        """The hash the wanted list would produce on the device."""
        return schedule_hash(self.schedule) if self.schedule else None


class ControlEngine:
    """The planner for one heater."""

    def __init__(
        self, capabilities: Capabilities, tz: tzinfo, *, shadow: bool = True
    ) -> None:
        """Start with no baseline and no intent."""
        self.capabilities = capabilities
        self.tz = tz
        self.shadow = shadow
        self.baseline: Baseline | None = None
        self.intent: Intent | None = None
        self.document_ack: Ack | None = None
        self.runs: dict[str, DirectiveRun] = {}
        self.owned: list[OwnedEntry] = []
        self.overrides: dict[str, Override] = {}
        self.expected: dict[str, Any] = {}
        self.last_restore: Restore | None = None
        self.pending_restore: str | None = None
        self.suspended_by: str | None = None
        self.wanted = Wanted()
        self.next_event_at: datetime | None = None
        self.last_eval_at: datetime | None = None
        # Compressor cycle tracking (spec section 5.5).
        self._compressor_on: bool | None = None
        self._cycle_started_at: datetime | None = None
        self._cycle_protected = False
        self._lowered_in_cycle = False
        # Surplus grant tracking (spec section 5.8).
        self._surplus_on_since: datetime | None = None
        self._surplus_off_since: datetime | None = None
        self._surplus_raised_in_cycle = False
        self._surplus_raise_raw: int | None = None
        # The daily revert displaces the intent until this time.
        self._revert_hold_until: datetime | None = None
        self._in_force_ids: set[str] = set()
        # Set by an unconditional restore (disabled): from then on nothing
        # holds the baseline back, because nothing else will be written.
        self._unconditional = False

    # -- persistence -------------------------------------------------------

    def as_document(self) -> dict[str, Any]:
        """The state worth keeping across a restart."""
        return {
            "owned": [e.as_document() for e in self.owned],
            "overrides": [o.as_document() for o in self.overrides.values()],
            "expected": dict(self.expected),
            "last_restore": self.last_restore.as_document()
            if self.last_restore
            else None,
        }

    def load_document(self, document: dict[str, Any]) -> None:
        """The inverse of `as_document`; tolerant of anything missing."""
        self.owned = [
            OwnedEntry.from_document(e) for e in document.get("owned", [])
        ]
        self.overrides = {}
        for raw in document.get("overrides", []):
            override = Override(
                field=str(raw["field"]),
                value=raw.get("value"),
                detected_at=datetime.fromisoformat(raw["detected_at"]),
                expires_at=datetime.fromisoformat(raw["expires_at"]),
            )
            self.overrides[override.field] = override
        self.expected = dict(document.get("expected", {}))
        if raw_restore := document.get("last_restore"):
            self.last_restore = Restore(
                reason=str(raw_restore["reason"]),
                at=datetime.fromisoformat(raw_restore["at"]),
                matches_baseline=raw_restore.get("matches_baseline"),
            )

    # -- the ack -----------------------------------------------------------

    @property
    def ack(self) -> Ack:
        """The document ack with the execution statuses filled in."""
        if self.document_ack is None:
            return Ack(intent_id=None, state="none")
        if self.document_ack.state == STATUS_REJECTED and not self.runs:
            return self.document_ack
        acks = tuple(
            DirectiveAck(
                id=run.directive.id,
                type=run.directive.type,
                status=run.status,
                reason=run.reason,
                extra=run.directive.extra,
                detail=self._run_detail(run),
            )
            for run in self._runs_in_order()
        )
        rejected = sum(1 for a in acks if a.status == STATUS_REJECTED)
        if acks and rejected == len(acks):
            state = STATUS_REJECTED
        elif rejected:
            state = "partly_applied"
        else:
            state = STATUS_SHADOW if self.shadow else STATUS_PENDING
        return replace(self.document_ack, state=state, directives=acks)

    @staticmethod
    def _run_detail(run: DirectiveRun) -> dict[str, Any]:
        """What execution knows about a directive beyond its status."""
        detail: dict[str, Any] = {}
        if run.directive.type == "charge":
            detail["complete"] = run.complete
        elif run.directive.type == "hold_off":
            detail["hold_setpoint_raw"] = run.hold_setpoint_raw
        return detail

    def _runs_in_order(self) -> list[DirectiveRun]:
        if self.intent is None:
            return []
        return [
            self.runs[d.id] for d in self.intent.directives if d.id in self.runs
        ]

    # -- baseline and intents ------------------------------------------------

    def set_baseline(
        self, baseline: Baseline, now: datetime, observed: Observed
    ) -> None:
        """Adopt a baseline; directives admitted without one get entries.

        An intent can arrive before the device has reported, in which case
        it was admitted against no baseline and owns no entries. Admitting
        again is idempotent, so every directive is simply re-admitted.
        """
        self.baseline = baseline
        if self.intent is not None:
            for directive in sorted(
                self.intent.directives, key=lambda d: d.start
            ):
                run = self.runs.get(directive.id)
                if run is None or (
                    run.status == STATUS_REJECTED
                    and run.reason != REASON_ENTRY_BUDGET
                ):
                    continue
                self._admit(directive, now)
        self.evaluate(now, observed)

    def set_intent(
        self, intent: Intent, ack: Ack, now: datetime, observed: Observed
    ) -> None:
        """Replace the intent directive by directive (spec section 5.2.3).

        Unchanged directives keep their state and entries. Dropped ones are
        withdrawn: pending entries deleted, a state in force restored. New
        ones are admitted against the entry budget in start order.
        """
        previous = self.intent
        validated = {a.id: a for a in ack.directives}
        kept: dict[str, DirectiveRun] = {}
        if previous is not None:
            old = {d.id: d for d in previous.directives}
            for directive in intent.directives:
                run = self.runs.get(directive.id)
                if run is not None and old.get(directive.id) == directive:
                    kept[directive.id] = run
        dropped = [run for did, run in self.runs.items() if did not in kept]
        for run in dropped:
            self._withdraw(run, now, RESTORE_INTENT_ENDED)
        # Entries loaded from storage for directives this intent no longer
        # has (start-up after a change) are no longer wanted either.
        wanted_ids = {d.id for d in intent.directives}
        self.owned = [
            e
            for e in self.owned
            if e.directive_id is None or e.directive_id in wanted_ids
        ]

        self.intent = intent
        self.document_ack = ack
        self.runs = kept
        for directive in sorted(intent.directives, key=lambda d: d.start):
            if directive.id in kept:
                continue
            validation = validated.get(directive.id)
            if validation is not None and validation.status == STATUS_REJECTED:
                self.runs[directive.id] = DirectiveRun(
                    directive, STATUS_REJECTED, validation.reason
                )
                continue
            self._admit(directive, now)
        if not intent.directives and previous is not None:
            # An explicit "no intent": run on the baseline.
            self.pending_restore = self.pending_restore or RESTORE_INTENT_ENDED
        self._protect_running_cycle(now, observed)
        self.evaluate(now, observed)

    def clear_intent(
        self,
        reason: str,
        now: datetime,
        observed: Observed,
        *,
        unconditional: bool = False,
    ) -> None:
        """Withdraw the whole intent: stale, start-up or disabled."""
        had_state = any(
            run.in_force(now) for run in self.runs.values()
        ) or bool(self.owned)
        self.intent = None
        self.document_ack = None
        self.runs = {}
        self.owned = []
        self._in_force_ids = set()
        if unconditional:
            self._unconditional = True
            self.pending_restore = None
            self._record_restore(reason, now, observed)
        elif had_state or reason == RESTORE_STARTUP:
            self.pending_restore = reason
        self.evaluate(now, observed)

    def _withdraw(self, run: DirectiveRun, now: datetime, reason: str) -> None:
        self.owned = [
            e for e in self.owned if e.directive_id != run.directive.id
        ]
        if run.in_force(now):
            self.pending_restore = reason

    def _admit(self, directive: Directive, now: datetime) -> None:
        """Admit a directive if its entries fit the budget (section 5.3)."""
        # Re-admission (the same intent adopted again after a restart)
        # replaces the directive's entries rather than adding to them.
        self.owned = [e for e in self.owned if e.directive_id != directive.id]
        cap = self.capabilities
        starts = (
            1 if directive.type == "charge" and directive.start > now else 0
        )
        closings = 1 if directive.type in ("charge", "hold_off") else 0
        baseline_count = len(self.baseline.reservations) if self.baseline else 0
        used_starts = baseline_count + sum(
            1 for e in self.owned if e.kind == KIND_START
        )
        used_total = baseline_count + len(self.owned) + 1  # the daily revert
        if (
            used_starts + starts
            > cap.reservation_entry_limit - cap.reservation_entry_reserve
            or used_total + starts + closings > cap.reservation_entry_limit
        ):
            self.runs[directive.id] = DirectiveRun(
                directive, STATUS_REJECTED, REASON_ENTRY_BUDGET
            )
            return
        self.runs[directive.id] = DirectiveRun(
            directive, STATUS_SHADOW if self.shadow else STATUS_PENDING
        )
        if self.baseline is None:
            return
        if starts:
            self.owned.append(
                OwnedEntry(
                    kind=KIND_START,
                    directive_id=directive.id,
                    fires_at=directive.start.astimezone(self.tz),
                    mode=self._mode_wanted_at(directive.start),
                    param=int(directive.temperature_raw or 0),
                )
            )
        if closings:
            self.owned.append(
                OwnedEntry(
                    kind=KIND_CLOSING,
                    directive_id=directive.id,
                    fires_at=directive.end.astimezone(self.tz),
                    mode=self._mode_wanted_at(directive.end),
                    param=self._setpoint_wanted_at(directive.end, directive.id),
                )
            )

    def _mode_wanted_at(self, when: datetime) -> str:
        for run in self.runs.values():
            d = run.directive
            if d.type == "mode" and run.admitted and d.start <= when < d.end:
                return str(d.mode)
        return self.baseline.mode if self.baseline else "energy_saver"

    def _setpoint_wanted_at(self, when: datetime, except_id: str) -> int:
        for run in self.runs.values():
            d = run.directive
            if (
                d.type == "charge"
                and d.id != except_id
                and run.admitted
                and d.start <= when < d.end
                and d.temperature_raw is not None
            ):
                return d.temperature_raw
        return self.baseline.setpoint_raw if self.baseline else 0

    # -- evaluation --------------------------------------------------------

    def evaluate(self, now: datetime, observed: Observed) -> Wanted:
        """Work out the wanted state at `now`."""
        previous = self.last_eval_at
        self._track_cycle(now, observed)
        self._track_surplus(now, observed)
        self._track_precedence(now, observed)
        self._drop_fired_entries(now)
        self._daily_revert(previous, now, observed)
        self._directive_boundaries(now, observed)

        if self.baseline is None:
            self.wanted = Wanted(suspended_by=self.suspended_by)
            self.last_eval_at = now
            self.next_event_at = self._next_event(now)
            return self.wanted

        wanted = self._compose(now, observed)
        wanted = self._apply_compressor_rules(now, observed, wanted)
        self._track_overrides(now, observed)
        wanted = self._apply_overrides(observed, wanted)
        self._settle_pending_restore(now, observed, wanted)
        wanted = replace(wanted, restore_pending=self.pending_restore)

        self.wanted = wanted
        self.last_eval_at = now
        self.next_event_at = self._next_event(now)
        return wanted

    # Cycle and surplus tracking

    def _track_cycle(self, now: datetime, observed: Observed) -> None:
        on = observed.compressor_on
        if on is None:
            return
        if on and not self._compressor_on:
            # Started; or first seen running, in which case the start is
            # unknown and taken as now, the safe assumption.
            self._cycle_started_at = now
            self._lowered_in_cycle = False
            self._surplus_raised_in_cycle = False
            self._cycle_protected = self._request_directive_in_force(now)
            for run in self.runs.values():
                if run.directive.type == "charge" and run.in_force(now):
                    run.compressor_seen = True
        elif not on and self._compressor_on:
            self._cycle_started_at = None
            self._cycle_protected = False
            self._lowered_in_cycle = False
            self._surplus_raise_raw = None
            for run in self.runs.values():
                if (
                    run.directive.type == "charge"
                    and run.in_force(now)
                    and run.compressor_seen
                ):
                    # Completion is the compressor stopping (section 5.2).
                    run.complete = True
        self._compressor_on = on

    def _request_directive_in_force(self, now: datetime) -> bool:
        return any(
            run.directive.type == "mode"
            and run.directive.request
            and run.in_force(now)
            for run in self.runs.values()
        )

    def _protect_running_cycle(self, now: datetime, observed: Observed) -> None:
        """A cycle already running when a request arrives runs to the end."""
        if observed.compressor_on and self._request_directive_in_force(now):
            self._cycle_protected = True

    def _cycle_run_time(self, now: datetime) -> timedelta | None:
        if self._cycle_started_at is None:
            return None
        return now - self._cycle_started_at

    def _track_surplus(self, now: datetime, observed: Observed) -> None:
        on = observed.surplus_on
        if on:
            if self._surplus_on_since is None:
                self._surplus_on_since = now
            self._surplus_off_since = None
        else:
            # Unknown counts as no surplus (section 5.8).
            if self._surplus_off_since is None:
                self._surplus_off_since = now
            self._surplus_on_since = None

        grant = self._grant_in_force(now)
        mode_in_force = any(
            run.directive.type == "mode" and run.in_force(now)
            for run in self.runs.values()
        )
        if self._surplus_raise_raw is not None:
            run_time = self._cycle_run_time(now)
            lower = (
                not observed.compressor_on
                or grant is None
                or mode_in_force
                or (
                    run_time is not None
                    and run_time
                    >= timedelta(
                        minutes=self.capabilities.min_run_before_stop_min
                    )
                    and self._surplus_off_since is not None
                    and now - self._surplus_off_since
                    >= SURPLUS_OFF_BEFORE_LOWER
                )
            )
            if lower:
                self._surplus_raise_raw = None
            return
        if (
            grant is not None
            and observed.compressor_on
            and observed.mode == "heat_pump"
            and not mode_in_force
            and not self._surplus_raised_in_cycle
            and self._surplus_on_since is not None
            and now - self._surplus_on_since >= SURPLUS_ON_BEFORE_RAISE
            and grant.directive.temperature_raw is not None
        ):
            limit = self.capabilities.setpoint_max_raw
            target = grant.directive.temperature_raw
            self._surplus_raise_raw = (
                min(target, limit) if limit is not None else target
            )
            self._surplus_raised_in_cycle = True

    def _grant_in_force(self, now: datetime) -> DirectiveRun | None:
        for run in self.runs.values():
            if run.directive.type == "surplus_grant" and run.in_force(now):
                return run
        return None

    # Precedence, entries, the daily revert, boundaries

    def _track_precedence(self, now: datetime, observed: Observed) -> None:
        suspended = observed.suspended_by
        if suspended and not self.suspended_by:
            # Pending entries are deleted so none fire later (section 5.10).
            self.owned = []
        self.suspended_by = suspended

    def _drop_fired_entries(self, now: datetime) -> None:
        """Entries repeat weekly, so each is deleted once it has fired."""
        self.owned = [e for e in self.owned if e.fires_at > now]

    def _daily_revert_time(self, local: datetime) -> datetime:
        hour, minute = (
            int(part) for part in self.capabilities.daily_revert_time.split(":")
        )
        return local.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def next_daily_revert(self, now: datetime) -> datetime:
        """The next occurrence of the daily revert time, as an aware time."""
        local = now.astimezone(self.tz)
        candidate = self._daily_revert_time(local)
        if candidate <= local:
            candidate = self._daily_revert_time(local + timedelta(days=1))
        return candidate

    def _daily_revert(
        self, previous: datetime | None, now: datetime, observed: Observed
    ) -> None:
        if previous is None:
            return
        revert = self.next_daily_revert(previous)
        if revert > now:
            return
        # The device-side entry fired: the heater is at the baseline, any
        # override is over, and the intent is re-applied after a short
        # displacement.
        self._record_restore(RESTORE_DAILY_REVERT, now, observed)
        self.overrides = {}
        self.pending_restore = None
        self._revert_hold_until = revert + DAILY_REVERT_REAPPLY
        self._reset_expected(observed)

    def _directive_boundaries(self, now: datetime, observed: Observed) -> None:
        in_force = {did for did, run in self.runs.items() if run.in_force(now)}
        ended = self._in_force_ids - in_force
        if ended and not self.pending_restore:
            self.pending_restore = RESTORE_EXPIRY
        self._in_force_ids = in_force

        for run in self.runs.values():
            if (
                run.directive.type == "hold_off"
                and run.in_force(now)
                and run.hold_setpoint_raw is None
                and observed.upper_tank_raw is not None
            ):
                self._start_hold_off(run, observed)

    def _start_hold_off(self, run: DirectiveRun, observed: Observed) -> None:
        """Fix the hold-off setpoint from the tank at start (section 5.6)."""
        margin_raw = math.ceil(self.capabilities.hold_off_margin_f * 10 / 9)
        target = int(observed.upper_tank_raw or 0) - margin_raw
        floor = self.capabilities.setpoint_min_raw
        if floor is not None and target < floor:
            run.status = STATUS_REJECTED
            run.reason = REASON_FLOOR_PREVENTS_HOLD_OFF
            self.owned = [
                e for e in self.owned if e.directive_id != run.directive.id
            ]
            return
        run.hold_setpoint_raw = target

    # Composition

    def _compose(self, now: datetime, observed: Observed) -> Wanted:
        baseline = self.baseline
        if baseline is None:
            return Wanted(suspended_by=self.suspended_by)
        mode = baseline.mode
        setpoint = baseline.setpoint_raw
        tou: bool = baseline.tou_enabled
        holds: list[str] = []

        if self.suspended_by:
            schedule = wanted_schedule(
                baseline.reservations, (), enabled=baseline.reservations_enabled
            )
            return Wanted(
                mode=mode,
                setpoint_raw=setpoint,
                tou_on=tou,
                schedule=schedule,
                suspended_by=self.suspended_by,
            )

        displaced = (
            self._revert_hold_until is not None
            and now < self._revert_hold_until
        )
        surplus_raised = False
        if not displaced:
            for run in self._runs_in_order():
                d = run.directive
                if not run.in_force(now):
                    continue
                if d.type == "mode":
                    mode = str(d.mode)
                    if self.capabilities.tou_off_for_mode and observed.tou_on:
                        tou = False
                elif d.type == "charge" and not run.complete:
                    setpoint = int(d.temperature_raw or setpoint)
                elif d.type == "hold_off" and run.hold_setpoint_raw is not None:
                    setpoint = run.hold_setpoint_raw
            if self._surplus_raise_raw is not None:
                setpoint = max(setpoint, self._surplus_raise_raw)
                surplus_raised = True
        else:
            holds.append("daily_revert")

        entries = list(self.owned)
        if entries or self.overrides or self._non_baseline(now):
            entries.append(
                OwnedEntry(
                    kind=KIND_DAILY_REVERT,
                    directive_id=None,
                    fires_at=self.next_daily_revert(now),
                    mode=baseline.mode,
                    param=baseline.setpoint_raw,
                    week=ALL_DAYS,
                )
            )
        entries = merge_by_slot(entries)
        schedule = wanted_schedule(
            baseline.reservations,
            entries,
            enabled=baseline.reservations_enabled or bool(entries),
        )
        return Wanted(
            mode=mode,
            setpoint_raw=setpoint,
            tou_on=tou,
            schedule=schedule,
            entries=tuple(entries),
            holds=tuple(holds),
            surplus_raised=surplus_raised,
        )

    def _non_baseline(self, now: datetime) -> bool:
        return any(run.in_force(now) for run in self.runs.values())

    def _apply_compressor_rules(
        self, now: datetime, observed: Observed, wanted: Wanted
    ) -> Wanted:
        """Never stop a running compressor early; no reversal in a cycle."""
        if (
            "daily_revert" in wanted.holds
            or wanted.suspended_by
            or self._unconditional
        ):
            return wanted
        previous = self.wanted.setpoint_raw
        if previous is None:
            previous = observed.setpoint_raw
        if wanted.setpoint_raw is None or previous is None:
            return wanted
        if not observed.compressor_on:
            return wanted

        holds = list(wanted.holds)
        if wanted.setpoint_raw < previous:
            would_stop = (
                observed.upper_tank_raw is not None
                and wanted.setpoint_raw < observed.upper_tank_raw
            )
            if would_stop:
                run_time = self._cycle_run_time(now)
                min_run = timedelta(
                    minutes=self.capabilities.min_run_before_stop_min
                )
                if self._cycle_protected:
                    holds.append(HOLD_REQUEST_CYCLE)
                elif run_time is None or run_time < min_run:
                    holds.append(HOLD_COMPRESSOR_MIN_RUN)
                else:
                    self._lowered_in_cycle = True
            if holds != list(wanted.holds):
                return replace(
                    wanted, setpoint_raw=previous, holds=tuple(holds)
                )
        elif wanted.setpoint_raw > previous and self._lowered_in_cycle:
            holds.append(HOLD_NO_REVERSAL)
            return replace(wanted, setpoint_raw=previous, holds=tuple(holds))
        return wanted

    # Overrides

    @staticmethod
    def _observed_field(observed: Observed, name: str) -> Any:
        if name == FIELD_SETPOINT:
            return observed.setpoint_raw
        if name == FIELD_MODE:
            return observed.mode
        if name == FIELD_TOU:
            return observed.tou_on
        if observed.reservations is None:
            return None
        return schedule_hash(
            {
                "reservation_use": 2 if observed.reservations_enabled else 1,
                "reservation": list(observed.reservations),
            }
        )

    def _reset_expected(self, observed: Observed) -> None:
        for name in OVERRIDE_FIELDS:
            value = self._observed_field(observed, name)
            if value is not None:
                self.expected[name] = value

    def _track_overrides(self, now: datetime, observed: Observed) -> None:
        if self.suspended_by:
            # Vacation and power-off are never overrides (section 5.10).
            return
        for name in OVERRIDE_FIELDS:
            value = self._observed_field(observed, name)
            if value is None:
                continue
            if name not in self.expected:
                self.expected[name] = value
                continue
            expected = self.expected[name]
            override = self.overrides.get(name)
            if override is None:
                if value != expected and not self._baseline_entry_fired(
                    now, observed
                ):
                    _LOGGER.info(
                        "Override detected on %s: %r (expected %r)",
                        name,
                        value,
                        expected,
                    )
                    self.overrides[name] = Override(
                        field=name,
                        value=value,
                        detected_at=now,
                        expires_at=self.next_daily_revert(now),
                    )
                elif value != expected:
                    self.expected[name] = value
            elif value == expected:
                # The person reverted it.
                del self.overrides[name]
                self.pending_restore = RESTORE_OVERRIDE_EXPIRED
            elif value != override.value:
                self.overrides[name] = replace(override, value=value)

    def _baseline_entry_fired(self, now: datetime, observed: Observed) -> bool:
        """Whether a baseline reservation entry explains the observed state."""
        if self.baseline is None:
            return False
        local = now.astimezone(self.tz)
        for entry in self.baseline.reservations:
            if entry.get("enable") != 2 or not entry.get("week", 0) & week_bit(
                local
            ):
                continue
            fired_at = local.replace(
                hour=int(entry["hour"]),
                minute=int(entry["min"]),
                second=0,
                microsecond=0,
            )
            if not (fired_at <= local <= fired_at + BASELINE_ENTRY_WINDOW):
                continue
            if observed.setpoint_raw == entry.get("param") or (
                observed.mode is not None
                and MODE_ID_TO_NAME.get(int(entry.get("mode", 0)))
                == observed.mode
            ):
                self._reset_expected(observed)
                return True
        return False

    def _apply_overrides(self, observed: Observed, wanted: Wanted) -> Wanted:
        """An overridden field is left alone, even to restore."""
        if not self.overrides:
            return wanted
        changes: dict[str, Any] = {}
        if FIELD_SETPOINT in self.overrides:
            changes["setpoint_raw"] = self.overrides[FIELD_SETPOINT].value
        if FIELD_MODE in self.overrides:
            changes["mode"] = self.overrides[FIELD_MODE].value
        if FIELD_TOU in self.overrides:
            changes["tou_on"] = self.overrides[FIELD_TOU].value
        if (
            FIELD_RESERVATIONS in self.overrides
            and observed.reservations is not None
        ):
            changes["schedule"] = {
                "reservation_use": 2 if observed.reservations_enabled else 1,
                "reservation": list(observed.reservations),
            }
            changes["entries"] = ()
        return replace(wanted, **changes)

    # Restores

    def _settle_pending_restore(
        self, now: datetime, observed: Observed, wanted: Wanted
    ) -> None:
        if self.pending_restore is None or self.baseline is None:
            return
        at_baseline = (
            wanted.mode == self.baseline.mode
            and wanted.setpoint_raw == self.baseline.setpoint_raw
        ) or wanted.suspended_by is not None
        if at_baseline and not any(
            h in wanted.holds
            for h in (HOLD_COMPRESSOR_MIN_RUN, HOLD_REQUEST_CYCLE)
        ):
            self._record_restore(self.pending_restore, now, observed)
            self.pending_restore = None

    def _record_restore(
        self, reason: str, now: datetime, observed: Observed
    ) -> None:
        matches: bool | None = None
        if self.baseline is not None and observed.mode is not None:
            matches = (
                observed.mode == self.baseline.mode
                and observed.setpoint_raw == self.baseline.setpoint_raw
            )
        self.last_restore = Restore(
            reason=reason, at=now, matches_baseline=matches
        )
        _LOGGER.info(
            "Restore (%s)%s: baseline %s",
            reason,
            " simulated" if self.shadow else "",
            "matched"
            if matches
            else "not matched"
            if matches is False
            else "unknown",
        )

    # Timing

    def _next_event(self, now: datetime) -> datetime | None:
        times: list[datetime] = [self.next_daily_revert(now)]
        if self.intent is not None:
            times.append(self.intent.valid_until)
            for run in self.runs.values():
                if not run.admitted:
                    continue
                times.extend(
                    t
                    for t in (run.directive.start, run.directive.end)
                    if t > now
                )
        if self._revert_hold_until and self._revert_hold_until > now:
            times.append(self._revert_hold_until)
        if self._surplus_on_since and self._grant_in_force(now):
            times.append(self._surplus_on_since + SURPLUS_ON_BEFORE_RAISE)
        if self._surplus_off_since and self._surplus_raise_raw is not None:
            times.append(self._surplus_off_since + SURPLUS_OFF_BEFORE_LOWER)
        if self._cycle_started_at is not None and (
            self.pending_restore or self._surplus_raise_raw is not None
        ):
            times.append(
                self._cycle_started_at
                + timedelta(minutes=self.capabilities.min_run_before_stop_min)
            )
        future = [t for t in times if t > now]
        return min(future) if future else None
