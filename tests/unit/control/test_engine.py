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
    WRITE_NEAR_TERM,
    WRITE_PLAN,
    Planner,
    Report,
    State,
)
from custom_components.nwp500.control.entries import (
    KIND_GRANT_RAISE,
    KIND_GUARD,
    KIND_NEAR_TERM,
    KIND_PLAN,
    OwnedEntry,
    near_term_minute,
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
from custom_components.nwp500.control.sensor import ControlLastWriteSensor

from .conftest import capabilities, grant, make_document, segment

TZ = ZoneInfo("America/Los_Angeles")
# A Monday morning, local time.
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=TZ)
MONDAY = 64
# 119 half-degrees is 59.5 degC / 139.1 degF.
OWNER_SETPOINT = 119

EXAMPLES = Path("docs/examples")
SCHEMA = Path("docs/external-control-protocol-1.schema.json")
EXAMPLE_WRITES = ("last-write-plan",)


def obs(**overrides) -> Observed:
    """The device on the owner's state, idle, reservations switched off."""
    values = {
        "mode": "energy_saver",
        "setpoint_raw": OWNER_SETPOINT,
        "tou_on": False,
        "reservations_enabled": False,
        "reservations": (),
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
    shadow: bool = True,
    **options,
) -> Planner:
    planner = Planner(capabilities(**options), TZ, shadow=shadow)
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
        planner = Planner(capabilities(), TZ)
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

    The plan arrives at 05:00:12, after its first segment began. The write
    is confirmed, and the device then holds the list written. The state is in UTC, as
    Home Assistant shows a timestamp sensor's.
    """
    plan = parse_plan(json.loads((EXAMPLES / "plan-day.json").read_text()))
    at = plan.issued_at
    planner = Planner(
        capabilities(control_mode="live", control_live_segments=True),
        TZ,
        shadow=False,
    )
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

    return {"last-write-plan": confirmed(at, obs())}


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

    def test_min_resolves_to_the_heaters_own_minimum(self):
        planner = planner_with(
            [segment(NOW, "a", 60, mode="energy_saver", setpoint="min")],
        )
        assert (
            planner.owned[0].setpoint_raw
            == planner.capabilities.setpoint_min_raw
        )

    def test_a_setpoint_is_written_as_the_plan_gives_it(self):
        """No bounds: the heater clamps what it is given."""
        planner = planner_with(
            [segment(NOW, "a", 60, mode="heat_pump", setpoint_f=170)],
        )
        assert planner.owned[0].setpoint_raw == 153

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

    def test_other_entries_count_against_the_budget(self):
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
        planner = planner_with(
            self._alternating(7),
            observed=observed,
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
        run(planner, NOW)
        write = give(planner, self.SEGMENTS, restoring=True)
        assert [e.kind for e in write.added] == [KIND_PLAN]


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
        planner = planner_with(observed=observed)
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
        planner = planner_with(observed=observed)
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

    def test_a_persons_vacation_is_reported(self):
        """Told to the scheduler like any change; it decides (#192)."""
        planner = planner_with()
        run(planner, minutes(1), obs())
        run(planner, minutes(2), obs(mode="vacation"))
        assert planner.reports[REPORT_MODE].value == "vacation"

    def test_a_persons_power_off_is_reported(self):
        planner = planner_with()
        run(planner, minutes(1), obs())
        run(planner, minutes(2), obs(mode="power_off"))
        assert planner.reports[REPORT_MODE].value == "power_off"

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

    def test_no_plan(self):
        assert planner_with().ack(None).state == "none"


class TestPersistence:
    def test_round_trip(self):
        planner = planner_with(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "t", 60, setpoint_f=130),
            ]
        )
        run(planner, minutes(11), obs(setpoint_raw=100))
        document = planner.as_document()

        again = Planner(planner.capabilities, TZ)
        again.load_document(document)

        assert again.owned == planner.owned
        assert again.extra == planner.extra
        assert again.asserted == planner.asserted
        assert again.reports == planner.reports
        assert again.last_write == planner.last_write
        assert again.as_document() == document

    def test_a_surplus_raise_stored_by_an_earlier_version_is_dropped(self):
        """Grants are not supported: its entries are no longer wanted."""
        planner = planner_with(
            [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)]
        )
        document = planner.as_document()
        raise_entry = OwnedEntry(
            "grant_raise", "g", minutes(2), "heat_pump", 126
        ).as_document()
        guard = OwnedEntry("guard", "g", minutes(60), "heat_pump", 120)
        document = {
            **document,
            "extra": [*document["extra"], raise_entry],
            "owned": [*document["owned"], guard.as_document()],
            "raise": {
                "grant_id": "g",
                "raised_at": NOW.isoformat(),
                "entry": raise_entry,
                "guard": guard.as_document(),
            },
            "raised_in_cycle": True,
        }

        again = Planner(planner.capabilities, TZ, shadow=True)
        again.load_document(document)

        assert all(e.kind != "grant_raise" for e in again.extra)
        assert "raise" not in again.as_document()
        # The guard on the heater is the feature's: the next write removes it.
        write = again.step(minutes(1), obs())
        assert write is not None
        assert guard in write.removed

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

    # Found by the adversarial review of the effective timeline.

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
