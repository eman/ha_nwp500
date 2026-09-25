"""Regressions for the adversarial review of PR #162.

Each test is a scenario the review showed going wrong: going live from
shadow, leaving live and coming back, writes that landed unconfirmed,
restarts and flapping sources, outages, lost writes, and surplus raises.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.util import dt as dt_util

from custom_components.nwp500.const import (
    CONF_CONTROL_LIVE_SEGMENTS,
    CONF_CONTROL_MODE,
    CONF_CONTROL_OWNER_PROGRAM,
)
from custom_components.nwp500.control.device import WRITE_PAUSE, WRITE_RETRY
from custom_components.nwp500.control.engine import Planner
from custom_components.nwp500.control.entries import (
    KIND_GRANT_LOWER,
    KIND_GRANT_RAISE,
    KIND_GUARD,
    KIND_NEAR_TERM,
)
from custom_components.nwp500.control.intent import (
    IntentRejected,
    parse_plan,
)

from . import test_live
from .conftest import grant, make_document, segment
from .test_device import INTENT_ENTITY, MAC, _publish
from .test_engine import (
    NOW,
    SURPLUS,
    TZ,
    TestSurplusGrants,
    give,
    minutes,
    obs,
    planner_with,
    run,
)
from .test_live import (
    LIVE,
    OWNER_ENTRY,
    OWNER_LIST,
    _tick,
    _two_segments,
    device_obs,
    statuses,
)

# The live fixtures, shared with the live-mode tests.
live_gate = test_live.live_gate
live_factory = test_live.live_factory

OWNER_OFF_LIST = {"reservation_use": 1, "reservation": [OWNER_ENTRY]}
OWNER_OFF_DOC = {
    "mode": "energy_saver",
    "setpoint_raw": 119,
    "reservations_enabled": False,
    "entries": [OWNER_ENTRY],
}


async def _leave_live(hass, control, **options) -> None:
    from custom_components.nwp500.control import ControlFeature

    feature = ControlFeature(hass, control.entry, control.coordinator)
    feature.devices[MAC] = control
    hass.config_entries.async_update_entry(
        control.entry, options={**control.entry.options, **options}
    )
    await feature.async_options_changed()


class TestGoingLive:
    @pytest.mark.asyncio
    async def test_from_shadow_programs_the_plan(self, hass, live_factory, now):
        _publish(hass, _two_segments(now))
        heater, shadow = await live_factory(**{CONF_CONTROL_MODE: "shadow"})
        assert shadow.planner.owned  # simulated
        await shadow.async_stop()

        _, live = await live_factory(reuse=True)

        assert live.planner.removed_segments == set()
        assert {e.serves for e in live.planner.owned} >= {"now", "later"}
        assert len(heater.schedule["reservation"]) == 3
        assert statuses(live.ack)["later"] == ("programmed", None)

    @pytest.mark.asyncio
    async def test_live_shadow_live_takes_the_list_over_again(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        declared = {CONF_CONTROL_OWNER_PROGRAM: {MAC: OWNER_OFF_DOC}}
        heater, control = await live_factory(
            schedule=OWNER_OFF_LIST, **declared
        )
        assert heater.schedule["reservation_use"] == 2
        await _leave_live(hass, control, **{CONF_CONTROL_MODE: "shadow"})
        assert heater.schedule == OWNER_OFF_LIST
        await control.async_stop()

        _, shadow = await live_factory(
            reuse=True, **{CONF_CONTROL_MODE: "shadow"}, **declared
        )
        assert shadow.holds_device is False
        await shadow.async_stop()
        _, live = await live_factory(reuse=True, **declared)

        assert heater.schedule["reservation_use"] == 2
        assert _entries_off(heater.schedule)
        assert statuses(live.ack)["later"] == ("programmed", None)

    @pytest.mark.asyncio
    async def test_segments_off_then_on(self, hass, live_factory, now):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory()
        await _leave_live(hass, control, **{CONF_CONTROL_LIVE_SEGMENTS: False})
        assert heater.schedule == OWNER_LIST
        await control.async_stop()
        _, again = await live_factory(reuse=True)
        assert statuses(again.ack)["later"] == ("programmed", None)


def _entries_off(schedule) -> bool:
    return all(
        e["enable"] == 1
        for e in schedule["reservation"]
        if (e["hour"], e["min"]) == (OWNER_ENTRY["hour"], OWNER_ENTRY["min"])
    )


class TestHandBack:
    @pytest.mark.asyncio
    async def test_leaving_live_is_saved(self, hass, live_factory, now):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory()
        await _leave_live(hass, control, **{CONF_CONTROL_MODE: "shadow"})
        assert heater.schedule == OWNER_LIST
        await control.async_stop()
        states = list(heater.states)

        _, shadow = await live_factory(
            reuse=True, **{CONF_CONTROL_MODE: "shadow"}
        )

        assert shadow.holds_device is False
        assert shadow.store.stored_engine(MAC)["took_over"] is False
        assert heater.states == states

    @pytest.mark.asyncio
    async def test_the_disable_button_retries_a_failed_hand_back(
        self, hass, live_factory, now, freezer
    ):
        from custom_components.nwp500.control.button import ControlDisableButton

        _publish(hass, _two_segments(now))
        heater, control = await live_factory()
        await control.async_stop()
        heater.lose = 2
        _, disabled = await live_factory(
            reuse=True, **{CONF_CONTROL_MODE: "disabled"}
        )
        await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))
        assert disabled.store.disabled_done(MAC) is False

        button = ControlDisableButton(disabled, "disable")
        button.hass = hass
        await button.async_press()

        assert heater.schedule == OWNER_LIST
        assert disabled.store.disabled_done(MAC) is True


class TestUnconfirmedWrites:
    @pytest.mark.asyncio
    async def test_a_landed_write_is_not_forgotten_when_a_read_fails(
        self, hass, live_factory, now, freezer
    ):
        heater, control = await live_factory()
        heater.land_unconfirmed = 1
        _publish(hass, _two_segments(now))
        await hass.async_block_till_done()
        control.coordinator.reservation_schedules[MAC] = copy.deepcopy(
            OWNER_LIST
        )
        read = heater.async_read
        fails = {"n": 1}

        async def flaky_read():
            if fails["n"]:
                fails["n"] -= 1
                return None
            return await read()

        heater.async_read = flaky_read
        await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))
        assert len(control.planner.unconfirmed) == 1
        await _tick(hass, freezer, WRITE_PAUSE + timedelta(seconds=1))

        assert await control.async_release(dt_util.utcnow()) is True
        assert heater.schedule == OWNER_LIST


class TestPeoplesChanges:
    @pytest.mark.asyncio
    async def test_a_restart_does_not_restore_a_removed_entry(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory()
        later = next(e for e in control.planner.owned if e.serves == "later")
        heater.person_sets(
            {
                **heater.schedule,
                "reservation": [
                    e
                    for e in heater.schedule["reservation"]
                    if e != later.as_entry()
                ],
            }
        )
        await control._async_evaluate(dt_util.utcnow())
        await control.async_stop()

        _, again = await live_factory(reuse=True)

        assert later.as_entry() not in heater.schedule["reservation"]
        assert statuses(again.ack)["later"] == ("removed", None)

    @pytest.mark.asyncio
    async def test_the_same_plan_again_is_not_adopted_again(
        self, hass, live_factory, now
    ):
        doc = _two_segments(now)
        _publish(hass, doc)
        heater, control = await live_factory()
        later = next(e for e in control.planner.owned if e.serves == "later")
        heater.person_sets(
            {
                **heater.schedule,
                "reservation": [
                    e
                    for e in heater.schedule["reservation"]
                    if e != later.as_entry()
                ],
            }
        )
        await control._async_evaluate(dt_util.utcnow())
        writes = len(heater.writes)

        hass.states.async_set(INTENT_ENTITY, STATE_UNAVAILABLE, {})
        await hass.async_block_till_done()
        _publish(hass, doc)
        await hass.async_block_till_done()

        assert len(heater.writes) == writes
        assert statuses(control.ack)["later"] == ("removed", None)

    def test_an_entry_fired_during_an_outage_is_not_a_person(self):
        doc = make_document(
            NOW,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_c=59.5),
                segment(NOW, "b", 60, mode="energy_saver", setpoint_f=130),
            ],
        )
        planner = planner_with(shadow=False, **LIVE)
        plan = parse_plan(doc)
        seen = device_obs(planner, mode="energy_saver", setpoint_raw=119)
        planner.set_plan(plan, NOW, seen)
        planner.commit(planner.step(NOW, seen))
        planner.step(minutes(1), device_obs(planner))
        restarted = Planner(planner.capabilities, TZ, shadow=False)
        restarted.owner = planner.owner
        restarted.load_document(planner.as_document())
        # Home Assistant is down from minute 2 to 120; "b" fires at 60.
        restarted.set_plan(
            plan, minutes(120), device_obs(planner), restoring=True
        )
        b = next(e for e in planner.owned if e.serves == "b")
        restarted.step(
            minutes(120),
            device_obs(
                restarted, mode="energy_saver", setpoint_raw=b.setpoint_raw
            ),
        )
        assert restarted.reports == {}


class TestAck:
    @pytest.mark.asyncio
    async def test_a_lost_write_is_not_programmed(
        self, hass, live_factory, now
    ):
        heater, control = await live_factory()
        heater.lose = 1
        _publish(
            hass,
            make_document(
                now, [segment(now, "now", -5, mode="heat_pump", setpoint_f=140)]
            ),
        )
        await hass.async_block_till_done()
        assert control.ack.state == "pending"
        assert statuses(control.ack)["now"] == ("pending", None)

    def test_a_near_term_confirmed_after_its_minute_is_issued_again(self):
        planner = planner_with(shadow=False, **LIVE)
        give_now = make_document(
            NOW, [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)]
        )
        planner.set_plan(parse_plan(give_now), NOW, device_obs(planner))
        write = planner.step(NOW, device_obs(planner))
        entry = next(e for e in write.added if e.kind == KIND_NEAR_TERM)
        late = entry.fires_at + timedelta(seconds=30)
        planner.commit(write, confirmed_at=late)
        pending = [e for e in planner.extra if e.kind == KIND_NEAR_TERM]
        assert len(pending) == 1
        assert pending[0].fires_at > late


class TestSurplusRaise:
    def test_a_restart_keeps_the_raise(self):
        t = TestSurplusGrants()
        planner = t._planner()
        run(planner, minutes(10), t.RUNNING)
        run(planner, minutes(20), t.RUNNING)
        assert planner.raise_state is not None
        restarted = Planner(planner.capabilities, TZ, shadow=True)
        restarted.owner = planner.owner
        restarted.load_document(planner.as_document())
        restarted.set_plan(planner.plan, minutes(25), t.RUNNING, restoring=True)
        assert restarted.raise_state is not None
        assert not any(e.kind == KIND_GRANT_LOWER for e in restarted.extra)

    def test_stopping_the_plan_lowers_a_raise(self):
        t = TestSurplusGrants()
        planner = t._planner()
        run(planner, minutes(10), t.RUNNING)
        run(planner, minutes(20), t.RUNNING)
        give(planner, [], now=minutes(21), observed=t.RUNNING, intent_id="stop")
        lowers = [e for e in planner.owned if e.kind == KIND_GRANT_LOWER]
        assert len(lowers) == 1
        assert lowers[0].setpoint_raw == 120


class TestDocuments:
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), 1e308])
    def test_a_setpoint_that_is_not_a_temperature(self, value):
        doc = make_document(NOW, [segment(NOW, "s", 0, mode="heat_pump")])
        doc["segments"][0]["setpoint_f"] = value
        with pytest.raises(IntentRejected) as err:
            parse_plan(doc)
        assert err.value.reason == "invalid_document"


class TestCopilotReview:
    """The Copilot review threads on #162."""

    def test_a_restart_keeps_the_raised_in_cycle_guard(self):
        t = TestSurplusGrants()
        planner = t._planner()
        run(planner, minutes(10), t.RUNNING)
        assert planner.raise_state is not None
        restarted = Planner(planner.capabilities, TZ, shadow=True)
        restarted.owner = planner.owner
        restarted.load_document(planner.as_document())
        assert restarted._raised_in_cycle is True
        assert restarted._cycle_started_at == planner._cycle_started_at

    def test_a_removed_segment_moved_to_a_new_start_is_programmed(self):
        planner = planner_with(shadow=False, **LIVE)
        give(
            planner,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_c=59.5),
                segment(NOW, "b", 60, mode="heat_pump", setpoint_f=140),
            ],
        )
        planner.removed_segments = {"b"}
        give(
            planner,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_c=59.5),
                segment(NOW, "b", 90, mode="heat_pump", setpoint_f=140),
            ],
            intent_id="i-2",
            observed=device_obs(planner),
        )
        assert "b" not in planner.removed_segments
        # Unmoved, it stays removed.
        planner.removed_segments = {"b"}
        give(
            planner,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_c=59.5),
                segment(NOW, "b", 90, mode="heat_pump", setpoint_f=140),
            ],
            intent_id="i-3",
            observed=device_obs(planner),
        )
        assert planner.removed_segments == {"b"}

    @pytest.mark.asyncio
    async def test_a_controller_that_fails_to_start_stops_the_others(
        self, hass, hass_storage
    ):
        from unittest.mock import AsyncMock, MagicMock, patch

        from pytest_homeassistant_custom_component.common import MockConfigEntry

        from custom_components.nwp500.const import DOMAIN
        from custom_components.nwp500.control import ControlFeature

        entry = MockConfigEntry(domain=DOMAIN, entry_id="e1", options={})
        entry.add_to_hass(hass)
        coordinator = MagicMock()
        coordinator.data = {
            "A": {"device": MagicMock()},
            "B": {"device": MagicMock()},
        }
        feature = ControlFeature(hass, entry, coordinator)
        started = AsyncMock(side_effect=[None, RuntimeError("boom")])
        stopped = AsyncMock()
        with (
            patch(
                "custom_components.nwp500.control.DeviceControl.async_start",
                started,
            ),
            patch(
                "custom_components.nwp500.control.DeviceControl.async_stop",
                stopped,
            ),
            pytest.raises(RuntimeError),
        ):
            await feature.async_start()
        assert stopped.await_count == 2
        assert feature.devices == {}


class TestIssueQuestions:
    """The questions on #158 from writing a scheduler against protocol 1."""

    def test_reassert_programs_a_segment_that_repeats_the_state(self):
        planner = planner_with(
            [
                segment(NOW, "day", -5, mode="heat_pump", setpoint_f=140),
                {
                    **segment(NOW, "night", 60, setpoint_f=140),
                    "reassert": True,
                },
                segment(NOW, "later", 120, setpoint_f=140),
            ]
        )
        details = {s.id: s.status for s in planner.ack("i").segments}
        assert details["night"] == "shadow"
        assert details["later"] == "merged"
        assert [e.serves for e in planner.owned if e.kind == "plan"] == [
            "night"
        ]

    def test_reassert_must_be_a_boolean(self):
        doc = make_document(
            NOW,
            [
                {
                    **segment(NOW, "s", 0, mode="heat_pump", setpoint_f=140),
                    "reassert": "yes",
                }
            ],
        )
        with pytest.raises(IntentRejected):
            parse_plan(doc)

    def test_reassert_is_echoed(self):
        doc = make_document(
            NOW,
            [
                {
                    **segment(NOW, "s", 0, mode="heat_pump", setpoint_f=140),
                    "reassert": True,
                }
            ],
        )
        assert parse_plan(doc).as_document()["segments"][0]["reassert"] is True

    def test_a_merged_segment_before_the_grant_ends_still_gets_a_guard(self):
        t = TestSurplusGrants()
        planner = t._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                # Same state: merged, so no entry of its own on the heater.
                segment(NOW, "same", 60, setpoint_f=140),
            ]
        )
        write = run(planner, minutes(10), t.RUNNING)
        assert write is not None
        guards = [e for e in write.added if e.kind == "guard"]
        assert len(guards) == 1
        assert guards[0].setpoint_raw == 120

    def test_a_merged_segment_mid_raise_keeps_the_raise_and_its_guard(self):
        t = TestSurplusGrants()
        planner = t._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "same", 60, setpoint_f=140),
            ]
        )
        run(planner, minutes(10), t.RUNNING)
        rs = planner.raise_state
        assert rs is not None and rs.guard is not None
        # The merged segment starts: nothing on the heater ends the raise,
        # so it is still tracked and the guard stays.
        assert run(planner, minutes(61), t.RUNNING) is None
        assert planner.raise_state is rs
        assert rs.guard in planner.extra

    def test_no_raise_when_a_segment_starts_inside_the_lead(self):
        """The raise entry would fire after the segment's and undo it."""
        t = TestSurplusGrants()
        planner = t._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "next", 11, mode="energy_saver", setpoint_f=130),
            ]
        )
        run(planner, minutes(10), t.RUNNING)
        assert planner.raise_state is None

    def test_a_programmed_segment_ends_the_raise_and_its_guard(self):
        t = TestSurplusGrants()
        planner = t._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "next", 60, setpoint_f=135),
            ]
        )
        write = run(planner, minutes(10), t.RUNNING)
        assert write is not None
        assert [e for e in write.added if e.kind == "guard"]
        run(planner, minutes(61), t.RUNNING)
        assert planner.raise_state is None
        assert not [e for e in planner.owned if e.kind == "guard"]


def _foreign(*minutes_past_1300: int) -> tuple[dict, ...]:
    """Enabled entries of someone else's on Monday, 13:00 plus the minutes."""
    return tuple(
        {"enable": 2, "week": 64, "hour": 13, "min": m, "mode": 3, "param": 100}
        for m in minutes_past_1300
    )


def _unended(planner: Planner, after) -> bool:
    """Raised with nothing on the heater, of the feature's, to end it."""
    return planner.raise_state is None and not any(
        e.fires_at > after and e.setpoint_raw != 127 for e in planner.owned
    )


class TestRaiseEndsOnlyOnTheHeater:
    """The adversarial review of #163: a raise ends when an entry does.

    A raise was taken as ended, and its guard withdrawn, whenever a segment
    started, although the segment may have put nothing on the heater.
    """

    RUNNING = TestSurplusGrants.RUNNING
    IDLE = obs(
        mode="heat_pump", setpoint_raw=127, compressor_on=False, surplus_on=True
    )

    def test_republishing_mid_raise_keeps_the_raise_and_its_guard(self):
        planner = TestSurplusGrants()._planner()
        run(planner, minutes(10), self.RUNNING)
        run(planner, minutes(13), self.RUNNING)
        # The same grant, with the first segment re-anchored at now in the
        # same state: no entry is written for it.
        give(
            planner,
            [segment(NOW, "s-new", 15, mode="heat_pump", setpoint_f=140)],
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            now=minutes(15),
            observed=self.RUNNING,
            intent_id="i-2",
        )
        run(planner, minutes(16), self.RUNNING)
        rs = planner.raise_state
        assert rs is not None
        assert rs.guard in planner.owned
        assert not _unended(planner, minutes(16))

    def test_republishing_moves_the_guard_to_the_new_plans_state(self):
        planner = TestSurplusGrants()._planner()
        run(planner, minutes(10), self.RUNNING)
        give(
            planner,
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                # Not before the grant's end: the guard restores this.
                segment(NOW, "later", 180, mode="heat_pump", setpoint_f=130),
            ],
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            now=minutes(11),
            observed=self.RUNNING,
            intent_id="i-2",
        )
        rs = planner.raise_state
        assert rs is not None and rs.guard is not None
        assert (rs.guard.mode, rs.guard.setpoint_raw) == (
            "heat_pump",
            planner.resolve(planner.plan.segments[1]).setpoint_raw,
        )

    def test_lowering_just_before_a_merged_segment_is_written(self):
        planner = TestSurplusGrants()._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "same", 60, setpoint_f=140),
            ]
        )
        run(planner, minutes(10), self.RUNNING)
        run(planner, minutes(13), self.RUNNING)
        # The compressor stops 90 s before the merged segment starts.
        run(planner, minutes(58.5), self.IDLE)
        assert planner.raise_state is None
        assert any(
            e.kind == KIND_GRANT_LOWER and e.setpoint_raw == 120
            for e in planner.owned
        )

    def test_lowering_just_before_a_change_waits_for_its_entry(self):
        planner = TestSurplusGrants()._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "next", 60, setpoint_f=135),
            ]
        )
        run(planner, minutes(10), self.RUNNING)
        run(planner, minutes(13), self.RUNNING)
        run(planner, minutes(58.5), self.IDLE)
        # The segment's entry lowers it; until it fires the guard stays.
        rs = planner.raise_state
        assert rs is not None and rs.guard in planner.owned
        run(planner, minutes(60.5), self.IDLE)
        assert planner.raise_state is None

    def test_a_new_plan_reusing_an_id_as_merged_still_gets_a_guard(self):
        planner = planner_with(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s1", 60, mode="energy_saver", setpoint_f=130),
            ],
            observed=self.RUNNING,
            **SURPLUS,
        )
        run(planner, minutes(5), self.RUNNING)
        give(
            planner,
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s1", 60, setpoint_f=140),
            ],
            grants=[grant(NOW, "g", -5, 180, max_f=146)],
            now=minutes(10),
            observed=self.RUNNING,
            intent_id="i-2",
        )
        rs = planner.raise_state
        assert rs is not None and rs.guard is not None
        assert rs.guard in planner.owned

    def test_a_small_budget_cannot_leave_the_raise_without_a_guard(self):
        planner = TestSurplusGrants()._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s1", 60, mode="energy_saver", setpoint_f=130),
            ],
            control_reservation_entry_limit=2,
            control_reservation_entry_reserve=0,
        )
        run(planner, minutes(10), self.RUNNING)
        rs = planner.raise_state
        assert rs is not None and rs.guard in planner.owned

    def test_a_segment_a_person_removed_does_not_end_the_raise(self):
        planner = TestSurplusGrants()._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s1", 60, mode="heat_pump", setpoint_f=135),
                segment(NOW, "s2", 240, mode="heat_pump", setpoint_f=140),
            ]
        )
        run(planner, minutes(10), self.RUNNING)
        run(planner, minutes(13), self.RUNNING)
        # A person removes s1's entry, as the live list tracking records it.
        entry = next(e for e in planner.owned if e.serves == "s1")
        planner.removed_segments.add("s1")
        planner.owned = [e for e in planner.owned if e != entry]
        run(planner, minutes(61), self.RUNNING)
        rs = planner.raise_state
        assert rs is not None and rs.guard in planner.owned

    def test_a_guard_moved_past_collisions_is_kept_until_it_fires(self):
        running = replace(self.RUNNING, reservations=_foreign(0, 1))
        planner = TestSurplusGrants()._planner()
        run(planner, minutes(10), running)
        (guard,) = [e for e in planner.owned if e.kind == KIND_GUARD]
        assert guard.fires_at.strftime("%H:%M") == "13:02"
        run(planner, minutes(181), running)
        assert guard in planner.owned
        assert planner.raise_state is not None
        run(planner, minutes(182), running)
        assert planner.raise_state is None

    def test_a_raise_moved_onto_a_merged_segment_is_kept(self):
        running = replace(
            self.RUNNING,
            reservations=(
                {
                    "enable": 2,
                    "week": 64,
                    "hour": 10,
                    "min": 12,
                    "mode": 3,
                    "param": 100,
                },
            ),
        )
        planner = TestSurplusGrants()._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "same", 13, setpoint_f=140),
            ]
        )
        run(planner, minutes(10), running)
        rs = planner.raise_state
        assert rs is not None
        assert rs.entry in planner.owned
        assert rs.entry.fires_at.strftime("%H:%M") == "10:13"

    def test_a_raise_moved_onto_a_change_is_withdrawn(self):
        running = replace(
            self.RUNNING,
            reservations=(
                {
                    "enable": 2,
                    "week": 64,
                    "hour": 10,
                    "min": 12,
                    "mode": 3,
                    "param": 100,
                },
            ),
        )
        planner = TestSurplusGrants()._planner(
            [
                segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140),
                segment(NOW, "next", 13, setpoint_f=135),
            ]
        )
        run(planner, minutes(10), running)
        assert planner.raise_state is None
        assert not [
            e for e in planner.owned if e.kind in (KIND_GRANT_RAISE, KIND_GUARD)
        ]
