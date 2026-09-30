"""The planner (spec section 5, issue #158), in shadow.

Every scenario drives the planner with times and `Observed` snapshots only,
as the device controller does, so the rules are tested without Home
Assistant, timers or a device.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator

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
    Report,
    State,
)
from custom_components.nwp500.control.entries import (
    KIND_GRANT_LOWER,
    KIND_GRANT_RAISE,
    KIND_GUARD,
    KIND_NEAR_TERM,
    KIND_PLAN,
    KIND_PRECEDENCE_EXIT,
    OwnedEntry,
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
from custom_components.nwp500.control.sensor import ControlLastWriteSensor

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

EXAMPLES = Path("docs/examples")
SCHEMA = Path("docs/external-control-protocol-1.schema.json")
EXAMPLE_WRITES = ("last-write-plan", "last-write-grant-raise")


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

    @pytest.mark.parametrize("name", EXAMPLE_WRITES)
    def test_the_last_write_examples(self, name):
        """docs/examples/last-write-*.json are what the entity reports.

        Each is built by the planner from docs/examples/plan-day.json, and
        fits the schema's `last_write_attributes`.
        """
        example = json.loads((EXAMPLES / f"{name}.json").read_text())
        state, attributes = last_write_examples()[name]
        assert example["state"] == state
        assert example["attributes"] == attributes
        schema = json.loads(SCHEMA.read_text())
        Draft202012Validator(
            {"$defs": schema["$defs"], "$ref": "#/$defs/last_write_attributes"}
        ).validate(example["attributes"])


def last_write_examples() -> dict[str, tuple[str, dict]]:
    """The last write entity's state and attributes for plan-day.json.

    The plan arrives at 05:00:12, after its first segment began; surplus
    appears at 11:20 with the compressor running. Each write is confirmed,
    and the device then holds the list written. The state is in UTC, as
    Home Assistant shows a timestamp sensor's.
    """
    plan = parse_plan(json.loads((EXAMPLES / "plan-day.json").read_text()))
    at = plan.issued_at
    planner = Planner(
        capabilities(
            **SURPLUS,
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
        ),
        TZ,
        shadow=False,
    )
    planner.owner = OWNER
    check_plan(plan, planner.capabilities)
    planner.set_plan(plan, at, obs())

    def on_device(**overrides) -> Observed:
        return obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
            **overrides,
        )

    def confirmed(when: datetime, observed: Observed) -> tuple[str, dict]:
        write = planner.step(when, observed)
        assert write is not None
        write = replace(write, confirmed=True)
        planner.commit(write)
        control = MagicMock()
        control.last_write = write
        sensor = ControlLastWriteSensor(control, "last_write")
        return write.at.astimezone(UTC).isoformat(), (
            sensor.extra_state_attributes
        )

    first = confirmed(at, obs())
    base = at.replace(second=0)
    running = {
        "mode": "heat_pump",
        "setpoint_raw": 120,
        "compressor_on": True,
        "surplus_on": True,
    }
    assert planner.step(minutes(380, base), on_device(**running)) is None
    raise_ = confirmed(minutes(390, base), on_device(**running))
    return {"last-write-plan": first, "last-write-grant-raise": raise_}


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

    def test_a_plan_in_vacation_is_written_and_re_asserted_after(self):
        """Section 5.9: a plan accepted in Vacation is written at once.

        The heater skips entries in Vacation, and so holds the newest plan
        even if Home Assistant is unavailable when Vacation ends.
        """
        planner = planner_with(
            [segment(NOW, "a", -5, mode="energy_saver", setpoint_f=139.1)]
        )
        assert planner.owned == []
        assert run(planner, minutes(1), obs(mode="vacation")) is None
        write = give(
            planner,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_f=139.1),
                segment(NOW, "b", 60, setpoint_f=130),
            ],
            now=minutes(2),
            observed=obs(mode="vacation"),
            intent_id="i-2",
        )
        assert write is not None
        assert [e.kind for e in write.added] == [KIND_PLAN]
        write = run(planner, minutes(3))
        assert write is not None
        assert write.reason == WRITE_PRECEDENCE_EXIT
        assert {e.kind for e in write.added} == {KIND_PRECEDENCE_EXIT}
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
        assert f"{REPORT_REMOVED}:{KIND_PLAN}:a" in planner.reports
        # Reported as the last write entity reported it (section 4.2).
        assert planner.reports[f"{REPORT_REMOVED}:{KIND_PLAN}:a"].value == (
            entry.as_attributes()
        )
        assert statuses(planner)["a"][0] == "removed"
        assert planner.ack("i").state == "partly_programmed"

    SEGMENTS = [
        segment(NOW, "a", 60, mode="energy_saver", setpoint_f=140),
        segment(NOW, "b", 120, setpoint_f=130),
    ]

    def _remove_a(self) -> tuple[Planner, Observed]:
        """Live, with a person deleting segment a's entry from the heater."""
        planner = planner_with(self.SEGMENTS, shadow=False)
        a, b = sorted(planner.owned, key=lambda e: e.fires_at)
        run(
            planner,
            minutes(1),
            obs(
                reservations_enabled=True,
                reservations=(a.as_entry(), b.as_entry()),
            ),
        )
        only_b = obs(reservations_enabled=True, reservations=(b.as_entry(),))
        run(planner, minutes(2), only_b)
        assert f"{REPORT_REMOVED}:{KIND_PLAN}:a" in planner.reports
        return planner, only_b

    def test_a_removal_lasts_until_the_next_segment_starts(self):
        """Section 4.2: by the plan's clock, not by what fires."""
        planner, only_b = self._remove_a()
        # An entry of someone else's fires at 10:30, before a's minute: the
        # time a would have set is still to come.
        other = {"enable": 2, "week": MONDAY, "hour": 10, "min": 30}
        other |= {"mode": 1, "param": 100}
        with_other = obs(
            reservations_enabled=True,
            reservations=(*only_b.reservations, other),
        )
        run(planner, minutes(45), with_other)
        assert f"{REPORT_REMOVED}:{KIND_PLAN}:a" in planner.reports
        # a's minute (11:00) passes; the entry before it holds over.
        run(planner, minutes(90), with_other)
        assert f"{REPORT_REMOVED}:{KIND_PLAN}:a" in planner.reports
        # b starts at 12:00: a's time is over.
        run(planner, minutes(121), with_other)
        assert f"{REPORT_REMOVED}:{KIND_PLAN}:a" not in planner.reports

    def test_the_segment_before_a_removed_one_holds_over_its_time(self):
        """Section 5.10: a removed segment never takes effect."""
        planner = planner_with(self.SEGMENTS, shadow=False)
        a, b = sorted(planner.owned, key=lambda e: e.fires_at)
        both = obs(
            reservations_enabled=True,
            reservations=(a.as_entry(), b.as_entry()),
        )
        run(planner, minutes(1), both)
        only_a = obs(reservations_enabled=True, reservations=(a.as_entry(),))
        run(planner, minutes(2), only_a)
        after_b = obs(
            mode="energy_saver",
            setpoint_raw=a.setpoint_raw,
            reservations_enabled=True,
            reservations=(a.as_entry(),),
        )
        run(planner, minutes(121), after_b)
        ack = {s.id: s for s in planner.ack("i").segments}
        assert ack["a"].status == "in_force"
        assert ack["a"].detail["in_force"] is True
        assert ack["b"].status == "removed"
        assert ack["b"].detail["in_force"] is False
        assert planner.wanted_state(minutes(121)) == State(
            "energy_saver", a.setpoint_raw
        )
        # Nothing waits to be written: b's entry is not coming back.
        assert planner.programmed_complete is True

    def test_a_deleted_near_term_entry_is_not_written_again(self):
        """Nothing else is written for it either (section 5.10).

        Not the segment before it, which a plan beginning in the past has.
        """
        planner = planner_with(
            [
                segment(NOW, "z", -60, mode="electric", setpoint_f=120),
                segment(NOW, "a", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 120, setpoint_f=130),
            ],
            shadow=False,
        )
        near = next(e for e in planner.owned if e.kind == KIND_NEAR_TERM)
        later = [e for e in planner.owned if e.kind != KIND_NEAR_TERM]
        everything = obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
        )
        run(planner, minutes(1), everything)
        without = obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in later),
        )
        assert run(planner, minutes(2), without) is None
        assert run(planner, minutes(10), without) is None
        assert near not in planner.owned
        assert f"{REPORT_REMOVED}:{KIND_NEAR_TERM}:a" in planner.reports

    def test_deleting_the_entry_of_a_merged_run_removes_the_run(self):
        """A run of equal segments has one entry on the heater.

        Deleting it is not undone by writing the same state at the next
        segment of the run.
        """
        run_of = [
            segment(NOW, "a1", 60, mode="heat_pump", setpoint_f=140),
            segment(NOW, "a2", 120, setpoint_f=140),
            segment(NOW, "a3", 180, setpoint_f=140),
            segment(NOW, "y", 240, setpoint_f=120),
        ]
        planner = planner_with(run_of, shadow=False)
        assert {e.serves for e in planner.owned} == {"a1", "y"}
        run(
            planner,
            minutes(1),
            obs(
                reservations_enabled=True,
                reservations=tuple(e.as_entry() for e in planner.owned),
            ),
        )
        only_y = obs(
            reservations_enabled=True,
            reservations=tuple(
                e.as_entry() for e in planner.owned if e.serves == "y"
            ),
        )
        assert run(planner, minutes(2), only_y) is None
        assert run(planner, minutes(61), only_y) is None
        now_status = {s.id: s.status for s in planner.ack("i").segments}
        assert now_status["a1"] == "removed"
        assert now_status["a2"] == now_status["a3"] == "merged"

    def test_a_removal_ends_though_nothing_is_read(self):
        """Neither the heater's status nor its list is needed."""
        planner, _ = self._remove_a()
        unread = obs(
            mode=None,
            setpoint_raw=None,
            reservations_enabled=None,
            reservations=None,
        )
        run(planner, minutes(121), unread)
        assert planner.reports == {}

    def test_the_last_segments_removal_lasts_until_a_plan_changes_it(self):
        planner = planner_with(self.SEGMENTS, shadow=False)
        a, b = sorted(planner.owned, key=lambda e: e.fires_at)
        both = obs(
            reservations_enabled=True,
            reservations=(a.as_entry(), b.as_entry()),
        )
        run(planner, minutes(1), both)
        only_a = obs(reservations_enabled=True, reservations=(a.as_entry(),))
        run(planner, minutes(2), only_a)
        key = f"{REPORT_REMOVED}:{KIND_PLAN}:b"
        # The last segment holds until a new plan: so does its removal.
        run(planner, minutes(3 * 24 * 60), only_a)
        assert key in planner.reports
        give(planner, self.SEGMENTS[:1], now=minutes(3 * 24 * 60 + 1))
        assert key not in planner.reports

    def test_a_grant_entrys_removal_lasts_until_the_grant_ends(self):
        planner = planner_with(
            self.SEGMENTS,
            grants=[grant(NOW, "g1", 70, 110, max_f=146)],
            **SURPLUS,
        )
        guard = OwnedEntry(KIND_GUARD, "g1", minutes(110), "energy_saver", 120)
        report = Report(REPORT_REMOVED, guard.as_attributes(), minutes(1), "g1")
        planner.reports[Planner._report_key(report)] = report
        run(planner, minutes(100))
        assert planner.reports
        run(planner, minutes(111))
        assert planner.reports == {}

    def test_a_change_ends_only_by_an_entry_fired_while_switched_on(self):
        """An entry whose minute passes with the switch off did not fire."""
        daily = {"enable": 2, "week": 254, "hour": 10, "min": 30}
        daily |= {"mode": 3, "param": 119}
        on = {"reservations_enabled": True, "reservations": (daily,)}
        planner = planner_with(observed=obs(**on))
        run(planner, minutes(1), obs(setpoint_raw=100, **on))
        assert REPORT_SETPOINT in planner.reports
        off = {"reservations_enabled": False, "reservations": (daily,)}
        run(planner, minutes(10), obs(setpoint_raw=100, **off))
        run(planner, minutes(40), obs(setpoint_raw=100, **off))
        # On again after 10:30: that minute passed while it was off.
        run(planner, minutes(41), obs(setpoint_raw=100, **on))
        assert REPORT_SETPOINT in planner.reports
        # The next day's 10:30 fires.
        run(planner, minutes(24 * 60 + 31), obs(setpoint_raw=100, **on))
        assert REPORT_SETPOINT not in planner.reports

    def test_two_added_entries_in_one_slot_are_both_reported(self):
        """The heater can hold two in one slot (section 5.2)."""
        planner = planner_with()
        slot = {"week": 2, "hour": 7, "min": 0, "mode": 1}
        first = {"enable": 2, "param": 100, **slot}
        second = {"enable": 1, "param": 110, **slot}
        both = obs(reservations=(first, second))
        run(planner, minutes(1), both)
        run(planner, minutes(2), both)
        reports = sorted(
            planner.reports.values(), key=lambda r: r.value["param"]
        )
        assert [r.value for r in reports] == [first, second]
        # Stable: the same reports, not new ones every pass.
        assert {r.detected_at for r in reports} == {minutes(1)}

    def test_handing_back_is_not_a_persons_change(self):
        """What the hand-back restores is the feature's doing (6.6)."""
        live = obs(
            mode="heat_pump",
            setpoint_raw=120,
            reservations_enabled=True,
            reservations=(),
        )
        planner = planner_with(observed=live)
        run(planner, minutes(1), live)
        planner.disable(minutes(2))
        owner = obs(reservations_enabled=False, reservations=())
        run(planner, minutes(3), owner)
        assert planner.reports == {}

    def test_stored_reports_load_in_the_current_shape(self):
        """Reports stored by earlier versions load as current ones.

        Before #168 a removal held the six stored keys; before #169 an
        added entry was keyed by its slot.
        """
        entry = OwnedEntry(KIND_PLAN, "a", minutes(60), "energy_saver", 120)
        added = {"enable": 2, "week": 2, "hour": 7, "min": 0}
        added |= {"mode": 1, "param": 100}
        planner = planner_with()
        planner.load_document(
            {
                "reports": [
                    Report(
                        REPORT_REMOVED, entry.as_document(), NOW, "a"
                    ).as_document(),
                    Report(
                        REPORT_FOREIGN, added, NOW, "(2, 7, 0)"
                    ).as_document(),
                ]
            }
        )
        removed = planner.reports[f"{REPORT_REMOVED}:{KIND_PLAN}:a"]
        assert removed.value == entry.as_attributes()
        (foreign,) = (
            r for r in planner.reports.values() if r.field == REPORT_FOREIGN
        )
        assert foreign.value == added

    def test_a_new_plan_is_the_answer_to_a_removal(self):
        """Section 5.6: a new plan is programmed as it stands.

        The scheduler saw the removal; publishing a plan with that segment
        again writes its entry again, and the report ends.
        """
        planner, only_b = self._remove_a()
        write = give(
            planner,
            self.SEGMENTS,
            now=minutes(3),
            observed=only_b,
            intent_id="i-2",
        )
        assert write is not None
        assert (KIND_PLAN, "a") in {(e.kind, e.serves) for e in write.added}
        assert f"{REPORT_REMOVED}:{KIND_PLAN}:a" not in planner.reports
        assert planner.removed_segments == set()

    def test_removals_of_two_kinds_for_one_item_are_both_reported(self):
        """A grant's raise and its guard: one report each."""
        entry = OwnedEntry(KIND_GRANT_RAISE, "g1", minutes(5), "heat_pump", 127)
        guard = OwnedEntry(KIND_GUARD, "g1", minutes(60), "heat_pump", 120)
        keys = {
            Planner._report_key(
                Report(REPORT_REMOVED, e.as_attributes(), NOW, "g1")
            )
            for e in (entry, guard)
        }
        assert len(keys) == 2

    def test_a_changed_foreign_entry_is_reported_again(self):
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
        changed = {**added, "param": 110}
        run(planner, minutes(2), obs(reservations=(changed,)))
        (report,) = planner.reports.values()
        assert report.value == changed
        assert report.detected_at == minutes(2)

    def test_disabling_ends_every_report(self):
        planner = planner_with()
        run(planner, minutes(1), obs(mode="energy_saver", setpoint_raw=100))
        assert planner.reports
        planner.removed_segments = {"a"}
        planner.disable(minutes(2))
        assert planner.reports == {}
        # The entries people removed were the feature's, now gone too.
        assert planner.removed_segments == set()


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

    def test_a_segment_entry_ends_the_raise_and_withdraws_the_guard(self):
        planner = self._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "next", 60, setpoint_f=135),
            ]
        )
        write = run(planner, minutes(10), self.RUNNING)
        # A guard even so: the segment's entry may not stay on the heater.
        assert {e.kind for e in write.added} == {KIND_GRANT_RAISE, KIND_GUARD}
        run(planner, minutes(61), self.RUNNING)
        assert planner.raise_state is None
        assert not [e for e in planner.owned if e.kind == KIND_GUARD]

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


class TestEffectiveTimeline:
    """A person deleting any of the feature's entries (#171, section 5.10).

    The segment the entry would have put in force never takes effect: the
    state before it holds, and nothing is written for that state.
    """

    def _on_heater(self, planner: Planner, **overrides) -> Observed:
        return obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
            **overrides,
        )

    def _without(self, planner: Planner, drop, **overrides) -> Observed:
        """The heater's list, less the owned entries `drop` selects."""
        return obs(
            reservations_enabled=True,
            reservations=tuple(
                e.as_entry() for e in planner.owned if not drop(e)
            ),
            **overrides,
        )

    def test_a_segment_whose_near_term_entry_was_deleted_is_not_in_force(
        self,
    ):
        planner = planner_with(
            [
                segment(NOW, "a", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 120, setpoint_f=130),
            ],
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner))
        without = obs(
            reservations_enabled=True,
            reservations=tuple(
                e.as_entry() for e in planner.owned if e.kind != KIND_NEAR_TERM
            ),
        )
        assert run(planner, minutes(2), without) is None
        assert run(planner, minutes(10), without) is None
        (a,) = (s for s in planner.ack("i").segments if s.id == "a")
        assert a.detail["in_force"] is False
        assert a.status == "removed"
        # The heater keeps the state it had when the plan arrived.
        assert planner.wanted_state(minutes(10)) == State(
            "energy_saver", OWNER_SETPOINT
        )
        assert planner.segment_in_force(minutes(10)) is None
        # b is still programmed, and takes effect at its minute.
        b = next(e for e in planner.owned if e.serves == "b")
        at_b = obs(
            mode=b.mode,
            setpoint_raw=b.setpoint_raw,
            reservations_enabled=True,
            reservations=(b.as_entry(),),
        )
        run(planner, minutes(121), at_b)
        assert planner.segment_in_force(minutes(121)) == "b"
        assert statuses(planner)["a"][0] == "removed"

    def test_a_deleted_exit_leaves_a_segment_begun_in_vacation_out(self):
        """Segment b begins during Vacation; its exit entry is deleted.

        The heater comes back in a's state, so b is not in force.
        """
        planner = planner_with(
            [
                segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            shadow=False,
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        run(planner, minutes(40), self._on_heater(planner, mode="vacation"))
        write = run(planner, minutes(41), self._on_heater(planner, **on_a))
        assert write is not None
        assert {e.kind for e in write.added} == {KIND_PRECEDENCE_EXIT}
        without_exit = obs(
            reservations_enabled=True,
            reservations=tuple(
                e.as_entry()
                for e in planner.owned
                if e.kind != KIND_PRECEDENCE_EXIT
            ),
            **on_a,
        )
        assert run(planner, minutes(42), without_exit) is None
        assert run(planner, minutes(50), without_exit) is None
        ack = {s.id: s for s in planner.ack("i").segments}
        assert ack["b"].detail["in_force"] is False
        assert ack["b"].status == "removed"
        # The state the heater came back in holds.
        assert ack["a"].detail["in_force"] is True
        assert planner.wanted_state(minutes(50)) == State("heat_pump", 120)
        # Its mode is read from the heater's behaviour, not against b's
        # entry, which fired during Vacation and was skipped.
        running = self._without(
            planner,
            lambda e: e.kind == KIND_PRECEDENCE_EXIT,
            elements_on=False,
            **on_a,
        )
        run(planner, minutes(51), running)
        (a,) = (s for s in planner.ack("i").segments if s.id == "a")
        assert a.detail["mode_confirmed"] is True

    def test_a_removed_segment_does_not_stop_a_raise_before_it(self):
        """Segment b's entry is gone: it cannot undo a raise at its minute."""
        running = {
            "mode": "heat_pump",
            "setpoint_raw": 120,
            "compressor_on": True,
            "surplus_on": True,
        }
        planner = planner_with(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 12, setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            observed=obs(**running),
            shadow=False,
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
            **SURPLUS,
        )
        run(planner, minutes(1), self._on_heater(planner, **running))
        without_b = obs(
            reservations_enabled=True,
            reservations=tuple(
                e.as_entry() for e in planner.owned if e.serves != "b"
            ),
            **running,
        )
        run(planner, minutes(2), without_b)
        assert "b" in planner.removed_segments
        write = run(planner, minutes(10), without_b)
        assert write is not None
        assert KIND_GRANT_RAISE in {e.kind for e in write.added}

    # The rest pin one rule across every kind of deletion: the state before
    # holds, and nothing is written for it.

    def test_a_plan_begun_in_the_past_does_not_fall_back_to_its_past(self):
        """Segment z ended before the plan arrived: it never took effect.

        With a's near-term entry deleted, the state the heater had holds,
        and z's is not written.
        """
        planner = planner_with(
            [
                segment(NOW, "z", -60, mode="electric", setpoint_f=120),
                segment(NOW, "a", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 120, setpoint_f=130),
            ],
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner))
        without = self._without(planner, lambda e: e.kind == KIND_NEAR_TERM)
        for m in (2, 10, 60, 119):
            assert run(planner, minutes(m), without) is None
        ack = {s.id: s for s in planner.ack("i").segments}
        assert ack["z"].status == "ended"
        assert ack["z"].detail["in_force"] is False
        assert ack["a"].status == "removed"
        assert not any(s.detail["in_force"] for s in ack.values())
        assert planner.wanted_state(minutes(119)) == State(
            "energy_saver", OWNER_SETPOINT
        )

    def test_a_deleted_exit_for_a_segment_already_in_force_changes_nothing(
        self,
    ):
        """Vacation began and ended within a: the heater came back in a."""
        planner = planner_with(
            [
                segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 120, mode="energy_saver", setpoint_f=130),
            ],
            shadow=False,
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        # The pass one minute after a's near-term entry's minute, which the
        # planner schedules: the entry is found to have fired.
        run(planner, minutes(3), self._on_heater(planner, **on_a))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        write = run(planner, minutes(41), self._on_heater(planner, **on_a))
        assert write is not None
        assert {e.kind for e in write.added} == {KIND_PRECEDENCE_EXIT}
        without = self._without(
            planner, lambda e: e.kind == KIND_PRECEDENCE_EXIT, **on_a
        )
        assert run(planner, minutes(42), without) is None
        assert run(planner, minutes(50), without) is None
        ack = {s.id: s for s in planner.ack("i").segments}
        assert ack["a"].status == "in_force"
        assert ack["a"].detail["in_force"] is True
        assert "a" not in planner.removed_segments
        # Reported all the same: a person deleted it.
        assert f"{REPORT_REMOVED}:{KIND_PRECEDENCE_EXIT}:a" in planner.reports

    @pytest.mark.parametrize("began_during", [True, False])
    def test_a_deleted_power_off_exit(self, began_during):
        """As with Vacation: only a segment begun while off is left out."""
        planner = planner_with(
            [
                segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            shadow=False,
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        # The pass one minute after a's near-term entry's minute, which the
        # planner schedules: the entry is found to have fired.
        run(planner, minutes(3), self._on_heater(planner, **on_a))
        back = 41 if began_during else 20
        run(planner, minutes(10), self._on_heater(planner, mode="power_off"))
        run(planner, minutes(11), self._on_heater(planner, mode="power_off"))
        write = run(planner, minutes(back), self._on_heater(planner, **on_a))
        assert write is not None
        assert KIND_PRECEDENCE_EXIT in {e.kind for e in write.added}
        (exit_entry,) = (
            e for e in planner.owned if e.kind == KIND_PRECEDENCE_EXIT
        )
        served = "b" if began_during else "a"
        assert exit_entry.serves == served
        without = self._without(
            planner, lambda e: e.kind == KIND_PRECEDENCE_EXIT, **on_a
        )
        assert run(planner, minutes(back + 1), without) is None
        assert run(planner, minutes(back + 5), without) is None
        in_force = planner.segment_in_force(minutes(back + 5))
        assert in_force == "a"
        assert (served in planner.removed_segments) is began_during

    def test_deleting_a_merged_runs_entry_writes_nothing_through_the_run(
        self,
    ):
        """With nothing before the run, the state before the plan holds."""
        run_of = [
            segment(NOW, "a1", 60, mode="heat_pump", setpoint_f=140),
            segment(NOW, "a2", 120, setpoint_f=140),
            segment(NOW, "a3", 180, setpoint_f=140),
            segment(NOW, "y", 240, setpoint_f=120),
        ]
        planner = planner_with(run_of, shadow=False)
        run(planner, minutes(1), self._on_heater(planner))
        only_y = self._without(planner, lambda e: e.serves != "y")
        for m in (2, 61, 121, 181):
            assert run(planner, minutes(m), only_y) is None
        assert planner.segment_in_force(minutes(181)) is None
        assert planner.wanted_state(minutes(181)) == State(
            "energy_saver", OWNER_SETPOINT
        )
        now_status = {s.id: s.status for s in planner.ack("i").segments}
        assert now_status == {
            "a1": "removed",
            "a2": "merged",
            "a3": "merged",
            "y": "programmed",
        }

    def test_a_removed_segment_between_two_equal_ones(self):
        """The later one keeps its entry, and takes effect at its minute."""
        planner = planner_with(
            [
                segment(NOW, "x", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "a", 60, mode="energy_saver", setpoint_f=130),
                segment(NOW, "b", 120, mode="heat_pump", setpoint_f=140),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            observed=obs(mode="heat_pump", setpoint_raw=120),
            shadow=False,
        )
        on_x = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(1), self._on_heater(planner, **on_x))
        without_a = self._without(planner, lambda e: e.serves == "a", **on_x)
        assert run(planner, minutes(2), without_a) is None
        assert {e.serves for e in planner.owned} == {"b", "c"}
        assert run(planner, minutes(61), without_a) is None
        assert planner.segment_in_force(minutes(61)) == "x"
        run(planner, minutes(121), without_a)
        assert planner.segment_in_force(minutes(121)) == "b"
        ack = {s.id: s.status for s in planner.ack("i").segments}
        assert ack == {
            "x": "ended",
            "a": "removed",
            "b": "in_force",
            "c": "programmed",
        }

    def _declined_a(self) -> tuple[Planner, Observed]:
        """Live, with a person deleting segment a's near-term entry."""
        planner = planner_with(
            [
                segment(NOW, "a", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 120, setpoint_f=130),
            ],
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner))
        without = self._without(planner, lambda e: e.kind == KIND_NEAR_TERM)
        assert run(planner, minutes(2), without) is None
        assert "a" in planner.removed_segments
        return planner, without

    @pytest.mark.parametrize("rename", [False, True])
    def test_a_new_plan_asserts_what_a_person_kept_out(self, rename):
        """A new plan is the scheduler's answer to the removal (5.6).

        Published again, as it was or renamed, it puts a in force.
        """
        planner, without = self._declined_a()
        first = "a2" if rename else "a"
        write = give(
            planner,
            [
                segment(NOW, first, -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 120, setpoint_f=130),
            ],
            now=minutes(5),
            observed=without,
            intent_id="i-2",
        )
        assert write is not None
        assert [(e.kind, e.serves) for e in write.added] == [
            (KIND_NEAR_TERM, first)
        ]
        assert planner.removed_segments == set()
        assert f"{REPORT_REMOVED}:{KIND_NEAR_TERM}:a" not in planner.reports

    def test_the_same_document_again_keeps_the_segment_out(self):
        """Received again (the source came back), it is not adopted again.

        That is the device controller's rule (section 5.4); restoring at
        start-up keeps the removal likewise (below).
        """
        planner, without = self._declined_a()
        assert run(planner, minutes(10), without) is None
        assert statuses(planner)["a"][0] == "removed"
        assert planner.segment_in_force(minutes(10)) is None
        assert planner.wanted_state(minutes(10)) == State(
            "energy_saver", OWNER_SETPOINT
        )

    def test_a_new_plan_wanting_another_state_now_is_asserted(self):
        planner, without = self._declined_a()
        write = give(
            planner,
            [
                segment(NOW, "a", -5, mode="electric", setpoint_f=130),
                segment(NOW, "b", 120, setpoint_f=130),
            ],
            now=minutes(5),
            observed=without,
            intent_id="i-2",
        )
        assert write is not None
        assert [(e.kind, e.serves) for e in write.added] == [
            (KIND_NEAR_TERM, "a")
        ]
        assert "a" not in planner.removed_segments

    @pytest.mark.parametrize("at", [10, 125])
    def test_a_restart_keeps_the_segment_declined(self, at):
        """During a, or after b began: nothing is written either way."""
        planner, without = self._declined_a()
        b = next(e for e in planner.owned if e.serves == "b")
        seen = without
        if at > 120:
            seen = obs(
                mode=b.mode,
                setpoint_raw=b.setpoint_raw,
                reservations_enabled=True,
                reservations=without.reservations,
            )
        restarted = Planner(planner.capabilities, TZ, shadow=False)
        restarted.owner = planner.owner
        restarted.load_document(planner.as_document())
        assert planner.plan is not None
        restarted.set_plan(planner.plan, minutes(at), seen, restoring=True)
        write = run(restarted, minutes(at), seen)
        # At most fired entries are cleaned up: nothing is added.
        assert write is None or not write.added
        assert statuses(restarted)["a"][0] == "removed"
        assert restarted.segment_in_force(minutes(at)) == (
            "b" if at > 120 else None
        )

    def test_the_state_before_a_plan_holds_after_a_decline(self):
        """A new plan keeps x and adds w, begun; w's near-term is deleted.

        The state x put in force holds. x is not in force in the new plan:
        it had ended by the new plan's clock when that plan was adopted.
        """
        on_x = {"mode": "heat_pump", "setpoint_raw": 120}
        planner = planner_with(
            [
                segment(NOW, "x", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            observed=obs(**on_x),
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner, **on_x))
        write = give(
            planner,
            [
                segment(NOW, "x", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "v", -30, mode="electric", setpoint_f=120),
                segment(NOW, "w", 0, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            now=minutes(5),
            observed=self._on_heater(planner, **on_x),
            intent_id="i-2",
        )
        assert write is not None
        assert (KIND_NEAR_TERM, "w") in {
            (e.kind, e.serves) for e in write.added
        }
        without = self._without(
            planner, lambda e: e.kind == KIND_NEAR_TERM, **on_x
        )
        assert run(planner, minutes(6), without) is None
        assert run(planner, minutes(30), without) is None
        ack = {s.id: s for s in planner.ack("i").segments}
        assert ack["x"].status == ack["v"].status == "ended"
        assert ack["w"].status == "removed"
        assert not any(s.detail["in_force"] for s in ack.values())
        assert planner.wanted_state(minutes(30)) == State("heat_pump", 120)

    @pytest.mark.parametrize("rename", [False, True])
    def test_a_new_plan_after_a_hold_over_asserts_its_segment(self, rename):
        """Segment b's entry was deleted and its minute passed; x holds over.

        A new plan with b, as it was or renamed, puts b in force.
        """
        on_x = {"mode": "heat_pump", "setpoint_raw": 120}
        segments = [
            segment(NOW, "x", -5, mode="heat_pump", setpoint_f=140),
            segment(NOW, "b", 60, mode="energy_saver", setpoint_f=130),
            segment(NOW, "c", 240, setpoint_f=120),
        ]
        planner = planner_with(segments, observed=obs(**on_x), shadow=False)
        run(planner, minutes(1), self._on_heater(planner, **on_x))
        without_b = self._without(planner, lambda e: e.serves == "b", **on_x)
        run(planner, minutes(2), without_b)
        assert run(planner, minutes(61), without_b) is None
        second = "b2" if rename else "b"
        renamed = [
            segments[0],
            segment(NOW, second, 60, mode="energy_saver", setpoint_f=130),
            segments[2],
        ]
        write = give(
            planner,
            renamed,
            now=minutes(70),
            observed=without_b,
            intent_id="i-2",
        )
        assert write is not None
        assert (KIND_NEAR_TERM, second) in {
            (e.kind, e.serves) for e in write.added
        }
        assert planner.removed_segments == set()

    def test_an_exit_just_before_a_removed_segment_is_written(self):
        """A removed segment puts nothing on the heater at its minute."""
        on_s = {"mode": "heat_pump", "setpoint_raw": 120}
        planner = planner_with(
            [
                segment(NOW, "s", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 42, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            observed=obs(**on_s),
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner, **on_s))
        without_b = self._without(planner, lambda e: e.serves == "b", **on_s)
        run(planner, minutes(2), without_b)
        vacation = self._without(
            planner, lambda e: e.serves == "b", mode="vacation"
        )
        run(planner, minutes(10), vacation)
        write = run(planner, minutes(40), without_b)
        assert write is not None
        assert [(e.kind, e.serves) for e in write.added] == [
            (KIND_PRECEDENCE_EXIT, "s")
        ]

    def _raised(self, segments) -> tuple[Planner, dict]:
        running = {
            "mode": "heat_pump",
            "setpoint_raw": 120,
            "compressor_on": True,
            "surplus_on": True,
        }
        planner = planner_with(
            segments,
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            observed=obs(**running),
            shadow=False,
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
            **SURPLUS,
        )
        run(planner, minutes(1), self._on_heater(planner, **running))
        run(planner, minutes(10), self._on_heater(planner, **running))
        assert planner.raise_state is not None
        return planner, running

    def test_the_guard_follows_a_deletion_inside_the_grant(self):
        """Segment b's entry is deleted: the guard restores s, not b."""
        planner, running = self._raised(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 60, setpoint_f=135),
                segment(NOW, "c", 240, setpoint_f=120),
            ]
        )
        guard = planner.raise_state.guard
        assert guard is not None and guard.setpoint_raw == 114
        run(planner, minutes(11), self._on_heater(planner, **running))
        without_b = self._without(planner, lambda e: e.serves == "b", **running)
        write = run(planner, minutes(20), without_b)
        assert write is not None
        (new_guard,) = (e for e in write.added if e.kind == KIND_GUARD)
        assert (new_guard.mode, new_guard.setpoint_raw) == ("heat_pump", 120)
        assert new_guard.fires_at == guard.fires_at

    def test_lowering_after_a_removed_segment_restores_the_state_before(self):
        planner, running = self._raised(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 60, setpoint_f=135),
                segment(NOW, "c", 240, setpoint_f=120),
            ]
        )
        run(planner, minutes(11), self._on_heater(planner, **running))
        without_b = self._without(planner, lambda e: e.serves == "b", **running)
        run(planner, minutes(20), without_b)
        stopped = {**running, "compressor_on": False, "setpoint_raw": 127}
        write = run(
            planner,
            minutes(70),
            self._without(planner, lambda e: e.serves == "b", **stopped),
        )
        assert write is not None
        (lower,) = (e for e in write.added if e.kind == KIND_GRANT_LOWER)
        assert (lower.mode, lower.setpoint_raw) == ("heat_pump", 120)

    # Found by the adversarial review of the effective timeline.

    def test_an_exit_deleted_while_raised_keeps_the_segment_in_force(self):
        """Power-off within a, with a raise: the heater is not in a's state.

        Whether the exit only re-asserted a comes from the timeline, not
        the heater's state: the guard and a lowering keep restoring a.
        """
        planner = planner_with(
            [
                segment(NOW, "x", -60, mode="heat_pump", setpoint_f=130),
                segment(NOW, "a", 3, mode="heat_pump", setpoint_f=140),
                segment(NOW, "c", 600, mode="energy_saver", setpoint_f=120),
            ],
            grants=[grant(NOW, "g", -5, 300, max_f=146)],
            observed=obs(mode="heat_pump", setpoint_raw=109),
            shadow=False,
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
            **SURPLUS,
        )
        on_a = {
            "mode": "heat_pump",
            "setpoint_raw": 120,
            "compressor_on": True,
            "surplus_on": True,
        }
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        for m in (4, 20, 21):
            run(planner, minutes(m), self._on_heater(planner, **on_a))
        assert planner.raise_state is not None
        raised = {**on_a, "setpoint_raw": 127}
        run(planner, minutes(25), self._on_heater(planner, **raised))
        off = {**raised, "mode": "power_off", "compressor_on": None}
        run(planner, minutes(30), self._on_heater(planner, **off))
        run(planner, minutes(31), self._on_heater(planner, **off))
        run(planner, minutes(40), self._on_heater(planner, **raised))

        def no_exit(e):
            return e.kind == KIND_PRECEDENCE_EXIT

        assert (
            run(planner, minutes(41), self._without(planner, no_exit, **raised))
            is None
        )
        assert "a" not in planner.removed_segments
        assert planner.segment_in_force(minutes(41)) == "a"
        stopped = {**raised, "compressor_on": False}
        write = run(
            planner, minutes(60), self._without(planner, no_exit, **stopped)
        )
        assert write is not None
        (lower,) = (e for e in write.added if e.kind == KIND_GRANT_LOWER)
        assert (lower.mode, lower.setpoint_raw) == ("heat_pump", 120)

    def test_an_exit_deleted_after_a_persons_change_keeps_the_segment(self):
        planner = planner_with(
            [
                segment(NOW, "x", -5, mode="heat_pump", setpoint_f=130),
                segment(NOW, "a", 30, mode="heat_pump", setpoint_f=140),
                segment(NOW, "c", 600, mode="energy_saver", setpoint_f=120),
            ],
            observed=obs(mode="heat_pump", setpoint_raw=109),
            shadow=False,
        )
        run(
            planner,
            minutes(1),
            self._on_heater(planner, mode="heat_pump", setpoint_raw=109),
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(31), self._on_heater(planner, **on_a))
        mine = {"mode": "heat_pump", "setpoint_raw": 124}
        run(planner, minutes(40), self._on_heater(planner, **mine))
        vacation = {"mode": "vacation", "setpoint_raw": 124}
        run(planner, minutes(50), self._on_heater(planner, **vacation))
        write = run(planner, minutes(70), self._on_heater(planner, **mine))
        assert write is not None
        assert KIND_PRECEDENCE_EXIT in {e.kind for e in write.added}
        without = self._without(
            planner, lambda e: e.kind == KIND_PRECEDENCE_EXIT, **mine
        )
        assert run(planner, minutes(71), without) is None
        assert statuses(planner)["a"][0] == "in_force"
        assert planner.segment_in_force(minutes(71)) == "a"

    @pytest.mark.parametrize("precedence", ["vacation", "power_off"])
    def test_a_segment_skipped_in_precedence_is_re_asserted(self, precedence):
        """Segment b's entry was deleted; a's was skipped in the precedence.

        a is in force, not a hold-over: its exit is written.
        """
        on_x = {"mode": "heat_pump", "setpoint_raw": 120}
        planner = planner_with(
            [
                segment(NOW, "x", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "a", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "b", 50, mode="electric", setpoint_f=120),
                segment(NOW, "c", 240, mode="heat_pump", setpoint_f=120),
            ],
            observed=obs(**on_x),
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner, **on_x))

        def no_b(e):
            return e.serves == "b"

        run(planner, minutes(2), self._without(planner, no_b, **on_x))
        held = {"mode": precedence, "setpoint_raw": 120}
        run(planner, minutes(20), self._without(planner, no_b, **held))
        run(planner, minutes(40), self._without(planner, no_b, **held))
        write = run(planner, minutes(60), self._without(planner, no_b, **on_x))
        assert write is not None
        assert (KIND_PRECEDENCE_EXIT, "a") in {
            (e.kind, e.serves) for e in write.added
        }

    @pytest.mark.parametrize("keep_x", [False, True])
    def test_with_nothing_in_force_the_guard_restores_the_state_before(
        self, keep_x
    ):
        """A new plan wants w now; a person deletes w's near-term entry."""
        running = {
            "mode": "heat_pump",
            "setpoint_raw": 120,
            "compressor_on": True,
            "surplus_on": True,
        }
        grants = [grant(NOW, "g", -5, 180, max_f=146)]
        segments = [
            segment(NOW, "x", -5, mode="heat_pump", setpoint_f=140),
            segment(NOW, "c", 400, mode="energy_saver", setpoint_f=120),
        ]
        planner = planner_with(
            segments,
            grants=grants,
            observed=obs(**running),
            shadow=False,
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
            **SURPLUS,
        )
        run(planner, minutes(1), self._on_heater(planner, **running))
        run(planner, minutes(10), self._on_heater(planner, **running))
        raised = {**running, "setpoint_raw": 127}
        run(planner, minutes(13), self._on_heater(planner, **raised))
        new = [
            *(segments[:1] if keep_x else []),
            segment(NOW, "w", 14, mode="heat_pump", setpoint_f=135),
            segments[1],
        ]
        give(
            planner,
            new,
            grants=grants,
            now=minutes(15),
            observed=self._on_heater(planner, **raised),
            intent_id="i-2",
        )

        def no_w(e):
            return e.kind == KIND_NEAR_TERM and e.serves == "w"

        run(planner, minutes(16), self._without(planner, no_w, **raised))
        assert "w" in planner.removed_segments
        rs = planner.raise_state
        assert rs is not None and rs.guard is not None
        assert (rs.guard.mode, rs.guard.setpoint_raw) == ("heat_pump", 120)
        # The raise is still on the heater.
        assert planner.wanted_state(minutes(16)) == State("heat_pump", 127)
        stopped = {**raised, "compressor_on": False}
        write = run(
            planner, minutes(30), self._without(planner, no_w, **stopped)
        )
        assert write is not None
        (lower,) = (e for e in write.added if e.kind == KIND_GRANT_LOWER)
        assert (lower.mode, lower.setpoint_raw) == ("heat_pump", 120)

    def test_a_plan_adopted_in_vacation_falls_back_to_what_took_effect(
        self,
    ):
        """Segment b began in Vacation, skipped; a new plan comes then.

        Its exit for b is deleted: the state a put in force holds, not b's.
        """
        segments = [
            segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
            segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
            segment(NOW, "c", 240, setpoint_f=120),
        ]
        planner = planner_with(segments, shadow=False)
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        # The pass after a's near-term entry fires.
        run(planner, minutes(3), self._on_heater(planner, **on_a))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        give(
            planner,
            segments,
            now=minutes(35),
            observed=self._on_heater(planner, mode="vacation"),
            intent_id="i-2",
        )
        run(planner, minutes(40), self._on_heater(planner, mode="vacation"))
        run(planner, minutes(41), self._on_heater(planner, **on_a))
        without = self._without(
            planner, lambda e: e.kind == KIND_PRECEDENCE_EXIT, **on_a
        )
        assert run(planner, minutes(42), without) is None
        assert run(planner, minutes(50), without) is None
        assert statuses(planner)["b"][0] == "removed"
        assert planner.wanted_state(minutes(50)) == State("heat_pump", 120)

    def test_a_plan_adopted_in_vacation_never_wants_vacation(self):
        vacation = {"mode": "vacation", "setpoint_raw": OWNER_SETPOINT}
        planner = planner_with(
            [
                segment(NOW, "a", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 240, setpoint_f=130),
            ],
            observed=obs(**vacation),
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner, **vacation))
        run(planner, minutes(20), self._on_heater(planner))
        without = self._without(
            planner, lambda e: e.kind == KIND_PRECEDENCE_EXIT
        )
        run(planner, minutes(21), without)
        assert "a" in planner.removed_segments
        wanted = planner.wanted_state(minutes(30))
        assert wanted is None or wanted.mode != "vacation"

    def test_a_declined_segments_pending_entries_are_withdrawn(self):
        """Deleting a segment's entry also withdraws its pending near-term.

        And an old plan's entry, stale, is not the new plan's: deleting it
        declines nothing.
        """
        planner = planner_with(
            [
                segment(NOW, "x", -5, mode="energy_saver", setpoint_f=120),
                segment(NOW, "s1", 300, mode="heat_pump", setpoint_f=140),
            ],
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner))
        run(planner, minutes(10), self._on_heater(planner, mode="power_off"))
        give(
            planner,
            [segment(NOW, "s1", 15, mode="heat_pump", setpoint_f=130)],
            now=minutes(20),
            observed=self._on_heater(planner, mode="power_off"),
            intent_id="i-2",
        )
        assert (KIND_NEAR_TERM, "s1") in {
            (e.kind, e.serves) for e in planner.extra
        }

        def old(e):
            return e.kind == KIND_PLAN and e.serves == "s1"

        run(planner, minutes(30), self._without(planner, old, mode="power_off"))
        assert "s1" not in planner.removed_segments
        write = run(planner, minutes(40), self._on_heater(planner))
        assert write is not None
        assert (KIND_PRECEDENCE_EXIT, "s1") in {
            (e.kind, e.serves) for e in write.added
        } or (KIND_NEAR_TERM, "s1") in {(e.kind, e.serves) for e in write.added}
        # Now its pending entry is deleted: nothing more is written for it.
        pending = self._without(
            planner, lambda e: e.serves == "s1" and e.fires_at > minutes(41)
        )
        run(planner, minutes(41), pending)
        assert "s1" in planner.removed_segments
        assert not [e for e in planner.extra if e.serves == "s1"]
        assert run(planner, minutes(50), pending) is None

    def test_a_plan_entry_deleted_after_it_fired_leaves_its_segment(self):
        """Seen on the heater at its minute, it fired: it took effect.

        Deleted within the minute after, it still ends nothing.
        """
        on_x = {"mode": "heat_pump", "setpoint_raw": 120}
        planner = planner_with(
            [
                segment(NOW, "x", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "a", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            observed=obs(**on_x),
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner, **on_x))
        on_a = {"mode": "energy_saver", "setpoint_raw": 109}
        run(planner, minutes(30), self._on_heater(planner, **on_a))
        without_a = self._without(planner, lambda e: e.serves == "a", **on_a)
        run(planner, minutes(30.5), without_a)
        assert "a" not in planner.removed_segments
        assert planner.segment_in_force(minutes(31)) == "a"

    # Found by the second adversarial review.

    def test_a_stale_list_read_does_not_count_an_entry_as_fired(self):
        """A pass at b's minute on a list read before it, then a fresh read.

        The fresh read shows b's entry gone: it never fired, so b is out.
        """
        planner = planner_with(
            [
                segment(NOW, "a", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 60, setpoint_f=120),
            ],
            shadow=False,
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(
            planner,
            minutes(1),
            self._on_heater(planner, reservations_read_at=minutes(1), **on_a),
        )
        run(
            planner,
            minutes(3),
            self._on_heater(planner, reservations_read_at=minutes(3), **on_a),
        )
        at_b = minutes(30) + timedelta(seconds=1)
        cached = self._on_heater(
            planner, reservations_read_at=minutes(3), **on_a
        )
        planner.step(at_b, cached)
        fresh = self._without(
            planner,
            lambda e: e.serves == "b",
            reservations_read_at=at_b,
            **on_a,
        )
        planner.step(at_b, fresh)
        assert "b" in planner.removed_segments
        assert planner.segment_in_force(at_b) == "a"

    def test_an_exit_for_a_segment_redefined_under_its_id_is_its_own(self):
        """A plan in Vacation reuses s0 for another state; its exit goes.

        The heater never took the new s0: it is left out.
        """
        on_s0 = {"mode": "heat_pump", "setpoint_raw": 120}
        planner = planner_with(
            [segment(NOW, "s0", -5, mode="heat_pump", setpoint_f=140)],
            observed=obs(**on_s0),
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner, **on_s0))
        vacation = {"mode": "vacation", "setpoint_raw": 120}
        run(planner, minutes(10), self._on_heater(planner, **vacation))
        give(
            planner,
            [segment(NOW, "s0", -5, mode="electric", setpoint_f=130)],
            now=minutes(20),
            observed=self._on_heater(planner, **vacation),
            intent_id="i-2",
        )
        run(planner, minutes(21), self._on_heater(planner, **vacation))
        write = run(planner, minutes(40), self._on_heater(planner, **on_s0))
        assert write is not None
        assert (KIND_PRECEDENCE_EXIT, "s0") in {
            (e.kind, e.serves) for e in write.added
        }
        without = self._without(
            planner, lambda e: e.kind == KIND_PRECEDENCE_EXIT, **on_s0
        )
        assert run(planner, minutes(41), without) is None
        assert "s0" in planner.removed_segments
        assert planner.wanted_state(minutes(41)) == State("heat_pump", 120)

    def test_an_old_entry_for_a_segment_moved_earlier_is_not_its_own(self):
        """While powered off, a new plan moves b earlier, same state.

        The old plan's entry for b, still on the heater, is deleted: that
        is not the new plan's entry, and b is not left out.
        """
        planner = planner_with(
            [
                segment(NOW, "x", -5, mode="energy_saver", setpoint_f=120),
                segment(NOW, "b", 40, mode="heat_pump", setpoint_f=140),
            ],
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner))
        run(planner, minutes(5), self._on_heater(planner, mode="power_off"))
        give(
            planner,
            [
                segment(NOW, "x", -5, mode="energy_saver", setpoint_f=120),
                segment(NOW, "b", 20, mode="heat_pump", setpoint_f=140),
            ],
            now=minutes(6),
            observed=self._on_heater(planner, mode="power_off"),
            intent_id="i-2",
        )

        def old_b(e):
            return e.kind == KIND_PLAN and e.serves == "b"

        run(
            planner, minutes(7), self._without(planner, old_b, mode="power_off")
        )
        assert "b" not in planner.removed_segments

    def test_a_second_entry_for_a_segment_that_took_effect_is_a_re_assert(
        self,
    ):
        """An exit fired and put b in force; its later near-term is deleted.

        b stays in force: that entry could only re-assert it.
        """
        planner = planner_with(
            [
                segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            shadow=False,
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        on_b = {"mode": "energy_saver", "setpoint_raw": 109}
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        run(planner, minutes(3), self._on_heater(planner, **on_a))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        run(planner, minutes(40), self._on_heater(planner, **on_a))
        (exit_entry,) = (
            e for e in planner.owned if e.kind == KIND_PRECEDENCE_EXIT
        )
        run(planner, minutes(43), self._on_heater(planner, **on_b))
        assert planner.settled is not None
        assert planner.settled[:2] == ("b", State("energy_saver", 109))
        # A near-term for b, as a late confirmation issues (section 5.2).
        again = OwnedEntry(
            KIND_NEAR_TERM, "b", minutes(50), "energy_saver", 109
        )
        planner.owned.append(again)
        assert exit_entry.fires_at < again.fires_at
        run(
            planner,
            minutes(44),
            self._without(planner, lambda e: e == again, **on_b),
        )
        assert "b" not in planner.removed_segments
        assert planner.segment_in_force(minutes(44)) == "b"

    def test_an_entry_due_while_the_state_cannot_be_read_is_skipped(self):
        """Vacation, with the heater's mode unreadable around b's minute.

        b's entry may have been skipped: its exit is what puts b in force.
        """
        planner = planner_with(
            [
                segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            shadow=False,
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        run(planner, minutes(3), self._on_heater(planner, **on_a))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        unread = {"mode": None, "setpoint_raw": None}
        run(planner, minutes(29), self._on_heater(planner, **unread))
        run(planner, minutes(32), self._on_heater(planner, **unread))
        assert planner.settled is not None
        assert planner.settled[0] == "a"

    def test_a_restart_keeps_the_exit_of_a_segment_holding_over(self):
        """Segment b began in Vacation; c's entry is deleted then.

        Vacation ends: the exit for b, holding over c, waits to fire when
        Home Assistant restarts. It is kept, and b is put in force.
        """
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        planner = planner_with(
            [
                segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 45, mode="heat_pump", setpoint_f=125),
                segment(NOW, "d", 240, mode="heat_pump", setpoint_f=140),
            ],
            shadow=False,
        )
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        run(
            planner,
            minutes(40),
            self._without(planner, lambda e: e.serves == "c", mode="vacation"),
        )
        run(planner, minutes(50), self._on_heater(planner, **on_a))
        (exit_entry,) = (
            e for e in planner.owned if e.kind == KIND_PRECEDENCE_EXIT
        )
        assert exit_entry.serves == "b"
        restarted = Planner(planner.capabilities, TZ, shadow=False)
        restarted.owner = planner.owner
        restarted.load_document(planner.as_document())
        assert planner.plan is not None
        seen = self._on_heater(planner, **on_a)
        restarted.set_plan(planner.plan, minutes(51), seen, restoring=True)
        run(restarted, minutes(51), seen)
        assert exit_entry in restarted.owned
        assert restarted.segment_in_force(minutes(51)) == "b"

    def test_a_blip_in_reading_the_state_outside_precedence_is_no_skip(self):
        """The mode is unreadable for a pass just before b's minute.

        Outside Vacation and power-off, b's entry still fired, and a new
        plan carries the state it put in force.
        """
        planner = planner_with(
            [
                segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            shadow=False,
        )
        on_a = {"mode": "heat_pump", "setpoint_raw": 120}
        run(planner, minutes(1), self._on_heater(planner, **on_a))
        run(planner, minutes(3), self._on_heater(planner, **on_a))
        unread = {"mode": None, "setpoint_raw": None}
        run(planner, minutes(29), self._on_heater(planner, **unread))
        on_b = {"mode": "energy_saver", "setpoint_raw": 109}
        run(planner, minutes(31), self._on_heater(planner, **on_b))
        assert planner.settled is not None
        assert planner.settled[:2] == ("b", State("energy_saver", 109))

    def test_a_rename_before_the_near_term_is_written_asserts_it(self):
        """A plan is renamed while its near-term entry is still unwritten.

        That entry served the old id and goes; the renamed segment gets its
        own, since nothing has put its state in force.
        """
        planner = planner_with(shadow=False)
        plan = parse_plan(
            make_document(
                NOW, [segment(NOW, "s0", -45, mode="heat_pump", setpoint_f=130)]
            )
        )
        check_plan(plan, planner.capabilities)
        planner.set_plan(plan, NOW, obs())
        assert (KIND_NEAR_TERM, "s0") in {
            (e.kind, e.serves) for e in planner.extra
        }
        write = give(
            planner,
            [segment(NOW, "s0r", -45, mode="heat_pump", setpoint_f=130)],
            now=minutes(10),
            intent_id="i-2",
        )
        assert write is not None
        assert (KIND_NEAR_TERM, "s0r") in {
            (e.kind, e.serves) for e in write.added
        }

    def test_a_moved_entry_is_its_segments_after_the_collision_goes(self):
        """A person removes the entry that moved b's, and b's entry.

        b's entry is still the plan's own though nothing moves it now: b is
        removed, not written again at its unmoved minute (PR #176 review).
        """
        foreign = {"enable": 2, "week": MONDAY, "hour": 11, "min": 0}
        foreign |= {"mode": 1, "param": 100}
        segments = [
            segment(NOW, "a", -5, mode="energy_saver", setpoint_f=139.1),
            segment(NOW, "b", 60, mode="heat_pump", setpoint_f=140),
        ]
        with_foreign = obs(reservations_enabled=True, reservations=(foreign,))
        planner = planner_with(segments, observed=with_foreign, shadow=False)
        (b,) = (e for e in planner.owned if e.serves == "b")
        assert b.fires_at == minutes(61)
        run(
            planner,
            minutes(1),
            obs(
                reservations_enabled=True,
                reservations=(foreign, *(e.as_entry() for e in planner.owned)),
            ),
        )
        neither = obs(
            reservations_enabled=True,
            reservations=tuple(
                e.as_entry() for e in planner.owned if e.serves != "b"
            ),
        )
        assert run(planner, minutes(2), neither) is None
        assert "b" in planner.removed_segments


class TestUnreadableState:
    """Issue #173: a heater state that cannot be read is not precedence's end.

    The last known Vacation or power-off holds until the mode reads again.
    """

    SEGMENTS = [
        segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
        segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
        segment(NOW, "c", 240, mode="heat_pump", setpoint_f=130),
    ]
    ON_A = {"mode": "heat_pump", "setpoint_raw": 120}
    UNREAD = {"mode": None, "setpoint_raw": None}

    def _on_heater(self, planner: Planner, **overrides) -> Observed:
        return obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
            **overrides,
        )

    def test_the_exit_waits_for_vacation_to_be_read_as_over(self):
        planner = planner_with(self.SEGMENTS, shadow=False)
        run(planner, minutes(1), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        run(planner, minutes(40), self._on_heater(planner, mode="vacation"))
        for m in range(45, 50):
            write = run(
                planner, minutes(m), self._on_heater(planner, **self.UNREAD)
            )
            assert write is None or not write.added
        write = run(planner, minutes(50), self._on_heater(planner, **self.ON_A))
        assert write is not None
        assert [(e.kind, e.serves) for e in write.added] == [
            (KIND_PRECEDENCE_EXIT, "b")
        ]

    def test_power_off_keeps_the_entries_off_through_a_blip(self):
        planner = planner_with(self.SEGMENTS, shadow=False)
        run(planner, minutes(1), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(5), self._on_heater(planner, mode="power_off"))
        assert all(not e.enabled for e in planner.owned)
        for m in (6, 7, 8):
            write = run(
                planner, minutes(m), self._on_heater(planner, **self.UNREAD)
            )
            assert write is None
        assert all(not e.enabled for e in planner.owned)
        write = run(planner, minutes(10), self._on_heater(planner, **self.ON_A))
        assert write is not None
        assert all(e.enabled for e in planner.owned)
        assert KIND_PRECEDENCE_EXIT in {e.kind for e in write.added}

    def test_an_unread_state_before_any_is_read_is_no_precedence(self):
        """At start-up, nothing is known yet: passes run as usual."""
        planner = planner_with(
            self.SEGMENTS, observed=obs(**self.UNREAD), shadow=False
        )
        assert planner.suspended_by is None
        assert planner.owned


class TestLoweringAndANewRaise:
    """Issue #175: a lowering still to fire is withdrawn by a new raise."""

    def _on_heater(self, planner: Planner, **overrides) -> Observed:
        return obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
            **overrides,
        )

    def test_a_lowering_queued_while_powered_off_goes_with_a_new_raise(self):
        planner = planner_with(
            [
                segment(NOW, "x", -60, mode="heat_pump", setpoint_f=130),
                segment(NOW, "a", 3, mode="heat_pump", setpoint_f=140),
                segment(NOW, "c", 600, mode="energy_saver", setpoint_f=120),
            ],
            grants=[grant(NOW, "g", -5, 300, max_f=146)],
            observed=obs(mode="heat_pump", setpoint_raw=109),
            shadow=False,
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
            **SURPLUS,
        )
        on_a = {
            "mode": "heat_pump",
            "setpoint_raw": 120,
            "compressor_on": True,
            "surplus_on": True,
        }
        run(
            planner,
            minutes(1),
            self._on_heater(planner, mode="heat_pump", setpoint_raw=109),
        )
        for m in (4, 20, 21):
            run(planner, minutes(m), self._on_heater(planner, **on_a))
        assert planner.raise_state is not None
        raised = {**on_a, "setpoint_raw": 127}
        run(planner, minutes(25), self._on_heater(planner, **raised))
        off = {**raised, "mode": "power_off", "compressor_on": False}
        run(planner, minutes(30), self._on_heater(planner, **off))
        run(planner, minutes(31), self._on_heater(planner, **off))
        # The compressor stopped: the raise is being lowered.
        assert KIND_GRANT_LOWER in {e.kind for e in planner.extra}
        write = run(planner, minutes(40), self._on_heater(planner, **raised))
        assert write is not None
        added = {e.kind for e in write.added}
        assert KIND_GRANT_RAISE in added
        # The lowering would fire after the new raise and undo it.
        assert KIND_GRANT_LOWER not in added
        assert not [e for e in planner.owned if e.kind == KIND_GRANT_LOWER]
        assert planner.raise_state is not None

    def test_a_raise_dropped_by_a_collision_keeps_the_lowering(self):
        """PR #178 review: the new raise is moved onto the next segment.

        No raise reaches the heater then, so the lowering still to fire
        from before is kept: it restores the earlier raise's setpoint.
        """
        running = {
            "mode": "heat_pump",
            "setpoint_raw": 127,
            "compressor_on": True,
            "surplus_on": True,
        }
        planner = planner_with(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "b", 13, mode="energy_saver", setpoint_f=130),
                segment(NOW, "c", 240, setpoint_f=120),
            ],
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            observed=obs(**running),
            shadow=False,
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
            **SURPLUS,
        )
        # A lowering of an earlier raise, written and still to fire.
        lowering = OwnedEntry(
            KIND_GRANT_LOWER, "g", minutes(11), "heat_pump", 120
        )
        planner.extra.append(lowering)
        planner.owned.append(lowering)
        # Someone else's entry takes the raise's minute (10:12), so the
        # raise would move to 10:13, b's start.
        foreign = {"enable": 2, "week": MONDAY, "hour": 10, "min": 12}
        foreign |= {"mode": 1, "param": 100}
        run(
            planner,
            minutes(10),
            obs(
                reservations_enabled=True,
                reservations=(foreign, *(e.as_entry() for e in planner.owned)),
                **running,
            ),
        )
        assert planner.raise_state is None
        assert lowering in planner.owned


class TestStaleNearTerm:
    """Issue #172: a near-term entry serves only while its segment is in force."""

    SEGMENTS = [
        segment(NOW, "a", -10, mode="high_demand", setpoint_f=125),
        segment(NOW, "b", 30, mode="heat_pump", setpoint_f=130),
        segment(NOW, "c", 240, mode="energy_saver", setpoint_f=140),
    ]

    def _on_heater(self, planner: Planner, **overrides) -> Observed:
        return obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
            **overrides,
        )

    def _powered_off_through_b(self) -> Planner:
        """A plan arrives while powered off; b begins before power returns."""
        planner = Planner(capabilities(), TZ, shadow=False)
        planner.owner = OWNER
        run(planner, NOW, obs(mode="power_off", reservations_enabled=True))
        plan = parse_plan(make_document(minutes(1), self.SEGMENTS))
        check_plan(plan, planner.capabilities)
        planner.set_plan(
            plan, minutes(1), self._on_heater(planner, mode="power_off")
        )
        for m in (1, 5, 20, 40, 50):
            run(planner, minutes(m), self._on_heater(planner, mode="power_off"))
        return planner

    def test_an_ended_segments_near_term_is_not_written(self):
        planner = self._powered_off_through_b()
        write = run(planner, minutes(60), self._on_heater(planner))
        assert write is not None
        assert "a" not in {e.serves for e in write.added}
        assert not [e for e in planner.extra if e.serves == "a"]

    def test_the_exit_is_the_one_entry_for_the_segment_in_force(self):
        planner = self._powered_off_through_b()
        write = run(planner, minutes(60), self._on_heater(planner))
        assert write is not None
        for_b = [
            (e.kind, e.serves)
            for e in write.added
            if e.kind in (KIND_NEAR_TERM, KIND_PRECEDENCE_EXIT)
        ]
        assert for_b == [(KIND_PRECEDENCE_EXIT, "b")]

    def test_power_returning_as_a_segment_begins_writes_one_entry(self):
        """The exit asserts b; no near-term is added beside it.

        b comes with a plan adopted while powered off, so no entry of its
        own was written for it.
        """
        planner = planner_with(self.SEGMENTS, shadow=False)
        run(planner, minutes(1), self._on_heater(planner))
        run(planner, minutes(20), self._on_heater(planner, mode="power_off"))
        give(
            planner,
            [
                self.SEGMENTS[0],
                segment(NOW, "b", 30, mode="electric", setpoint_f=130),
                self.SEGMENTS[2],
            ],
            now=minutes(25),
            observed=self._on_heater(planner, mode="power_off"),
            intent_id="i-2",
        )
        write = run(planner, minutes(30), self._on_heater(planner))
        assert write is not None
        for_b = [
            (e.kind, e.serves)
            for e in write.added
            if e.kind in (KIND_NEAR_TERM, KIND_PRECEDENCE_EXIT)
        ]
        assert for_b == [(KIND_PRECEDENCE_EXIT, "b")]

    @pytest.mark.parametrize("late", [False, True])
    def test_an_exit_whose_write_fails_into_the_next_segment_is_dropped(
        self, late
    ):
        """PR #179 review: the exit half of the rule.

        Vacation ends just before b begins, and the exit for a is written.
        Its write is rejected, or confirmed only after its minute, with the
        retry after b's start: it is dropped, not moved into b's time.
        """
        planner = planner_with(self.SEGMENTS, shadow=False)
        run(planner, minutes(1), self._on_heater(planner))
        run(planner, minutes(10), self._on_heater(planner, mode="vacation"))
        write = planner.step(minutes(26), self._on_heater(planner))
        assert write is not None
        (exit_entry,) = (
            e for e in write.added if e.kind == KIND_PRECEDENCE_EXIT
        )
        assert exit_entry.serves == "a"
        assert exit_entry.fires_at < minutes(30)
        if late:
            planner.commit(write, confirmed_at=minutes(31))
        else:
            planner.reject(
                write, minutes(26), retry_at=minutes(31), final=False
            )
        assert not [
            e
            for e in planner.extra
            if e.kind == KIND_PRECEDENCE_EXIT and e.serves == "a"
        ]


class TestExitAfterAntiLegionella:
    """Issue #174: an exit owed at Vacation's end waits out anti-legionella."""

    SEGMENTS = [
        segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
        segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
        segment(NOW, "c", 240, mode="heat_pump", setpoint_f=130),
    ]
    ON_A = {"mode": "heat_pump", "setpoint_raw": 120}

    def _on_heater(self, planner: Planner, **overrides) -> Observed:
        return obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
            **overrides,
        )

    def _vacation_into_anti_legionella(self) -> Planner:
        planner = planner_with(self.SEGMENTS, shadow=False)
        run(planner, minutes(1), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        run(planner, minutes(40), self._on_heater(planner, mode="vacation"))
        cycle = self._on_heater(planner, anti_legionella_busy=True, **self.ON_A)
        # The feature does not write the list during the cycle (5.9).
        assert run(planner, minutes(50), cycle) is None
        return planner

    def _exit_for_b(self, write) -> bool:
        return write is not None and (KIND_PRECEDENCE_EXIT, "b") in {
            (e.kind, e.serves) for e in write.added
        }

    def test_the_exit_is_written_when_the_cycle_ends(self):
        planner = self._vacation_into_anti_legionella()
        write = run(planner, minutes(70), self._on_heater(planner, **self.ON_A))
        assert self._exit_for_b(write)
        # Written once.
        assert (
            run(planner, minutes(71), self._on_heater(planner, **self.ON_A))
            is None
        )

    def test_the_owed_exit_survives_a_restart(self):
        planner = self._vacation_into_anti_legionella()
        restarted = Planner(planner.capabilities, TZ, shadow=False)
        restarted.owner = planner.owner
        restarted.load_document(planner.as_document())
        assert planner.plan is not None
        cycle = self._on_heater(planner, anti_legionella_busy=True, **self.ON_A)
        restarted.set_plan(planner.plan, minutes(55), cycle, restoring=True)
        run(restarted, minutes(55), cycle)
        write = run(
            restarted, minutes(70), self._on_heater(restarted, **self.ON_A)
        )
        assert self._exit_for_b(write)

    def test_an_unreadable_state_does_not_spend_the_owed_exit(self):
        planner = self._vacation_into_anti_legionella()
        unread = self._on_heater(planner, mode=None, setpoint_raw=None)
        write = run(planner, minutes(60), unread)
        assert not self._exit_for_b(write)
        write = run(planner, minutes(70), self._on_heater(planner, **self.ON_A))
        assert self._exit_for_b(write)

    def test_a_known_cycle_is_kept_though_the_mode_is_unreadable(self):
        """PR #180 review: restarted during the cycle, the mode unread.

        The heater still reports the cycle: nothing is written during it,
        and the owed exit follows once it ends.
        """
        planner = self._vacation_into_anti_legionella()
        restarted = Planner(planner.capabilities, TZ, shadow=False)
        restarted.owner = planner.owner
        restarted.load_document(planner.as_document())
        assert planner.plan is not None
        unread_cycle = self._on_heater(
            planner, anti_legionella_busy=True, mode=None, setpoint_raw=None
        )
        restarted.set_plan(
            planner.plan, minutes(55), unread_cycle, restoring=True
        )
        assert run(restarted, minutes(55), unread_cycle) is None
        assert restarted.suspended_by == "anti_legionella"
        write = run(
            restarted, minutes(70), self._on_heater(restarted, **self.ON_A)
        )
        assert self._exit_for_b(write)


class TestReadBackOfSkippedEntries:
    """Issue #182: an entry the heater skipped in Vacation is not read back."""

    SEGMENTS = [
        segment(NOW, "a", -60, mode="heat_pump", setpoint_f=140),
        segment(NOW, "b", 30, mode="energy_saver", setpoint_f=130),
        segment(NOW, "c", 240, mode="heat_pump", setpoint_f=130),
    ]
    ON_A = {"mode": "heat_pump", "setpoint_raw": 120}
    ON_B = {"mode": "energy_saver", "setpoint_raw": 109}

    def _on_heater(self, planner: Planner, **overrides) -> Observed:
        return obs(
            reservations_enabled=True,
            reservations=tuple(e.as_entry() for e in planner.owned),
            **overrides,
        )

    @pytest.mark.parametrize("precedence", ["vacation", "power_off"])
    def test_a_segment_begun_in_precedence_is_read_back_by_its_exit(
        self, precedence
    ):
        """Power-off switches the entries off and on again; Vacation not."""
        planner = planner_with(self.SEGMENTS, shadow=False)
        run(planner, minutes(1), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(3), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(20), self._on_heater(planner, mode=precedence))
        run(planner, minutes(40), self._on_heater(planner, mode=precedence))
        # It ends: b's plan entry (10:30) was skipped; the heater is still
        # in a's state, and the exit for b is written.
        write = run(planner, minutes(41), self._on_heater(planner, **self.ON_A))
        assert write is not None
        assert KIND_PRECEDENCE_EXIT in {e.kind for e in write.added}
        assert "b" not in planner.readback
        assert statuses(planner)["b"][0] != "failed"
        # The exit fires and the heater takes b's state: read back, applied.
        run(planner, minutes(44), self._on_heater(planner, **self.ON_B))
        run(planner, minutes(46), self._on_heater(planner, **self.ON_B))
        assert "b" not in planner.readback
        assert statuses(planner)["b"][0] == "in_force"

    def test_an_exit_not_applied_is_still_flagged(self):
        """The exit is read back as any entry: not applied, it fails."""
        planner = planner_with(self.SEGMENTS, shadow=False)
        run(planner, minutes(1), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(3), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(20), self._on_heater(planner, mode="vacation"))
        run(planner, minutes(41), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(44), self._on_heater(planner, **self.ON_A))
        run(planner, minutes(46), self._on_heater(planner, **self.ON_A))
        assert statuses(planner)["b"] == ("failed", "not_applied_on_device")


class TestGrantRules:
    """Issue #183: a grant's own timing rules replace the declared ones."""

    RUNNING = obs(
        mode="heat_pump", setpoint_raw=120, compressor_on=True, surplus_on=True
    )

    def _planner(self, **rules) -> Planner:
        return planner_with(
            [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)],
            grants=[grant(NOW, "g", -5, 180, max_f=146, **rules)],
            observed=self.RUNNING,
            **SURPLUS,
        )

    def test_the_grants_surplus_on_before_raise(self):
        planner = self._planner(surplus_on_before_raise_min=3)
        assert planner.next_event_at == minutes(3)
        assert run(planner, minutes(2), self.RUNNING) is None
        write = run(planner, minutes(3), self.RUNNING)
        assert write is not None
        assert write.reason == WRITE_GRANT_RAISE

    def test_the_grants_surplus_off_before_lower_and_min_run(self):
        planner = self._planner(
            surplus_on_before_raise_min=0,
            surplus_off_before_lower_min=2,
            min_run_before_lower_min=0,
        )
        assert planner.raise_state is not None
        raised = replace(self.RUNNING, setpoint_raw=127)
        run(planner, minutes(3), raised)
        gone = replace(raised, surplus_on=False)
        assert run(planner, minutes(5), gone) is None
        assert planner.next_event_at == minutes(7)
        assert run(planner, minutes(6), gone) is None
        write = run(planner, minutes(7), gone)
        assert write is not None
        assert write.reason == WRITE_GRANT_LOWER

    def test_the_grants_min_run_before_lower(self):
        """A minimum run of its own, 20 min, not the declared 120."""
        planner = self._planner(
            surplus_on_before_raise_min=0,
            surplus_off_before_lower_min=2,
            min_run_before_lower_min=20,
        )
        assert planner.raise_state is not None
        raised = replace(self.RUNNING, setpoint_raw=127)
        run(planner, minutes(3), raised)
        gone = replace(raised, surplus_on=False)
        run(planner, minutes(5), gone)
        # Surplus has been gone 2 min at 7, but the compressor has run
        # only 7 of its 20: the raise waits for the run.
        assert run(planner, minutes(7), gone) is None
        assert planner.raise_state is not None
        assert planner.next_event_at == minutes(20)
        assert run(planner, minutes(19), gone) is None
        write = run(planner, minutes(20), gone)
        assert write is not None
        assert write.reason == WRITE_GRANT_LOWER

    def test_without_rules_the_declared_ones_apply(self):
        planner = self._planner()
        assert run(planner, minutes(9), self.RUNNING) is None
        write = run(planner, minutes(10), self.RUNNING)
        assert write is not None
        assert write.reason == WRITE_GRANT_RAISE
