"""The planner (spec section 5, issue #158), in shadow.

Every scenario drives the planner with times and `Observed` snapshots only,
as the device controller does, so the rules are tested without Home
Assistant, timers or a device.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.nwp500.control.engine import (
    CLEANUP_DEFER,
    REPORT_FOREIGN,
    REPORT_MODE,
    REPORT_REMOVED,
    REPORT_SETPOINT,
    REPORT_SWITCHED_OFF,
    WRITE_DISABLE,
    WRITE_GRANT_LOWER,
    WRITE_GRANT_RAISE,
    WRITE_NEAR_TERM,
    WRITE_PLAN,
    WRITE_POWER_OFF,
    WRITE_PRECEDENCE_EXIT,
    Planner,
    State,
)
from custom_components.nwp500.control.entries import (
    KIND_GRANT_LOWER,
    KIND_GRANT_RAISE,
    KIND_GUARD,
    KIND_NEAR_TERM,
    KIND_PLAN,
    KIND_PRECEDENCE_EXIT,
    near_term_minute,
    schedule_hash,
)
from custom_components.nwp500.control.evaluate import (
    REASON_BEYOND_HORIZON,
    REASON_BOUNDS_UNKNOWN,
    REASON_ENTRY_BUDGET,
    WARNING_MODE_IN_TOU_WINDOW,
    WARNING_MOVED,
    check_plan,
)
from custom_components.nwp500.control.intent import parse_plan
from custom_components.nwp500.control.observed import Observed
from custom_components.nwp500.control.owner import OwnerProgram

from .conftest import capabilities, grant, make_document, segment

TZ = ZoneInfo("America/Los_Angeles")
# A Monday morning, local time.
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=TZ)
MONDAY = 64
# 119 half-degrees is 59.5 degC / 139.1 degF.
OWNER_SETPOINT = 119
OWNER = OwnerProgram(
    mode="energy_saver",
    setpoint_raw=OWNER_SETPOINT,
    reservations_enabled=False,
)
SURPLUS = {"control_surplus_entity": "binary_sensor.surplus"}


def obs(**overrides) -> Observed:
    """The device on the owner's state, idle, reservations switched off."""
    values = {
        "mode": "energy_saver",
        "setpoint_raw": OWNER_SETPOINT,
        "tou_on": False,
        "compressor_on": False,
        "upper_tank_raw": 115,
        "reservations_enabled": False,
        "reservations": (),
        "surplus_on": None,
    }
    values.update(overrides)
    return Observed(**values)


def minutes(m: float, base: datetime = NOW) -> datetime:
    return base + timedelta(minutes=m)


def run(planner: Planner, now: datetime, observed: Observed | None = None):
    """One planning pass, committing its write as the controller does."""
    write = planner.step(now, observed or obs())
    if write is not None:
        planner.commit(write)
    return write


def give(
    planner: Planner,
    segments: list[dict],
    *,
    grants: list[dict] | None = None,
    now: datetime = NOW,
    observed: Observed | None = None,
    restoring: bool = False,
    **document_kwargs,
):
    """Hand the planner a checked plan, and run a pass."""
    plan = parse_plan(
        make_document(now, segments, grants=grants, **document_kwargs)
    )
    check_plan(plan, planner.capabilities)
    planner.set_plan(plan, now, observed or obs(), restoring=restoring)
    return run(planner, now, observed)


def planner_with(
    segments: list[dict] | None = None,
    *,
    grants: list[dict] | None = None,
    observed: Observed | None = None,
    owner: OwnerProgram | None = OWNER,
    shadow: bool = True,
    **options,
) -> Planner:
    planner = Planner(capabilities(**options), TZ, shadow=shadow)
    planner.owner = owner
    run(planner, NOW, observed)
    if segments is not None:
        give(planner, segments, grants=grants, observed=observed)
    return planner


def kinds(planner: Planner) -> list[tuple[str, str | None, str]]:
    """(kind, serves, HH:MM) of the owned entries, in time order."""
    return [
        (e.kind, e.serves, e.fires_at.astimezone(TZ).strftime("%H:%M"))
        for e in sorted(planner.owned, key=lambda e: e.fires_at)
    ]


def statuses(planner: Planner) -> dict[str, tuple[str, str | None]]:
    return {a.id: (a.status, a.reason) for a in planner.ack("i-1").segments}


class TestSpecExample:
    """Section 3.6, on the Sunday it describes."""

    SUNDAY = datetime(2026, 10, 4, 5, 0, 12, tzinfo=TZ)

    def _planner(self) -> Planner:
        base = self.SUNDAY.replace(second=0)
        planner = Planner(capabilities(**SURPLUS), TZ)
        planner.owner = OWNER
        plan = parse_plan(
            make_document(
                self.SUNDAY,
                [
                    segment(
                        base,
                        "s1",
                        0,
                        mode="heat_pump",
                        setpoint="min",
                        purpose="hold_off",
                    ),
                    segment(base, "s2", 330, setpoint_f=140, purpose="charge"),
                    segment(
                        base, "s3", 570, mode="energy_saver", setpoint_f=135
                    ),
                    segment(base, "s4", 1020, setpoint="min"),
                ],
                grants=[grant(base, "g1", 360, 540, max_f=146)],
            )
        )
        check_plan(plan, planner.capabilities)
        planner.set_plan(plan, self.SUNDAY, obs())
        return planner

    def test_one_list_is_written(self):
        planner = self._planner()
        write = run(planner, self.SUNDAY)

        assert write is not None
        assert write.simulated is True
        assert kinds(planner) == [
            (KIND_NEAR_TERM, "s1", "05:03"),
            (KIND_PLAN, "s2", "10:30"),
            (KIND_PLAN, "s3", "14:30"),
            (KIND_PLAN, "s4", "22:00"),
        ]
        entries = [
            e.as_entry()
            for e in sorted(planner.owned, key=lambda e: e.fires_at)
        ]
        assert entries == [
            {
                "enable": 2,
                "week": 128,
                "hour": 5,
                "min": 3,
                "mode": 1,
                "param": 81,
            },
            {
                "enable": 2,
                "week": 128,
                "hour": 10,
                "min": 30,
                "mode": 1,
                "param": 120,
            },
            {
                "enable": 2,
                "week": 128,
                "hour": 14,
                "min": 30,
                "mode": 3,
                "param": 114,
            },
            {
                "enable": 2,
                "week": 128,
                "hour": 22,
                "min": 0,
                "mode": 3,
                "param": 81,
            },
        ]
        assert planner.programmed_complete is True
        assert planner.wanted_state(self.SUNDAY) == State("heat_pump", 81)

    def test_the_surplus_raise(self):
        planner = self._planner()
        run(planner, self.SUNDAY)
        base = self.SUNDAY.replace(second=0)
        running = obs(
            mode="heat_pump",
            setpoint_raw=120,
            compressor_on=True,
            surplus_on=True,
        )

        run(planner, minutes(380, base), running)  # 11:20: surplus appears
        write = run(planner, minutes(390, base), running)  # 11:30

        assert write is not None
        assert write.reason == WRITE_GRANT_RAISE
        added = {
            (e.kind, e.fires_at.strftime("%H:%M"), e.mode, e.setpoint_raw)
            for e in write.added
        }
        assert added == {
            (KIND_GRANT_RAISE, "11:32", "heat_pump", 127),
            (KIND_GUARD, "14:00", "heat_pump", 120),
        }

        stopped = obs(
            mode="heat_pump",
            setpoint_raw=127,
            compressor_on=False,
            surplus_on=True,
        )
        write = run(planner, minutes(460, base), stopped)  # 12:40

        assert write is not None
        assert write.reason == WRITE_GRANT_LOWER
        assert {
            (e.kind, e.fires_at.strftime("%H:%M")) for e in write.added
        } == {(KIND_GRANT_LOWER, "12:42")}
        # The fired raise goes with the guard. Entries that fired earlier
        # were removed with the 11:30 write.
        removed = {
            (e.kind, e.fires_at.strftime("%H:%M")) for e in write.removed
        }
        assert removed == {(KIND_GUARD, "14:00"), (KIND_GRANT_RAISE, "11:32")}


class TestTranslation:
    """Section 5.2."""

    def test_future_segments_become_entries(self):
        planner = planner_with(
            [
                segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140),
                segment(NOW, "b", 120, setpoint_f=130),
            ]
        )
        assert kinds(planner) == [
            (KIND_PLAN, "a", "11:00"),
            (KIND_PLAN, "b", "12:00"),
        ]
        # Before the first segment, what was in force continues.
        assert planner.wanted_state(NOW) == State(
            "energy_saver", OWNER_SETPOINT
        )
        assert planner.wanted_state(minutes(61)) == State("energy_saver", 120)

    def test_a_segment_in_force_matching_the_device_needs_no_entry(self):
        planner = planner_with(
            [
                segment(NOW, "now", -5, mode="energy_saver", setpoint_f=139.1),
                segment(NOW, "later", 60, setpoint_f=130),
            ]
        )
        assert kinds(planner) == [(KIND_PLAN, "later", "11:00")]

    def test_merged_segments_need_one_entry(self):
        planner = planner_with(
            [
                segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140),
                segment(NOW, "b", 90, setpoint_f=140, purpose="same"),
                segment(NOW, "c", 120, setpoint_f=130),
            ]
        )
        assert kinds(planner) == [
            (KIND_PLAN, "a", "11:00"),
            (KIND_PLAN, "c", "12:00"),
        ]
        assert statuses(planner)["b"] == ("merged", None)

    def test_min_resolves_to_the_floor_option(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="energy_saver", setpoint="min")],
            control_setpoint_min_f=120,
        )
        assert planner.owned[0].setpoint_raw == 98

    def test_min_waits_for_the_bounds(self):
        from custom_components.nwp500.control.capabilities import (
            build_capabilities,
        )

        planner = Planner(
            build_capabilities(
                {"control_allowed_modes": ["energy_saver"]},
                features=None,
                feature_version="v",
                telemetry={},
            ),
            TZ,
        )
        planner.owner = OWNER
        give(
            planner,
            [segment(NOW, "a", 60, mode="energy_saver", setpoint="min")],
        )
        assert planner.owned == []
        assert statuses(planner)["a"] == ("scheduled", REASON_BOUNDS_UNKNOWN)

    def test_a_segment_too_close_is_asserted_when_it_begins(self):
        planner = planner_with(
            [segment(NOW, "soon", 1, mode="energy_saver", setpoint_f=140)]
        )
        assert planner.owned == []
        run(planner, minutes(1))
        assert kinds(planner) == [(KIND_NEAR_TERM, "soon", "10:03")]

    def test_a_near_term_entry_is_skipped_if_the_next_segment_is_sooner(self):
        planner = planner_with(
            [
                segment(NOW, "now", -5, mode="energy_saver", setpoint_f=140),
                segment(NOW, "next", 2, setpoint_f=130),
            ]
        )
        assert [k for k, _, _ in kinds(planner)] == [KIND_PLAN]

    def test_mode_change_in_a_tou_window_is_flagged(self):
        periods = (
            {
                "season": 4095,
                "week": 254,
                "start_hour": 0,
                "start_min": 0,
                "end_hour": 15,
                "end_min": 59,
                "price_max": 28000,
            },
            {
                "season": 4095,
                "week": 254,
                "start_hour": 16,
                "start_min": 0,
                "end_hour": 20,
                "end_min": 59,
                "price_max": 32000,
            },
        )
        observed = obs(tou_on=True, tou_periods=periods)
        planner = planner_with(
            [
                segment(NOW, "noon", 120, mode="heat_pump", setpoint_f=140),
                segment(NOW, "peak", 420, mode="energy_saver", setpoint_f=140),
                segment(NOW, "peak-setpoint", 480, setpoint_f=130),
            ],
            observed=observed,
        )
        warnings = {a.id: a.warnings for a in planner.ack("i").segments}
        assert warnings["noon"] == ()
        assert warnings["peak"] == (WARNING_MODE_IN_TOU_WINDOW,)
        assert warnings["peak-setpoint"] == ()

    def test_an_enabled_entry_in_the_slot_moves_the_plan_entry(self):
        foreign = {
            "enable": 2,
            "week": MONDAY | 2,
            "hour": 11,
            "min": 0,
            "mode": 3,
            "param": 110,
        }
        observed = obs(reservations=(foreign,))
        planner = planner_with(
            [segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140)],
            observed=observed,
        )
        assert kinds(planner) == [(KIND_PLAN, "a", "11:01")]
        ack = {a.id: a for a in planner.ack("i").segments}
        assert ack["a"].warnings == (WARNING_MOVED,)

    @pytest.mark.parametrize("owner_entry", [True, False])
    def test_a_switched_off_entry_shares_the_slot(self, owner_entry):
        """Section 5.2.6: only enabled entries hold a slot.

        The owner's entries are switched off while live, and a foreign
        entry may be switched off by its own flag. The device stores both
        and fires only the enabled one (section 8, test 10).
        """
        other = {
            "enable": 2 if owner_entry else 1,
            "week": MONDAY | 2,
            "hour": 11,
            "min": 0,
            "mode": 3,
            "param": 110,
        }
        observed = obs(reservations=(other,))
        owner = (
            OwnerProgram("energy_saver", OWNER_SETPOINT, False, (other,))
            if owner_entry
            else OWNER
        )
        planner = planner_with(
            [segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140)],
            observed=observed,
            owner=owner,
        )
        assert kinds(planner) == [(KIND_PLAN, "a", "11:00")]


class TestHorizonAndBudget:
    """Section 5.3."""

    def test_segments_beyond_the_horizon_are_scheduled(self):
        planner = planner_with(
            [
                segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140),
                segment(NOW, "far", 145 * 60, setpoint_f=130),
            ]
        )
        assert kinds(planner) == [(KIND_PLAN, "a", "11:00")]
        assert statuses(planner)["far"] == ("scheduled", REASON_BEYOND_HORIZON)
        assert planner.programmed_until == minutes(145 * 60)
        assert planner.programmed_complete is False
        assert planner.next_event_at == minutes(60)

        run(planner, minutes(61))
        run(planner, minutes(60 + 1 + 60))
        assert statuses(planner)["far"] == ("shadow", None)
        assert planner.programmed_complete is True

    def _alternating(self, count: int, step: int = 60) -> list[dict]:
        segments = [
            segment(NOW, "s0", step, mode="energy_saver", setpoint_f=140)
        ]
        for i in range(1, count):
            segments.append(
                segment(
                    NOW,
                    f"s{i}",
                    step * (i + 1),
                    setpoint_f=140 if i % 2 == 0 else 130,
                )
            )
        return segments

    def test_segments_beyond_the_budget_wait_in_order(self):
        planner = planner_with(
            self._alternating(7), control_reservation_entry_limit=7
        )
        # 7 entries, 2 kept in reserve: 5 programmed.
        assert [s for _, s, _ in kinds(planner)] == [
            "s0",
            "s1",
            "s2",
            "s3",
            "s4",
        ]
        assert statuses(planner)["s5"] == ("scheduled", REASON_ENTRY_BUDGET)
        assert planner.programmed_until == minutes(6 * 60)
        assert planner.scheduled_count == 2

    def test_a_fired_entry_makes_room(self):
        planner = planner_with(
            self._alternating(7), control_reservation_entry_limit=7
        )
        write = run(planner, minutes(61))
        assert write is not None
        assert write.reason == WRITE_PLAN
        assert [e.serves for e in write.removed] == ["s0"]
        assert [e.serves for e in write.added] == ["s5"]

    def test_owner_entries_count_against_the_budget(self):
        owner_entries = tuple(
            {
                "enable": 2,
                "week": 2,
                "hour": h,
                "min": 0,
                "mode": 3,
                "param": 110,
            }
            for h in (6, 7)
        )
        observed = obs(reservations=owner_entries)
        owner = OwnerProgram(
            "energy_saver", OWNER_SETPOINT, False, owner_entries
        )
        planner = planner_with(
            self._alternating(7),
            observed=observed,
            owner=owner,
            control_reservation_entry_limit=7,
        )
        assert len(planner.owned) == 3

    def test_cleanup_alone_waits_up_to_a_day(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140)]
        )
        assert run(planner, minutes(62)) is None
        assert len(planner.owned) == 1
        assert planner.next_event_at == minutes(60) + CLEANUP_DEFER
        write = run(planner, minutes(60) + CLEANUP_DEFER)
        assert write is not None
        assert write.reason == "cleanup"
        assert planner.owned == []


class TestReplacingAPlan:
    """Section 5.6."""

    SEGMENTS = [
        segment(NOW, "now", -5, mode="heat_pump", setpoint_f=140),
        segment(NOW, "later", 60, setpoint_f=130),
    ]

    def test_republishing_unchanged_writes_nothing(self):
        planner = planner_with(self.SEGMENTS)
        assert (
            give(planner, self.SEGMENTS, now=minutes(1), intent_id="i-2")
            is None
        )

    def test_a_new_state_now_gets_a_near_term_entry(self):
        planner = planner_with(self.SEGMENTS)
        write = give(
            planner,
            [
                segment(NOW, "now", -5, mode="heat_pump", setpoint_f=145),
                segment(NOW, "later", 60, setpoint_f=130),
            ],
            now=minutes(5),
            intent_id="i-2",
        )
        assert write is not None
        assert write.reason == WRITE_NEAR_TERM
        assert {
            (e.kind, e.fires_at.strftime("%H:%M")) for e in write.added
        } == {(KIND_NEAR_TERM, "10:07")}
        # The old plan's unfired near-term entry went; the later entry stayed.
        assert (KIND_PLAN, "later", "11:00") in kinds(planner)

    def test_a_dropped_segment_is_removed(self):
        planner = planner_with(self.SEGMENTS)
        write = give(
            planner, self.SEGMENTS[:1], now=minutes(1), intent_id="i-2"
        )
        assert write is not None
        # The unfired near-term entry for the kept segment stays.
        assert [e.serves for e in write.removed] == ["later"]

    def test_an_empty_plan_withdraws_everything_and_keeps_the_state(self):
        planner = planner_with(self.SEGMENTS)
        give(planner, [], now=minutes(5), intent_id="i-2")
        assert planner.owned == []
        assert planner.wanted_state(minutes(5)) == State("heat_pump", 120)

    def test_restoring_at_start_up_writes_nothing_for_the_segment_in_force(
        self,
    ):
        planner = Planner(capabilities(), TZ)
        planner.owner = OWNER
        run(planner, NOW)
        write = give(planner, self.SEGMENTS, restoring=True)
        assert [e.kind for e in write.added] == [KIND_PLAN]


class TestPrecedence:
    """Section 5.9."""

    def test_no_writes_while_suspended_and_a_re_assert_after(self):
        planner = planner_with(
            [segment(NOW, "a", -5, mode="energy_saver", setpoint_f=139.1)]
        )
        assert planner.owned == []
        assert run(planner, minutes(1), obs(mode="vacation")) is None
        give(
            planner,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_f=139.1),
                segment(NOW, "b", 60, setpoint_f=130),
            ],
            now=minutes(2),
            observed=obs(mode="vacation"),
            intent_id="i-2",
        )
        assert planner.owned == []
        write = run(planner, minutes(3))
        assert write is not None
        assert write.reason == WRITE_PRECEDENCE_EXIT
        assert {e.kind for e in write.added} == {
            KIND_PRECEDENCE_EXIT,
            KIND_PLAN,
        }
        assert planner.reports == {}

    def test_anti_legionella_suspends_without_a_re_assert(self):
        planner = planner_with(
            [segment(NOW, "a", -5, mode="energy_saver", setpoint_f=139.1)]
        )
        assert run(planner, minutes(1), obs(anti_legionella_busy=True)) is None
        assert run(planner, minutes(2)) is None


class TestPeoplesChanges:
    """Section 5.10."""

    OWNER_ENTRY = {
        "enable": 2,
        "week": MONDAY,
        "hour": 10,
        "min": 30,
        "mode": 3,
        "param": 110,
    }
    OWNER_PROGRAM = OwnerProgram(
        "energy_saver", OWNER_SETPOINT, True, (OWNER_ENTRY,)
    )

    def test_an_unexplained_change_is_reported_not_undone(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140)]
        )
        assert run(planner, minutes(5), obs(setpoint_raw=100)) is None
        report = planner.reports[REPORT_SETPOINT]
        assert report.value == 100
        assert report.detected_at == minutes(5)
        run(planner, minutes(6), obs(mode="heat_pump", setpoint_raw=100))
        assert planner.reports[REPORT_MODE].value == "heat_pump"

    def test_an_entry_firing_explains_a_change(self):
        observed = obs(
            reservations_enabled=True, reservations=(self.OWNER_ENTRY,)
        )
        planner = planner_with(observed=observed, owner=self.OWNER_PROGRAM)
        run(
            planner,
            minutes(31),
            obs(
                reservations_enabled=True,
                reservations=(self.OWNER_ENTRY,),
                setpoint_raw=110,
            ),
        )
        assert planner.reports == {}

    def test_a_report_lasts_until_the_next_entry_fires(self):
        observed = obs(
            reservations_enabled=True, reservations=(self.OWNER_ENTRY,)
        )
        planner = planner_with(observed=observed, owner=self.OWNER_PROGRAM)
        run(
            planner,
            minutes(5),
            obs(
                reservations_enabled=True,
                reservations=(self.OWNER_ENTRY,),
                setpoint_raw=100,
            ),
        )
        assert REPORT_SETPOINT in planner.reports
        run(
            planner,
            minutes(31),
            obs(
                reservations_enabled=True,
                reservations=(self.OWNER_ENTRY,),
                setpoint_raw=110,
            ),
        )
        assert REPORT_SETPOINT not in planner.reports

    def test_switching_reservations_off_is_reported(self):
        planner = planner_with(observed=obs(reservations_enabled=True))
        run(planner, minutes(1), obs(reservations_enabled=False))
        assert REPORT_SWITCHED_OFF in planner.reports
        run(planner, minutes(2), obs(reservations_enabled=True))
        assert REPORT_SWITCHED_OFF not in planner.reports

    def test_an_added_entry_is_foreign(self):
        planner = planner_with()
        added = {
            "enable": 2,
            "week": 2,
            "hour": 7,
            "min": 0,
            "mode": 1,
            "param": 100,
        }
        run(planner, minutes(1), obs(reservations=(added,)))
        (report,) = planner.reports.values()
        assert report.field == REPORT_FOREIGN
        assert report.value == added
        run(planner, minutes(2), obs(reservations=()))
        assert planner.reports == {}

    def test_vacation_is_not_a_change(self):
        planner = planner_with()
        run(planner, minutes(1), obs(mode="vacation"))
        run(planner, minutes(2), obs(mode="energy_saver", setpoint_raw=100))
        assert planner.reports == {}

    def test_live_a_deleted_entry_is_not_restored(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140)],
            shadow=False,
        )
        (entry,) = planner.owned
        on_device = obs(
            reservations_enabled=True, reservations=(entry.as_entry(),)
        )
        assert run(planner, minutes(1), on_device) is None
        assert (
            run(
                planner,
                minutes(2),
                obs(reservations_enabled=True, reservations=()),
            )
            is None
        )
        assert planner.owned == []
        assert f"{REPORT_REMOVED}:a" in planner.reports
        assert statuses(planner)["a"][0] == "removed"
        assert planner.ack("i").state == "partly_programmed"


class TestSurplusGrants:
    """Section 5.7."""

    RUNNING = obs(
        mode="heat_pump", setpoint_raw=120, compressor_on=True, surplus_on=True
    )

    def _planner(self, segments=None, grant_max=146, **options) -> Planner:
        planner = planner_with(
            segments
            or [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)],
            grants=[grant(NOW, "g", -5, 180, max_f=grant_max)],
            observed=self.RUNNING,
            **{**SURPLUS, **options},
        )
        return planner

    def test_raises_after_ten_minutes_with_a_guard(self):
        planner = self._planner()
        assert run(planner, minutes(9), self.RUNNING) is None
        write = run(planner, minutes(10), self.RUNNING)
        assert write is not None
        assert write.reason == WRITE_GRANT_RAISE
        assert {
            (e.kind, e.fires_at.strftime("%H:%M"), e.setpoint_raw)
            for e in write.added
        } == {
            (KIND_GRANT_RAISE, "10:12", 127),
            (KIND_GUARD, "13:00", 120),
        }
        assert planner.wanted_state(minutes(11)) == State("heat_pump", 120)
        assert planner.wanted_state(minutes(13)) == State("heat_pump", 127)
        grants = {g.id: g.status for g in planner.ack("i").grants}
        assert grants == {"g": "raised"}

    def test_no_guard_when_a_segment_ends_the_raise_first(self):
        planner = self._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "next", 60, setpoint_f=135),
            ]
        )
        write = run(planner, minutes(10), self.RUNNING)
        assert {e.kind for e in write.added} == {KIND_GRANT_RAISE}
        run(planner, minutes(61), self.RUNNING)
        assert planner.raise_state is None

    def test_capped_at_the_setpoint_maximum(self):
        """The cap still applies if the bounds tighten after acceptance.

        A grant is checked against the bounds when accepted.
        """
        planner = self._planner()
        planner.capabilities = capabilities(
            control_setpoint_max_f=142, **SURPLUS
        )
        run(planner, minutes(10), self.RUNNING)
        assert planner.raise_state is not None
        assert planner.raise_state.entry.setpoint_raw == 122

    def test_no_raise_below_the_segment(self):
        planner = self._planner(grant_max=130)
        assert run(planner, minutes(10), self.RUNNING) is None

    def test_never_to_start_a_cycle(self):
        planner = self._planner()
        idle = obs(
            mode="heat_pump",
            setpoint_raw=120,
            compressor_on=False,
            surplus_on=True,
        )
        assert run(planner, minutes(10), idle) is None

    def test_only_in_heat_pump(self):
        planner = planner_with(
            [segment(NOW, "s", -5, mode="energy_saver", setpoint_f=140)],
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            observed=self.RUNNING,
            **SURPLUS,
        )
        assert run(planner, minutes(10), self.RUNNING) is None

    def test_lowered_when_the_compressor_stops(self):
        planner = self._planner()
        run(planner, minutes(10), self.RUNNING)
        stopped = obs(
            mode="heat_pump",
            setpoint_raw=127,
            compressor_on=False,
            surplus_on=True,
        )
        write = run(planner, minutes(30), stopped)
        assert write.reason == WRITE_GRANT_LOWER
        assert {(e.kind, e.setpoint_raw) for e in write.added} == {
            (KIND_GRANT_LOWER, 120)
        }
        assert KIND_GUARD in {e.kind for e in write.removed}
        assert planner.raise_state is None

    def test_an_unfired_raise_is_withdrawn_instead(self):
        planner = self._planner()
        run(planner, minutes(10), self.RUNNING)
        stopped = obs(
            mode="heat_pump",
            setpoint_raw=120,
            compressor_on=False,
            surplus_on=True,
        )
        write = run(planner, minutes(11), stopped)
        assert write.added == ()
        assert {e.kind for e in write.removed} == {KIND_GRANT_RAISE, KIND_GUARD}

    def test_lowered_after_min_run_and_surplus_gone(self):
        planner = self._planner(control_min_run_before_lower_min=30)
        run(planner, minutes(10), self.RUNNING)
        gone = obs(
            mode="heat_pump",
            setpoint_raw=127,
            compressor_on=True,
            surplus_on=False,
        )
        assert run(planner, minutes(20), gone) is None
        assert run(planner, minutes(34), gone) is None
        write = run(planner, minutes(35), gone)
        assert write.reason == WRITE_GRANT_LOWER

    def test_one_raise_per_cycle(self):
        planner = self._planner(control_min_run_before_lower_min=0)
        run(planner, minutes(10), self.RUNNING)
        gone = obs(
            mode="heat_pump",
            setpoint_raw=127,
            compressor_on=True,
            surplus_on=False,
        )
        run(planner, minutes(20), gone)
        run(planner, minutes(35), gone)
        assert planner.raise_state is None
        run(planner, minutes(36), self.RUNNING)
        assert run(planner, minutes(50), self.RUNNING) is None
        assert planner.raise_state is None

    def test_the_guard_ends_it_on_the_device(self):
        planner = self._planner()
        run(planner, minutes(10), self.RUNNING)
        run(planner, minutes(181), self.RUNNING)
        assert planner.raise_state is None
        assert {g.id: g.status for g in planner.ack("i").grants} == {
            "g": "ended"
        }

    def test_a_new_plan_without_the_grant_lowers(self):
        planner = self._planner()
        run(planner, minutes(13), self.RUNNING)
        run(planner, minutes(23), self.RUNNING)
        assert planner.raise_state is not None
        write = give(
            planner,
            [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)],
            now=minutes(24),
            observed=self.RUNNING,
            intent_id="i-2",
        )
        assert planner.raise_state is None
        assert KIND_GRANT_LOWER in {e.kind for e in write.added}


class TestDisable:
    def test_removes_everything_and_restores_the_owner(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="heat_pump", setpoint_f=140)]
        )
        write = planner.disable(minutes(1))
        assert write.reason == WRITE_DISABLE
        assert [e.serves for e in write.removed] == ["a"]
        assert write.owner_state == ("energy_saver", OWNER_SETPOINT)
        assert planner.owned == []
        assert planner.plan is None

    def test_the_owner_state_follows_their_last_entry(self):
        owner = OwnerProgram(
            "energy_saver",
            OWNER_SETPOINT,
            True,
            (
                {
                    "enable": 2,
                    "week": MONDAY,
                    "hour": 6,
                    "min": 0,
                    "mode": 1,
                    "param": 110,
                },
            ),
        )
        assert owner.state_now(NOW, TZ) == ("heat_pump", 110)
        switched_off = OwnerProgram(
            "energy_saver", OWNER_SETPOINT, False, owner.entries
        )
        assert switched_off.state_now(NOW, TZ) == (
            "energy_saver",
            OWNER_SETPOINT,
        )


class TestProgram:
    def test_owner_entries_are_switched_off_in_the_program(self):
        owner_entry = {
            "enable": 2,
            "week": 2,
            "hour": 7,
            "min": 0,
            "mode": 3,
            "param": 110,
        }
        observed = obs(reservations=(owner_entry,))
        owner = OwnerProgram(
            "energy_saver", OWNER_SETPOINT, False, (owner_entry,)
        )
        planner = planner_with(
            [segment(NOW, "a", 60, mode="heat_pump", setpoint_f=140)],
            observed=observed,
            owner=owner,
        )
        program = planner.program(observed)
        assert program["reservation_use"] == 2
        assert program["reservation"][0] == {**owner_entry, "enable": 1}
        assert program["reservation"][1]["hour"] == 11
        assert planner.program_hash(observed) == schedule_hash(program)


class TestAck:
    def test_statuses(self):
        planner = planner_with(
            [
                segment(NOW, "past", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "now", -5, setpoint_f=130),
                segment(NOW, "same", 30, setpoint_f=130),
                segment(NOW, "future", 60, setpoint_f=140),
            ]
        )
        ack = planner.ack("i-1")
        assert ack.state == "shadow"
        items = {a.id: a for a in ack.segments}
        assert items["past"].status == "ended"
        assert items["now"].status == "shadow"
        assert items["now"].detail["in_force"] is True
        # Put in force by a near-term entry, which the ack names.
        assert items["now"].detail["fires_at"] == minutes(2).isoformat()
        assert items["same"].status == "merged"
        assert items["future"].status == "shadow"
        assert items["future"].detail["fires_at"] == minutes(60).isoformat()

    def test_live_statuses(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="heat_pump", setpoint_f=140)],
            shadow=False,
        )
        assert planner.ack("i").state == "programmed"
        assert statuses(planner)["a"] == ("programmed", None)

    def test_live_grants_not_switched_on(self):
        planner = planner_with(
            [segment(NOW, "a", -5, mode="heat_pump", setpoint_f=140)],
            grants=[grant(NOW, "g", 0, 60, max_f=146)],
            shadow=False,
            **SURPLUS,
        )
        (item,) = planner.ack("i").grants
        assert (item.status, item.reason) == ("shadow", "not_live")

    def test_no_plan(self):
        assert planner_with().ack(None).state == "none"


class TestPersistence:
    def test_round_trip(self):
        planner = planner_with(
            [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)],
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            observed=TestSurplusGrants.RUNNING,
            **SURPLUS,
        )
        run(planner, minutes(10), TestSurplusGrants.RUNNING)
        run(planner, minutes(11), obs(setpoint_raw=100))
        document = planner.as_document()

        again = Planner(planner.capabilities, TZ)
        again.load_document(document)

        assert again.owned == planner.owned
        assert again.extra == planner.extra
        assert again.asserted == planner.asserted
        assert again.reports == planner.reports
        assert again.raise_state == planner.raise_state
        assert again.last_write == planner.last_write
        assert again.as_document() == document

    def test_tolerates_an_empty_document(self):
        planner = Planner(capabilities(), TZ)
        planner.load_document({})
        assert planner.owned == []


def test_near_term_minute():
    lead = timedelta(minutes=2)
    assert near_term_minute(NOW, lead) == minutes(2)
    assert near_term_minute(NOW + timedelta(seconds=12), lead) == minutes(3)


@pytest.mark.parametrize("hours", [0, 143])
def test_next_event_includes_the_horizon_edge(hours):
    planner = planner_with(
        [
            segment(NOW, "a", 60, mode="heat_pump", setpoint_f=140),
            segment(NOW, "far", (144 + hours) * 60 + 30, setpoint_f=130),
        ]
    )
    edge = minutes((144 + hours) * 60 + 30) - timedelta(hours=144)
    assert planner.next_event_at == min(minutes(60), edge)


class TestReviewFindings:
    """Cases from the review of the draft pull request (#162)."""

    def test_a_moved_segment_does_not_keep_its_old_minute(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="heat_pump", setpoint_f=140)]
        )
        assert kinds(planner) == [(KIND_PLAN, "a", "11:00")]

        write = give(
            planner,
            [segment(NOW, "a", 120, mode="heat_pump", setpoint_f=140)],
            now=minutes(1),
            intent_id="i-2",
        )

        assert write is not None
        assert kinds(planner) == [(KIND_PLAN, "a", "12:00")]
        assert [e.fires_at.strftime("%H:%M") for e in write.removed] == [
            "11:00"
        ]

    def test_a_near_term_entry_is_not_shifted_past_the_next_segment(self):
        # Another entry holds 10:02, where the near-term entry for the
        # segment in force would go. Moving it to 10:03 collides with the
        # next segment's entry, and 10:04 would undo that segment.
        other = {
            "enable": 2,
            "week": MONDAY,
            "hour": 10,
            "min": 2,
            "mode": 3,
            "param": 110,
        }
        # An enabled entry that is not the owner's: only enabled entries
        # hold a slot (section 5.2.6).
        observed = obs(reservations=(other,))
        planner = planner_with(
            [
                segment(NOW, "now", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "next", 3, setpoint_f=130),
            ],
            observed=observed,
        )
        assert kinds(planner) == [(KIND_PLAN, "next", "10:03")]

    def test_no_raise_in_the_last_two_minutes_of_a_grant(self):
        running = obs(
            mode="heat_pump",
            setpoint_raw=120,
            compressor_on=True,
            surplus_on=True,
        )
        planner = planner_with(
            [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)],
            grants=[grant(NOW, "g", -5, 11, max_f=146)],
            observed=running,
            **SURPLUS,
        )
        # Surplus has lasted ten minutes, but the raise could only fire at
        # 10:12, after the grant ends at 10:11.
        assert run(planner, minutes(10), running) is None
        assert planner.raise_state is None


class TestPowerOff:
    """Section 5.9: the feature's own entries are off while powered off.

    Entries fire while the heater is powered off and power it back on, so
    they are switched off by their own flag until power returns.
    """

    SEGMENTS = [
        segment(NOW, "now", -5, mode="energy_saver", setpoint_f=139.1),
        segment(NOW, "a", 60, setpoint_f=140),
        segment(NOW, "b", 120, setpoint_f=130),
    ]

    def test_entries_are_switched_off_once(self):
        planner = planner_with(self.SEGMENTS)
        assert [e.enabled for e in planner.owned] == [True, True]

        write = run(planner, minutes(5), obs(mode="power_off"))

        assert write is not None
        assert write.reason == WRITE_POWER_OFF
        assert [e.enabled for e in planner.owned] == [False, False]
        assert [e.as_entry()["enable"] for e in planner.owned] == [1, 1]
        # The same entries, only switched off.
        assert [(e.serves, e.fires_at) for e in planner.owned] == [
            ("a", minutes(60)),
            ("b", minutes(120)),
        ]
        # Staying off writes nothing more.
        assert run(planner, minutes(6), obs(mode="power_off")) is None

    def test_power_returning_switches_them_on_and_re_asserts(self):
        planner = planner_with(self.SEGMENTS)
        run(planner, minutes(5), obs(mode="power_off"))

        write = run(planner, minutes(10))

        assert write is not None
        assert write.reason == WRITE_PRECEDENCE_EXIT
        assert all(e.enabled for e in planner.owned)
        assert KIND_PRECEDENCE_EXIT in {e.kind for e in planner.owned}

    def test_nothing_to_switch_off(self):
        planner = planner_with()
        assert run(planner, minutes(5), obs(mode="power_off")) is None

    def test_vacation_writes_nothing(self):
        planner = planner_with(self.SEGMENTS)
        assert run(planner, minutes(5), obs(mode="vacation")) is None
        assert all(e.enabled for e in planner.owned)

    def test_the_program_shows_them_switched_off(self):
        planner = planner_with(self.SEGMENTS)
        run(planner, minutes(5), obs(mode="power_off"))
        program = planner.program(obs(mode="power_off"))
        assert [e["enable"] for e in program["reservation"]] == [1, 1]

    def test_switched_off_entries_survive_a_restart(self):
        planner = planner_with(self.SEGMENTS)
        run(planner, minutes(5), obs(mode="power_off"))
        again = Planner(planner.capabilities, TZ)
        again.load_document(planner.as_document())
        assert again.owned == planner.owned
