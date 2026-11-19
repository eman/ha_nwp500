"""Regressions for the 2026-11 review of the external-control engine.

Each test is a sequence the review showed going wrong: a segment moved
inside the lead by a replacing plan, a landed write planned for an older
plan, a restart losing a refused segment's status, and a failure count
left over after a retry found its write had landed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.nwp500.control.device import WRITE_RETRY
from custom_components.nwp500.control.engine import Planner
from custom_components.nwp500.control.entries import (
    KIND_NEAR_TERM,
    schedule_hash,
)
from custom_components.nwp500.control.intent import parse_plan

from . import test_live
from .conftest import capabilities, make_document, segment
from .test_device import _publish
from .test_engine import NOW, TZ, minutes, obs, run
from .test_live import _tick, statuses

live_gate = test_live.live_gate
live_factory = test_live.live_factory

LIVE = {"control_mode": "live", "control_live_segments": True}
# The heater at heat_pump 140 °F (raw half-degrees Celsius).
HELD = {"mode": "heat_pump", "setpoint_raw": 120}


def _on_device(planner: Planner, **status):
    """What the heater reports when it holds the planner's list."""
    program = planner.program(obs(reservations_enabled=True))
    return obs(
        reservations_enabled=True,
        reservations=tuple(program["reservation"]),
        **status,
    )


def test_a_segment_moved_inside_the_lead_gets_a_near_term_entry():
    """Plan B moves s1 (same id and state) to inside the lead: A's entry
    for s1 is withdrawn, so s1 is not put in force by it."""
    planner = Planner(capabilities(**LIVE), TZ, shadow=False)
    run(planner, NOW, obs())
    plan_a = parse_plan(
        make_document(
            NOW,
            [
                segment(NOW, "s0", -10, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s1", 90, setpoint_f=125),
            ],
            intent_id="A",
        )
    )
    planner.set_plan(plan_a, NOW, obs(**HELD))
    run(planner, NOW, _on_device(planner, **HELD))
    at = minutes(59.5)
    plan_b = parse_plan(
        make_document(
            at,
            [
                segment(NOW, "s0", -10, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s1", 61, setpoint_f=125),
            ],
            intent_id="B",
        )
    )
    planner.set_plan(plan_b, at, _on_device(planner, **HELD))
    assert "s1" not in planner.asserted
    run(planner, at, _on_device(planner, **HELD))
    run(planner, minutes(61.1), _on_device(planner, **HELD))
    assert any(
        e.serves == "s1" for e in [*planner.owned, *planner.extra]
    ), "nothing puts s1 in force"


def test_a_landed_write_of_an_older_plan_does_not_assert_the_new_plan():
    """A's write lands unconfirmed; B reuses s1 with another setpoint.
    Reconciling A's write must not count A's entry as B's."""
    planner = Planner(capabilities(**LIVE), TZ, shadow=False)
    planner.step(NOW, obs())
    plan_a = parse_plan(
        make_document(
            NOW,
            [
                segment(NOW, "s1", 10, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s2", 120, setpoint_f=130),
            ],
            intent_id="A",
        )
    )
    planner.set_plan(plan_a, NOW, obs())
    write = planner.step(NOW, obs())
    assert write is not None
    landed_list = planner.program(
        obs(reservations_enabled=False, reservations=()), write.result
    )
    landed = obs(
        reservations_enabled=True,
        reservations=tuple(landed_list["reservation"]),
    )
    planner.reject(
        write,
        NOW,
        retry_at=minutes(15),
        final=True,
        written_hash=schedule_hash(landed_list),
    )
    planner.hold_until = minutes(15)
    plan_b = parse_plan(
        make_document(
            minutes(5),
            [
                segment(NOW, "s1", 10, mode="heat_pump", setpoint_f=120),
                segment(NOW, "s2", 120, setpoint_f=130),
            ],
            intent_id="B",
        )
    )
    planner.set_plan(plan_b, minutes(5), landed)
    planner.step(minutes(6), landed)
    assert "s1" not in planner.asserted
    # A's 140 °F entry fired; the pause is over.
    planner.step(
        minutes(16),
        obs(
            reservations_enabled=True,
            reservations=landed.reservations,
            **HELD,
        ),
    )
    assert any(
        e.kind == KIND_NEAR_TERM and e.serves == "s1" for e in planner.extra
    ), "B's s1 is never put in force"


def test_a_refused_segment_stays_refused_across_a_restart():
    plan = parse_plan(
        make_document(
            NOW,
            [
                segment(NOW, "s0", -10, mode="heat_pump", setpoint_f=140),
                segment(NOW, "s1", 10, mode="electric", setpoint_f=140),
                segment(NOW, "s2", 600, mode="heat_pump", setpoint_f=140),
            ],
        )
    )
    planner = Planner(capabilities(**LIVE), TZ, shadow=False)
    run(planner, NOW, obs())
    planner.set_plan(plan, NOW, obs(**HELD))
    run(planner, NOW, _on_device(planner, **HELD))
    # s1's electric entry fires and the heater holds heat_pump.
    for m in (10.1, 11, 12.5, 13, 30):
        run(
            planner,
            minutes(m),
            _on_device(planner, status_at=minutes(m), **HELD),
        )
    before = {s.id: (s.status, s.reason) for s in planner.ack("i").segments}
    assert before["s1"] == ("failed", "not_applied_on_device")

    restarted = Planner(capabilities(**LIVE), TZ, shadow=False)
    restarted.load_document(planner.as_document())
    restarted.set_plan(
        plan, minutes(31), _on_device(restarted, **HELD), restoring=True
    )
    run(
        restarted,
        minutes(31),
        _on_device(restarted, status_at=minutes(31), **HELD),
    )
    after = {s.id: (s.status, s.reason) for s in restarted.ack("i").segments}
    assert after["s1"] == before["s1"]


@pytest.mark.asyncio
async def test_a_landed_retry_resets_the_failure_count(
    hass, live_factory, now, freezer
):
    """A retry that finds the lost write landed leaves nothing failing:
    the next write lost once is retried, not paused."""
    heater, control = await live_factory()
    heater.land_unconfirmed = 1
    _publish(
        hass,
        make_document(
            now,
            [
                segment(now, "f1", 30, mode="heat_pump", setpoint_f=140),
                segment(now, "f2", 120, setpoint_f=130),
            ],
        ),
    )
    await hass.async_block_till_done()
    assert control.last_write.confirmed is False
    writes = len(heater.writes)
    await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))
    assert control.last_write.confirmed is True
    assert len(heater.writes) == writes

    await _tick(hass, freezer, timedelta(minutes=2))
    later = dt_util.utcnow().replace(second=0, microsecond=0)
    heater.lose = 1
    _publish(
        hass,
        make_document(
            later,
            [
                segment(later, "g1", 30, mode="heat_pump", setpoint_f=135),
                segment(later, "g2", 120, setpoint_f=128),
            ],
            intent_id="second",
        ),
    )
    await hass.async_block_till_done()
    assert statuses(control.ack)["g1"][0] != "failed"
    assert control._paused_until is not None
    assert control._paused_until - dt_util.utcnow() <= WRITE_RETRY
