"""The planning engine (spec section 5, issue #158), in shadow.

Every scenario drives the engine with times and `Observed` snapshots only,
as the device controller does, so the rules are tested without Home
Assistant, timers or a device.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.nwp500.const import (
    CONF_CONTROL_HOLD_OFF_SUPPORTED,
    CONF_CONTROL_MIN_RUN_BEFORE_STOP_MIN,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    CONF_CONTROL_SETPOINT_MIN_F,
    CONF_CONTROL_SURPLUS_ENTITY,
    CONF_CONTROL_TOU_OFF_FOR_MODE,
)
from custom_components.nwp500.control.baseline import Baseline
from custom_components.nwp500.control.engine import (
    HOLD_COMPRESSOR_MIN_RUN,
    HOLD_NO_REVERSAL,
    HOLD_REQUEST_CYCLE,
    REASON_ENTRY_BUDGET,
    REASON_FLOOR_PREVENTS_HOLD_OFF,
    RESTORE_DAILY_REVERT,
    RESTORE_DISABLED,
    RESTORE_EXPIRY,
    RESTORE_INTENT_ENDED,
    RESTORE_OVERRIDE_EXPIRED,
    RESTORE_STALE_INTENT,
    RESTORE_STARTUP,
    ControlEngine,
)
from custom_components.nwp500.control.entries import (
    ALL_DAYS,
    KIND_CLOSING,
    KIND_DAILY_REVERT,
    KIND_START,
    schedule_hash,
)
from custom_components.nwp500.control.evaluate import evaluate_intent
from custom_components.nwp500.control.intent import parse_intent
from custom_components.nwp500.control.observed import Observed

from .conftest import capabilities, directive, make_document

TZ = ZoneInfo("America/Los_Angeles")
# A Monday morning, local time, well clear of the 03:00 daily revert.
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=TZ)
# 119 half-degrees is 59.5 degC / 139.1 degF; a typical setpoint.
BASELINE_SETPOINT = 119
BASELINE = Baseline(
    mode="energy_saver",
    setpoint_raw=BASELINE_SETPOINT,
    tou_enabled=True,
    reservations_enabled=False,
)
HOLD_OFF = {CONF_CONTROL_HOLD_OFF_SUPPORTED: True}
SURPLUS = {CONF_CONTROL_SURPLUS_ENTITY: "binary_sensor.surplus"}


def obs(**overrides) -> Observed:
    """The device at the baseline, idle, tank a little below setpoint."""
    values = {
        "mode": "energy_saver",
        "setpoint_raw": BASELINE_SETPOINT,
        "tou_on": True,
        "compressor_on": False,
        "upper_tank_raw": 115,
        "reservations_enabled": False,
        "reservations": (),
        "surplus_on": None,
    }
    values.update(overrides)
    return Observed(**values)


def engine_with(
    directives: list[dict] | None = None,
    *,
    now: datetime = NOW,
    observed: Observed | None = None,
    baseline: Baseline | None = BASELINE,
    intent_kwargs: dict | None = None,
    **options,
) -> ControlEngine:
    """An engine with the baseline and, if given, an accepted intent."""
    engine = ControlEngine(capabilities(**options), TZ)
    engine.baseline = baseline
    observed = observed or obs()
    engine.evaluate(now, observed)
    if directives is not None:
        give(
            engine,
            directives,
            now=now,
            observed=observed,
            **(intent_kwargs or {}),
        )
    return engine


def give(
    engine: ControlEngine,
    directives: list[dict],
    *,
    now: datetime = NOW,
    observed: Observed | None = None,
    **intent_kwargs,
) -> None:
    """Hand the engine a validated intent."""
    intent = parse_intent(
        make_document(now, directives, valid_for=24 * 60, **intent_kwargs),
        now=now,
    )
    ack = evaluate_intent(intent, engine.capabilities, now=now)
    engine.set_intent(intent, ack, now, observed or obs())


def entries_of(engine: ControlEngine, kind: str | None = None):
    return [e for e in engine.wanted.entries if kind is None or e.kind == kind]


def minutes(m: float) -> datetime:
    return NOW + timedelta(minutes=m)


class TestTranslation:
    """Section 5.2: directives into entries and direct writes."""

    def test_a_future_charge_gets_a_start_and_a_closing_entry(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )

        wanted = engine.wanted
        assert wanted.mode == "energy_saver"
        assert wanted.setpoint_raw == BASELINE_SETPOINT
        start = entries_of(engine, KIND_START)
        closing = entries_of(engine, KIND_CLOSING)
        assert [e.directive_id for e in start] == ["c"]
        assert start[0].fires_at == minutes(60).astimezone(TZ)
        assert start[0].as_entry() == {
            "enable": 2,
            "week": 64,  # Monday
            "hour": 11,
            "min": 0,
            "mode": 3,  # energy_saver, the baseline mode
            "param": 120,  # 140 degF
        }
        assert closing[0].as_entry()["param"] == BASELINE_SETPOINT
        assert closing[0].fires_at.hour == 14
        revert = entries_of(engine, KIND_DAILY_REVERT)
        assert len(revert) == 1
        assert revert[0].week_field == ALL_DAYS
        assert revert[0].as_entry()["hour"] == 3
        assert wanted.schedule is not None
        assert wanted.schedule["reservation_use"] == 2
        assert len(wanted.schedule["reservation"]) == 3
        assert wanted.schedule_hash == schedule_hash(wanted.schedule)

    def test_a_charge_already_under_way_is_a_direct_write(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-10, end=180, target_f=140)]
        )
        assert engine.wanted.setpoint_raw == 120
        assert entries_of(engine, KIND_START) == []
        assert len(entries_of(engine, KIND_CLOSING)) == 1

    def test_the_wanted_state_follows_the_windows(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        engine.evaluate(minutes(61), obs())
        assert engine.wanted.setpoint_raw == 120
        # The start entry fired and is gone; the closing entry remains.
        assert entries_of(engine, KIND_START) == []
        assert len(entries_of(engine, KIND_CLOSING)) == 1

        engine.evaluate(minutes(241), obs())
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert entries_of(engine, KIND_CLOSING) == []
        assert engine.last_restore is not None
        assert engine.last_restore.reason == RESTORE_EXPIRY
        assert engine.last_restore.matches_baseline is True

    def test_a_mode_directive_is_direct_with_the_daily_revert_as_closing(
        self,
    ):
        heat_pump_baseline = Baseline(
            mode="heat_pump",
            setpoint_raw=BASELINE_SETPOINT,
            tou_enabled=True,
            reservations_enabled=False,
        )
        engine = engine_with(
            [
                directive(
                    NOW, "mode", "m", start=-5, end=120, mode="energy_saver"
                )
            ],
            observed=obs(mode="heat_pump"),
            baseline=heat_pump_baseline,
        )
        assert engine.wanted.mode == "energy_saver"
        assert entries_of(engine, KIND_START) == []
        assert entries_of(engine, KIND_CLOSING) == []
        revert = entries_of(engine, KIND_DAILY_REVERT)
        assert revert[0].as_entry()["mode"] == 1  # heat_pump, the baseline
        engine.evaluate(minutes(121), obs(mode="heat_pump"))
        assert engine.wanted.mode == "heat_pump"

    def test_tou_lever_for_a_mode(self):
        engine = engine_with(
            [
                directive(
                    NOW, "mode", "m", start=-5, end=120, mode="energy_saver"
                )
            ],
            **{CONF_CONTROL_TOU_OFF_FOR_MODE: True},
        )
        assert engine.wanted.tou_on is False
        engine.evaluate(minutes(121), obs())
        assert engine.wanted.tou_on is True

    def test_without_the_lever_tou_stays_on(self):
        engine = engine_with(
            [
                directive(
                    NOW, "mode", "m", start=-5, end=120, mode="energy_saver"
                )
            ]
        )
        assert engine.wanted.tou_on is True

    def test_nothing_in_force_means_no_daily_revert_entry(self):
        engine = engine_with([])
        assert engine.wanted.entries == ()
        assert engine.wanted.schedule["reservation_use"] == 1

    def test_baseline_reservations_are_preserved_and_enabled(self):
        base = Baseline(
            mode="energy_saver",
            setpoint_raw=BASELINE_SETPOINT,
            tou_enabled=True,
            reservations_enabled=False,
            reservations=(
                {
                    "enable": 2,
                    "week": 4,
                    "hour": 6,
                    "min": 0,
                    "mode": 3,
                    "param": 110,
                },
            ),
        )
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)],
            baseline=base,
        )
        schedule = engine.wanted.schedule
        assert schedule["reservation_use"] == 2
        assert schedule["reservation"][0]["param"] == 110
        assert len(schedule["reservation"]) == 4


class TestHoldOff:
    """Section 5.6."""

    def test_setpoint_is_fixed_from_the_tank_at_start(self):
        engine = engine_with(
            [directive(NOW, "hold_off", "h", start=0, end=120)],
            observed=obs(upper_tank_raw=118),
            **HOLD_OFF,
        )
        # 2 degF is 2.2 half-degrees, so at least 3 below the tank.
        assert engine.wanted.setpoint_raw == 115
        engine.evaluate(minutes(30), obs(upper_tank_raw=125))
        assert engine.wanted.setpoint_raw == 115, "fixed at start, not tracking"
        assert entries_of(engine, KIND_START) == []
        assert entries_of(engine, KIND_CLOSING)[0].as_entry()["param"] == (
            BASELINE_SETPOINT
        )

    def test_waits_for_the_tank_reading(self):
        engine = engine_with(
            [directive(NOW, "hold_off", "h", start=0, end=120)],
            observed=obs(upper_tank_raw=None),
            **HOLD_OFF,
        )
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        engine.evaluate(minutes(1), obs(upper_tank_raw=118))
        assert engine.wanted.setpoint_raw == 115

    def test_floor_prevents_hold_off(self):
        engine = engine_with(
            [directive(NOW, "hold_off", "h", start=0, end=120)],
            observed=obs(upper_tank_raw=100),
            **{**HOLD_OFF, CONF_CONTROL_SETPOINT_MIN_F: 120},
        )
        run = engine.runs["h"]
        assert run.status == "rejected"
        assert run.reason == REASON_FLOOR_PREVENTS_HOLD_OFF
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert entries_of(engine, KIND_CLOSING) == []
        ack = engine.ack
        assert ack.state == "rejected"
        assert ack.directives[0].reason == REASON_FLOOR_PREVENTS_HOLD_OFF

    def test_unsupported_without_the_option(self):
        engine = engine_with(
            [directive(NOW, "hold_off", "h", start=0, end=120)]
        )
        assert engine.runs["h"].reason == "type_unsupported"
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT


class TestEntryBudget:
    """Section 5.3."""

    def test_directives_beyond_the_budget_are_rejected_in_start_order(self):
        # limit 4, reserve 2: two start entries fit, the third does not.
        engine = engine_with(
            [
                directive(NOW, "charge", "a", start=60, end=200, target_f=130),
                directive(NOW, "charge", "b", start=300, end=500, target_f=130),
                directive(NOW, "charge", "c", start=600, end=800, target_f=130),
            ],
            **{
                CONF_CONTROL_RESERVATION_ENTRY_LIMIT: 4,
                CONF_CONTROL_RESERVATION_ENTRY_RESERVE: 2,
            },
        )
        assert engine.runs["a"].status == "shadow"
        assert engine.runs["b"].status == "rejected"
        assert engine.runs["b"].reason == REASON_ENTRY_BUDGET
        assert engine.runs["c"].reason == REASON_ENTRY_BUDGET
        assert engine.ack.state == "partly_applied"

    def test_closing_entries_come_out_of_the_reserve(self):
        # limit 7, reserve 2: three charges need three starts (<= 5) and
        # three closings plus the daily revert (7 in all), which fits.
        engine = engine_with(
            [
                directive(NOW, "charge", "a", start=60, end=200, target_f=130),
                directive(NOW, "charge", "b", start=300, end=500, target_f=130),
                directive(NOW, "charge", "c", start=600, end=800, target_f=130),
            ]
        )
        assert all(run.status == "shadow" for run in engine.runs.values())
        assert len(engine.wanted.entries) == 7

    def test_the_whole_limit_is_respected(self):
        # limit 5, reserve 1: starts alone would fit, but not with closings.
        engine = engine_with(
            [
                directive(NOW, "charge", "a", start=60, end=200, target_f=130),
                directive(NOW, "charge", "b", start=300, end=500, target_f=130),
                directive(NOW, "charge", "c", start=600, end=800, target_f=130),
            ],
            **{
                CONF_CONTROL_RESERVATION_ENTRY_LIMIT: 5,
                CONF_CONTROL_RESERVATION_ENTRY_RESERVE: 1,
            },
        )
        assert engine.runs["a"].status == "shadow"
        assert engine.runs["b"].status == "shadow"
        assert engine.runs["c"].reason == REASON_ENTRY_BUDGET

    def test_fired_entries_are_deleted(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        assert len(engine.owned) == 2
        engine.evaluate(minutes(61), obs())
        assert [e.kind for e in engine.owned] == [KIND_CLOSING]
        engine.evaluate(minutes(241), obs())
        assert engine.owned == []

    def test_one_entry_per_slot(self):
        engine = engine_with(
            [
                directive(NOW, "hold_off", "h", start=0, end=60),
                directive(NOW, "charge", "c", start=60, end=240, target_f=140),
            ],
            observed=obs(upper_tank_raw=118),
            **HOLD_OFF,
        )
        at_60 = [
            e
            for e in engine.wanted.entries
            if e.fires_at == minutes(60).astimezone(TZ)
        ]
        assert len(at_60) == 1
        assert at_60[0].kind == KIND_START
        assert len(engine.wanted.schedule["reservation"]) == 3


class TestCharge:
    def test_completion_is_the_compressor_stopping(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        assert engine.wanted.setpoint_raw == 120
        engine.evaluate(minutes(1), obs(compressor_on=True))
        engine.evaluate(minutes(50), obs(compressor_on=True))
        engine.evaluate(minutes(90), obs(compressor_on=False))
        assert engine.runs["c"].complete is True
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert engine.ack.directives[0].status == "shadow"

    def test_a_cycle_that_never_ran_does_not_complete(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        engine.evaluate(minutes(90), obs(compressor_on=False))
        assert engine.runs["c"].complete is False
        assert engine.wanted.setpoint_raw == 120


class TestRestore:
    """Section 5.4: every reason, and the wait for the compressor."""

    def test_intent_ended_when_a_directive_is_dropped(self):
        engine = engine_with(
            [
                directive(NOW, "charge", "c", start=-5, end=180, target_f=140),
                directive(
                    NOW, "charge", "later", start=300, end=500, target_f=130
                ),
            ]
        )
        later_entries = [e for e in engine.owned if e.directive_id == "later"]
        give(
            engine,
            [
                directive(
                    NOW, "charge", "later", start=300, end=500, target_f=130
                )
            ],
            now=minutes(1),
            intent_id="i-2",
        )
        assert engine.last_restore.reason == RESTORE_INTENT_ENDED
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        # The kept directive kept its entries.
        assert [
            e for e in engine.owned if e.directive_id == "later"
        ] == later_entries
        assert all(e.directive_id != "c" for e in engine.owned)

    def test_a_changed_directive_is_a_new_one(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        give(
            engine,
            [directive(NOW, "charge", "c", start=60, end=240, target_f=130)],
            now=minutes(1),
            intent_id="i-2",
        )
        assert entries_of(engine, KIND_START)[0].param == 109  # 130 degF

    def test_an_empty_intent_ends_the_state(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        give(engine, [], now=minutes(1), intent_id="i-2")
        assert engine.last_restore.reason == RESTORE_INTENT_ENDED
        assert engine.wanted.entries == ()

    def test_stale_intent(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        engine.clear_intent(RESTORE_STALE_INTENT, minutes(30), obs())
        assert engine.last_restore.reason == RESTORE_STALE_INTENT
        assert engine.owned == []
        assert engine.intent is None
        assert engine.ack.state == "none"

    def test_startup_with_nothing(self):
        engine = engine_with()
        engine.clear_intent(RESTORE_STARTUP, NOW, obs())
        assert engine.last_restore.reason == RESTORE_STARTUP
        assert engine.last_restore.matches_baseline is True

    def test_matches_baseline_is_false_when_the_device_differs(self):
        engine = engine_with(observed=obs(setpoint_raw=100))
        engine.clear_intent(RESTORE_STARTUP, NOW, obs(setpoint_raw=100))
        assert engine.last_restore.matches_baseline is False

    def test_disabled_is_unconditional(self):
        running = obs(compressor_on=True, upper_tank_raw=121)
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)],
            observed=running,
        )
        assert engine.wanted.setpoint_raw == 120
        engine.clear_intent(
            RESTORE_DISABLED, minutes(1), running, unconditional=True
        )
        assert engine.last_restore.reason == RESTORE_DISABLED
        assert engine.wanted.restore_pending is None
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert engine.wanted.holds == ()

    def test_daily_revert_displaces_the_intent_briefly(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=24 * 60, target_f=140)]
        )
        revert = engine.next_daily_revert(NOW)
        assert revert.hour == 3
        engine.evaluate(revert + timedelta(seconds=30), obs())
        assert engine.last_restore.reason == RESTORE_DAILY_REVERT
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert "daily_revert" in engine.wanted.holds
        engine.evaluate(revert + timedelta(minutes=6), obs())
        assert engine.wanted.setpoint_raw == 120
        assert engine.wanted.holds == ()

    def test_a_restore_waits_for_the_compressor(self):
        running = obs(compressor_on=True, upper_tank_raw=121)
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        engine.evaluate(minutes(100), running)  # the cycle starts
        # The window ends while the compressor has run 81 minutes: the
        # baseline setpoint (119) is below the tank (121), so writing it
        # would stop the compressor before its 120 minutes.
        engine.evaluate(minutes(181), running)
        assert engine.wanted.setpoint_raw == 120
        assert HOLD_COMPRESSOR_MIN_RUN in engine.wanted.holds
        assert engine.wanted.restore_pending == RESTORE_EXPIRY
        assert engine.last_restore is None

        engine.evaluate(minutes(221), running)
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert engine.last_restore.reason == RESTORE_EXPIRY
        assert engine.wanted.restore_pending is None

    def test_a_restore_proceeds_once_the_compressor_stops(self):
        running = obs(compressor_on=True, upper_tank_raw=121)
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        engine.evaluate(minutes(100), running)
        engine.evaluate(minutes(181), running)
        assert engine.wanted.restore_pending == RESTORE_EXPIRY
        engine.evaluate(
            minutes(190), obs(compressor_on=False, upper_tank_raw=121)
        )
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert engine.last_restore.reason == RESTORE_EXPIRY

    def test_a_restore_that_would_not_stop_the_compressor_is_immediate(self):
        # The tank is below the baseline setpoint, so restoring keeps the
        # compressor going.
        running = obs(compressor_on=True, upper_tank_raw=110)
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        engine.evaluate(minutes(100), running)
        engine.evaluate(minutes(181), running)
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert engine.last_restore.reason == RESTORE_EXPIRY

    def test_a_request_cycle_runs_to_completion(self):
        running = obs(compressor_on=True, upper_tank_raw=121)
        engine = engine_with(
            [
                directive(
                    NOW,
                    "mode",
                    "m",
                    start=-5,
                    end=30,
                    mode="energy_saver",
                    request=True,
                ),
                directive(NOW, "charge", "c", start=-5, end=180, target_f=140),
            ],
            observed=running,
            **{CONF_CONTROL_MIN_RUN_BEFORE_STOP_MIN: 10},
        )
        engine.evaluate(minutes(181), running)
        assert HOLD_REQUEST_CYCLE in engine.wanted.holds
        assert engine.wanted.setpoint_raw == 120
        engine.evaluate(minutes(400), running)
        assert HOLD_REQUEST_CYCLE in engine.wanted.holds, (
            "min_run does not release it"
        )
        engine.evaluate(
            minutes(401), obs(compressor_on=False, upper_tank_raw=121)
        )
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT

    def test_no_reversal_within_a_cycle(self):
        running = obs(compressor_on=True, upper_tank_raw=121)
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)],
            observed=running,
            **{CONF_CONTROL_MIN_RUN_BEFORE_STOP_MIN: 10},
        )
        engine.evaluate(minutes(181), running)
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT, "lowered"
        give(
            engine,
            [
                directive(
                    NOW, "charge", "again", start=-5, end=400, target_f=140
                )
            ],
            now=minutes(182),
            observed=running,
            intent_id="i-2",
        )
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert HOLD_NO_REVERSAL in engine.wanted.holds
        engine.evaluate(
            minutes(183), obs(compressor_on=False, upper_tank_raw=121)
        )
        assert engine.wanted.setpoint_raw == 120


class TestOverrides:
    """Section 5.7."""

    def test_setpoint_change_is_an_override_that_is_honoured(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=180, target_f=140)]
        )
        engine.evaluate(minutes(1), obs(setpoint_raw=100))
        override = engine.overrides["setpoint"]
        assert override.value == 100
        assert override.detected_at == minutes(1)
        assert override.expires_at == engine.next_daily_revert(minutes(1))
        assert engine.wanted.setpoint_raw == 100, (
            "not reverted, even for the charge"
        )
        assert len(entries_of(engine, KIND_DAILY_REVERT)) == 1

    @pytest.mark.parametrize(
        ("change", "field", "value"),
        [
            ({"mode": "electric"}, "mode", "electric"),
            ({"tou_on": False}, "tou", False),
        ],
    )
    def test_other_fields(self, change, field, value):
        engine = engine_with([])
        engine.evaluate(minutes(1), obs(**change))
        assert engine.overrides[field].value == value
        wanted = engine.wanted
        assert {"mode": wanted.mode, "tou": wanted.tou_on}[field] == value

    def test_reservation_list_change(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        edited = (
            {
                "enable": 2,
                "week": 2,
                "hour": 7,
                "min": 0,
                "mode": 1,
                "param": 100,
            },
        )
        engine.evaluate(
            minutes(1), obs(reservations=edited, reservations_enabled=True)
        )
        assert "reservations" in engine.overrides
        assert engine.wanted.schedule["reservation"] == list(edited)
        assert engine.wanted.entries == ()

    def test_expires_when_the_person_reverts_it(self):
        engine = engine_with([])
        engine.evaluate(minutes(1), obs(setpoint_raw=100))
        engine.evaluate(minutes(2), obs())
        assert engine.overrides == {}
        assert engine.last_restore.reason == RESTORE_OVERRIDE_EXPIRED

    def test_expires_at_the_daily_revert(self):
        engine = engine_with([])
        engine.evaluate(minutes(1), obs(setpoint_raw=100))
        revert = engine.next_daily_revert(NOW)
        engine.evaluate(revert + timedelta(seconds=30), obs(setpoint_raw=100))
        assert engine.overrides == {}
        assert engine.last_restore.reason == RESTORE_DAILY_REVERT
        # After the revert the observed value is the new expectation.
        engine.evaluate(revert + timedelta(minutes=6), obs(setpoint_raw=100))
        assert engine.overrides == {}

    def test_vacation_is_precedence_not_an_override(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        assert engine.owned
        engine.evaluate(minutes(1), obs(mode="vacation"))
        assert engine.overrides == {}
        assert engine.wanted.suspended_by == "vacation"
        assert engine.owned == [], "pending entries withdrawn"
        assert engine.wanted.schedule["reservation"] == []
        engine.evaluate(minutes(2), obs(mode="power_off"))
        assert engine.wanted.suspended_by == "power_off"

    def test_anti_legionella_suspends(self):
        engine = engine_with([])
        engine.evaluate(minutes(1), obs(anti_legionella_busy=True))
        assert engine.wanted.suspended_by == "anti_legionella"
        engine.evaluate(minutes(2), obs())
        assert engine.wanted.suspended_by is None

    def test_a_baseline_entry_firing_is_not_an_override(self):
        base = Baseline(
            mode="energy_saver",
            setpoint_raw=BASELINE_SETPOINT,
            tou_enabled=True,
            reservations_enabled=True,
            # Monday at 10:30 local: energy_saver, 110 half-degrees.
            reservations=(
                {
                    "enable": 2,
                    "week": 64,
                    "hour": 10,
                    "min": 30,
                    "mode": 3,
                    "param": 110,
                },
            ),
        )
        engine = engine_with([], baseline=base)
        engine.evaluate(minutes(31), obs(setpoint_raw=110))
        assert engine.overrides == {}
        assert engine.expected["setpoint"] == 110

    def test_a_change_at_another_time_is_an_override(self):
        base = Baseline(
            mode="energy_saver",
            setpoint_raw=BASELINE_SETPOINT,
            tou_enabled=True,
            reservations_enabled=True,
            reservations=(
                {
                    "enable": 2,
                    "week": 64,
                    "hour": 10,
                    "min": 30,
                    "mode": 3,
                    "param": 110,
                },
            ),
        )
        engine = engine_with([], baseline=base)
        engine.evaluate(minutes(50), obs(setpoint_raw=110))
        assert "setpoint" in engine.overrides


class TestSurplusGrant:
    """Section 5.8."""

    @staticmethod
    def _grant(**options):
        running = obs(compressor_on=True, mode="heat_pump", surplus_on=True)
        engine = engine_with(
            [
                directive(
                    NOW, "surplus_grant", "g", start=-5, end=300, max_f=146
                )
            ],
            observed=running,
            **{**SURPLUS, **options},
        )
        return engine, running

    def test_raises_once_after_ten_minutes_of_surplus(self):
        engine, running = self._grant()
        engine.evaluate(minutes(9), running)
        assert engine.wanted.surplus_raised is False
        engine.evaluate(minutes(10), running)
        assert engine.wanted.surplus_raised is True
        assert engine.wanted.setpoint_raw == 127  # 146 degF

    def test_never_raises_to_start_a_cycle(self):
        engine, _ = self._grant()
        idle = obs(compressor_on=False, mode="heat_pump", surplus_on=True)
        engine.evaluate(minutes(10), idle)
        assert engine.wanted.surplus_raised is False

    def test_only_in_heat_pump_mode(self):
        engine, _ = self._grant()
        saver = obs(compressor_on=True, mode="energy_saver", surplus_on=True)
        engine.evaluate(minutes(10), saver)
        assert engine.wanted.surplus_raised is False

    def test_lowered_when_the_compressor_stops(self):
        engine, running = self._grant()
        engine.evaluate(minutes(10), running)
        engine.evaluate(
            minutes(20),
            obs(compressor_on=False, mode="heat_pump", surplus_on=True),
        )
        assert engine.wanted.surplus_raised is False
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT

    def test_one_raise_per_cycle(self):
        engine, running = self._grant(
            **{CONF_CONTROL_MIN_RUN_BEFORE_STOP_MIN: 5}
        )
        engine.evaluate(minutes(10), running)
        assert engine.wanted.surplus_raised
        off = obs(compressor_on=True, mode="heat_pump", surplus_on=False)
        engine.evaluate(minutes(11), off)
        engine.evaluate(minutes(26), off)
        assert engine.wanted.surplus_raised is False, "off 15 min after min run"
        engine.evaluate(minutes(27), running)
        engine.evaluate(minutes(40), running)
        assert engine.wanted.surplus_raised is False, "not twice in a cycle"

    def test_not_lowered_before_min_run(self):
        engine, running = self._grant()
        engine.evaluate(minutes(10), running)
        off = obs(compressor_on=True, mode="heat_pump", surplus_on=False)
        engine.evaluate(minutes(30), off)
        assert engine.wanted.surplus_raised is True, "min run is 120 min"

    def test_lowered_when_a_mode_starts(self):
        engine, running = self._grant()
        engine.evaluate(minutes(10), running)
        give(
            engine,
            [
                directive(
                    NOW, "surplus_grant", "g", start=-5, end=300, max_f=146
                ),
                directive(
                    NOW, "mode", "m", start=400, end=500, mode="energy_saver"
                ),
            ],
            now=minutes(11),
            observed=running,
            intent_id="i-2",
        )
        assert engine.wanted.surplus_raised is True, (
            "the mode is not in force yet"
        )
        engine.evaluate(minutes(401), running)
        assert engine.wanted.surplus_raised is False


class TestPersistence:
    def test_round_trip(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        engine.evaluate(minutes(1), obs(setpoint_raw=100))
        engine.clear_intent(
            RESTORE_STALE_INTENT, minutes(2), obs(setpoint_raw=100)
        )
        document = engine.as_document()

        again = ControlEngine(engine.capabilities, TZ)
        again.load_document(document)

        assert again.overrides == engine.overrides
        assert again.expected == engine.expected
        assert again.last_restore == engine.last_restore
        assert again.owned == engine.owned

    def test_owned_entries_survive(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        again = ControlEngine(engine.capabilities, TZ)
        again.load_document(engine.as_document())
        assert [e.as_entry() for e in again.owned] == [
            e.as_entry() for e in engine.owned
        ]

    def test_load_tolerates_an_empty_document(self):
        engine = ControlEngine(capabilities(), TZ)
        engine.load_document({})
        assert engine.owned == []


class TestTiming:
    def test_next_event_is_the_earliest_boundary(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)]
        )
        assert engine.next_event_at == minutes(60)
        engine.evaluate(minutes(61), obs())
        assert engine.next_event_at == minutes(240)

    def test_next_event_without_an_intent_is_the_daily_revert(self):
        engine = engine_with()
        assert engine.next_event_at == engine.next_daily_revert(NOW)

    def test_no_baseline_means_no_plan(self):
        engine = engine_with(
            [directive(NOW, "charge", "c", start=-5, end=240, target_f=140)],
            baseline=None,
        )
        assert engine.wanted.setpoint_raw is None
        assert engine.ack.state == "shadow"
        assert engine.owned == []

    def test_a_late_baseline_builds_the_entries(self):
        """The device may report after the intent arrived."""
        engine = engine_with(
            [directive(NOW, "charge", "c", start=60, end=240, target_f=140)],
            baseline=None,
        )
        engine.set_baseline(BASELINE, minutes(1), obs())
        assert engine.wanted.setpoint_raw == BASELINE_SETPOINT
        assert [e.kind for e in engine.owned] == [KIND_START, KIND_CLOSING]
        # Setting it again does not duplicate anything.
        engine.set_baseline(BASELINE, minutes(2), obs())
        assert len(engine.owned) == 2

    def test_ack_details(self):
        engine = engine_with(
            [
                directive(NOW, "charge", "c", start=-5, end=180, target_f=140),
                directive(NOW, "hold_off", "h", start=200, end=400),
            ],
            observed=obs(upper_tank_raw=118),
            **HOLD_OFF,
        )
        acks = {a.id: a.as_attribute() for a in engine.ack.directives}
        assert acks["c"]["complete"] is False
        assert acks["h"]["hold_setpoint_raw"] is None
        engine.evaluate(minutes(201), obs(upper_tank_raw=118))
        acks = {a.id: a.as_attribute() for a in engine.ack.directives}
        assert acks["h"]["hold_setpoint_raw"] == 115
