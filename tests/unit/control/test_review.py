"""Regressions for the adversarial review of PR #162.

Each test is a scenario the review showed going wrong: going live from
shadow, leaving live and coming back, writes that landed unconfirmed,
restarts and flapping sources, outages, lost writes, and surplus raises.
"""

from __future__ import annotations

import copy
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
    KIND_NEAR_TERM,
)
from custom_components.nwp500.control.intent import (
    IntentRejected,
    parse_plan,
)

from . import test_live
from .conftest import make_document, segment
from .test_device import INTENT_ENTITY, MAC, _publish
from .test_engine import (
    NOW,
    TZ,
    TestSurplusGrants,
    give,
    minutes,
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
