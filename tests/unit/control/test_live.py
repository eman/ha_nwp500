"""Live mode (delivery step 5 of issue #158), against a simulated heater.

Nothing here reaches a real heater. The planner rules are driven with
times and snapshots, as in `test_engine`; the controller writes through a
`FakeHeater` in place of the coordinator's MQTT path, which can lose writes
the way the unit tested did (spec section 8).
"""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nwp500.const import (
    CONF_CONTROL_LIVE_SEGMENTS,
    CONF_CONTROL_MODE,
    CONF_CONTROL_OWNER_PROGRAM,
    CONTROL_MODE_DISABLED,
    CONTROL_MODE_LIVE,
)
from custom_components.nwp500.control import device as device_module
from custom_components.nwp500.control.device import (
    WRITE_PAUSE,
    WRITE_RETRY,
    DeviceControl,
)
from custom_components.nwp500.control.engine import (
    REPORT_SWITCHED_OFF,
    WRITE_TAKEOVER,
    Planner,
)
from custom_components.nwp500.control.entries import (
    KIND_NEAR_TERM,
    KIND_PLAN,
    schedule_hash,
)
from custom_components.nwp500.control.evaluate import (
    REASON_HELD_IN_TOU_WINDOW,
    REASON_NOT_APPLIED,
    REASON_WRITE_NOT_CONFIRMED,
)
from custom_components.nwp500.control.store import ControlStore

from .conftest import make_document, segment
from .test_device import (
    MAC,
    _coordinator,
    _entry,
    _publish,
    _status,
)
from .test_engine import NOW, TZ, give, minutes, obs, planner_with

HAND_BACK_ISSUE = f"control_hand_back_failed_{MAC}"

LIVE = {
    "control_mode": "live",
    "control_live_segments": True,
}

# The owner's program: energy saver at 139.1 degF, reservations on, one
# weekday entry at 06:00 setting energy saver at 140 degF.
OWNER_ENTRY = {
    "enable": 2,
    "week": 62,
    "hour": 6,
    "min": 0,
    "mode": 3,
    "param": 120,
}
OWNER_DOC = {
    "mode": "energy_saver",
    "setpoint_raw": 119,
    "reservations_enabled": True,
    "entries": [OWNER_ENTRY],
}
OWNER_LIST = {"reservation_use": 2, "reservation": [OWNER_ENTRY]}
FOREIGN_ENTRY = {
    "enable": 2,
    "week": 128,
    "hour": 9,
    "min": 30,
    "mode": 1,
    "param": 110,
}


def statuses(ack) -> dict[str, tuple[str, str | None]]:
    return {s.id: (s.status, s.reason) for s in ack.segments}


def device_obs(planner: Planner, **overrides):
    """The device holding exactly the planner's program."""
    program = planner.program(obs(reservations_enabled=True))
    values = {
        "reservations_enabled": True,
        "reservations": tuple(program["reservation"]),
        **overrides,
    }
    return obs(**values)


# -- the planner ----------------------------------------------------------


class TestPlannerLive:
    def test_takes_the_list_over_with_nothing_to_add(self):
        planner = planner_with(
            [segment(NOW, "s", -5, mode="energy_saver", setpoint_c=59.5)],
            shadow=False,
            **LIVE,
        )
        assert planner.last_write is not None
        assert planner.last_write.reason == WRITE_TAKEOVER
        assert planner.took_over is True
        assert planner.step(minutes(1), device_obs(planner)) is None

    def test_no_takeover_without_a_plan(self):
        planner = planner_with(shadow=False, **LIVE)
        assert planner.last_write is None
        assert planner.took_over is False

    def test_the_switch_stays_off_once_a_person_turned_it_off(self):
        planner = planner_with(
            [segment(NOW, "s", -5, mode="energy_saver", setpoint_c=59.5)],
            shadow=False,
            **LIVE,
        )
        assert planner.program(device_obs(planner))["reservation_use"] == 2
        planner.step(minutes(0.5), device_obs(planner))
        off = device_obs(planner, reservations_enabled=False)
        planner.step(minutes(1), off)
        assert planner.program(off)["reservation_use"] == 1
        assert REPORT_SWITCHED_OFF in planner.reports

    def test_a_rejected_near_term_moves_past_the_retry(self):
        planner = planner_with(shadow=False, **LIVE)
        plan = make_document(
            NOW, [segment(NOW, "s", -5, mode="heat_pump", setpoint_f=140)]
        )
        from custom_components.nwp500.control.intent import parse_plan

        planner.set_plan(parse_plan(plan), NOW, obs())
        write = planner.step(NOW, obs())
        assert [
            (e.kind, e.fires_at.strftime("%H:%M")) for e in write.added
        ] == [(KIND_NEAR_TERM, "10:02")]

        planner.reject(write, NOW, retry_at=minutes(1), final=False)

        assert planner.owned == []
        assert planner.last_write.confirmed is False
        assert [e.fires_at.strftime("%H:%M") for e in planner.extra] == [
            "10:03"
        ]
        assert planner.failed == {}
        assert statuses(planner.ack("i"))["s"] == ("pending", None)

        retry = planner.step(minutes(1), obs())
        planner.reject(retry, minutes(1), retry_at=minutes(16), final=True)

        assert planner.failed == {"s": REASON_WRITE_NOT_CONFIRMED}
        assert statuses(planner.ack("i"))["s"] == (
            "failed",
            REASON_WRITE_NOT_CONFIRMED,
        )
        assert planner.ack("i").state == "partly_programmed"
        assert [e.fires_at.strftime("%H:%M") for e in planner.extra] == [
            "10:18"
        ]

        resumed = planner.step(minutes(16), obs())
        planner.commit(resumed)
        assert planner.failed == {}
        assert statuses(planner.ack("i"))["s"] == ("programmed", None)

    def test_a_failed_plan_entry_is_failed_until_written(self):
        planner = planner_with(shadow=False, **LIVE)
        from custom_components.nwp500.control.intent import parse_plan

        planner.set_plan(
            parse_plan(
                make_document(
                    NOW,
                    [
                        segment(
                            NOW, "a", -5, mode="energy_saver", setpoint_c=59.5
                        ),
                        segment(NOW, "b", 60, setpoint_f=130),
                    ],
                )
            ),
            NOW,
            obs(),
        )
        write = planner.step(NOW, obs())
        planner.reject(write, NOW, retry_at=minutes(1), final=True)
        assert statuses(planner.ack("i"))["b"] == (
            "failed",
            REASON_WRITE_NOT_CONFIRMED,
        )
        again = planner.step(minutes(16), obs())
        assert any(e.kind == KIND_PLAN for e in again.added)
        planner.commit(again)
        assert statuses(planner.ack("i"))["b"] == ("programmed", None)


class TestReadBack:
    """Section 5.11."""

    def _planner(self, **segment_state) -> Planner:
        planner = planner_with(shadow=False, **LIVE)
        give(
            planner,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_c=59.5),
                segment(NOW, "b", 10, mode="heat_pump", **segment_state),
            ],
        )
        assert any(e.serves == "b" for e in planner.owned)
        return planner

    def test_an_applied_entry_is_in_force(self):
        planner = self._planner(setpoint_f=140)
        fired = device_obs(planner, mode="heat_pump", setpoint_raw=120)
        planner.step(minutes(10.5), fired)
        planner.step(minutes(11.6), fired)
        assert planner.readback == {}
        assert statuses(planner.ack("i"))["b"] == ("in_force", None)
        assert planner.reports == {}

    def test_an_entry_that_did_not_apply_is_failed(self):
        planner = self._planner(setpoint_f=140)
        planner.step(minutes(11.6), device_obs(planner))
        assert planner.readback == {"b": REASON_NOT_APPLIED}
        assert statuses(planner.ack("i"))["b"] == ("failed", REASON_NOT_APPLIED)

    def test_checked_once(self):
        planner = self._planner(setpoint_f=140)
        planner.step(minutes(11.6), device_obs(planner))
        planner.readback.clear()
        planner.step(minutes(12), device_obs(planner))
        assert planner.readback == {}

    def test_a_mode_held_in_a_tou_window(self):
        planner = self._planner(setpoint_c=59.5)
        all_day = {
            "season": 0xFFF,
            "week": 254,
            "start_hour": 0,
            "start_min": 0,
            "end_hour": 23,
            "end_min": 59,
            "price_max": 10,
        }
        held = device_obs(planner, tou_on=True, tou_periods=(all_day,))
        planner.step(minutes(11.6), held)
        assert planner.readback == {"b": REASON_HELD_IN_TOU_WINDOW}
        assert statuses(planner.ack("i"))["b"] == (
            "in_force",
            REASON_HELD_IN_TOU_WINDOW,
        )

    def _detail(self, planner, item):
        return next(
            seg.detail for seg in planner.ack("i").segments if seg.id == item
        )

    def test_the_mode_is_not_inferred_from_what_the_heater_runs(self):
        """Only what the heater reports counts (section 5.11)."""
        planner = self._planner(setpoint_f=140)
        fired = device_obs(planner, mode="heat_pump", setpoint_raw=120)
        planner.step(minutes(10.5), fired)
        planner.step(minutes(11.6), fired)
        assert statuses(planner.ack("i"))["b"] == ("in_force", None)
        assert "mode_confirmed" not in self._detail(planner, "b")

    def _window(self, end_hour: int) -> dict:
        return {
            "season": 0xFFF,
            "week": 254,
            "start_hour": 0,
            "start_min": 0,
            "end_hour": end_hour,
            "end_min": 59,
            "price_max": 10,
        }

    def test_a_held_mode_is_applied_when_the_window_ends(self):
        planner = self._planner(setpoint_c=59.5)
        window = (self._window(10),)
        held = device_obs(planner, tou_on=True, tou_periods=window)
        planner.step(minutes(11.6), held)
        # The window runs to 10:59; the heater applies the mode at 11:00,
        # with no entry firing then. That is not a person's change.
        applied = device_obs(
            planner, mode="heat_pump", tou_on=True, tou_periods=window
        )
        planner.step(minutes(60.2), applied)
        assert planner.readback == {}
        assert planner.reports == {}
        assert statuses(planner.ack("i"))["b"] == ("in_force", None)

    def test_a_held_mode_not_applied_after_the_window_fails(self):
        planner = self._planner(setpoint_c=59.5)
        window = (self._window(10),)
        held = device_obs(planner, tou_on=True, tou_periods=window)
        planner.step(minutes(11.6), held)
        planner.step(minutes(62), held)
        assert planner.readback == {"b": REASON_NOT_APPLIED}

    def test_not_in_shadow(self):
        planner = planner_with(
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_c=59.5),
                segment(NOW, "b", 10, mode="heat_pump", setpoint_f=140),
            ]
        )
        planner.step(minutes(11.6), obs())
        assert planner.readback == {}

    def test_survives_a_restart(self):
        planner = self._planner(setpoint_f=140)
        planner.step(minutes(11.6), device_obs(planner))
        copy_ = Planner(planner.capabilities, TZ, shadow=False)
        copy_.load_document(planner.as_document())
        assert copy_.readback == planner.readback
        assert copy_.took_over is True


# -- the controller, against a simulated heater ---------------------------


class FakeHeater:
    """A heater's reservation list behind the `ListWriter` interface."""

    def __init__(self, coordinator, schedule: dict[str, Any]) -> None:
        self.coordinator = coordinator
        self.schedule = copy.deepcopy(schedule)
        coordinator.reservation_schedules[MAC] = copy.deepcopy(schedule)
        self.lose = 0
        self.reads = 0
        self.writes: list[dict[str, Any]] = []
        self.states: list[tuple[str, int]] = []
        self.restore_ok = True
        self.gate: asyncio.Event | None = None
        self.lock = asyncio.Lock()
        self.applies_state = True
        self.status_requests = 0
        # Lost writes that land anyway: only the confirmation is lost.
        self.land_unconfirmed = 0

    def locked(self) -> asyncio.Lock:
        return self.lock

    def person_sets(self, schedule: dict[str, Any], *, seen: bool = True):
        """A person changes the list; `seen` if the coordinator read it."""
        self.schedule = copy.deepcopy(schedule)
        if seen:
            self._seen(schedule)

    def _seen(self, schedule: dict[str, Any]) -> None:
        """The coordinator holds the list as read from the heater now."""
        self.coordinator.reservation_schedules[MAC] = copy.deepcopy(schedule)
        self.coordinator.reservation_schedules_read_at[MAC] = dt_util.utcnow()

    async def async_read(self) -> dict[str, Any] | None:
        self.reads += 1
        self._seen(self.schedule)
        return copy.deepcopy(self.schedule)

    async def async_write(self, schedule: dict[str, Any]) -> dict[str, Any]:
        if self.gate is not None:
            await self.gate.wait()
        self.writes.append(copy.deepcopy(schedule))
        if self.lose:
            # Lost with no error: the device keeps its list (section 8).
            self.lose -= 1
            return copy.deepcopy(self.schedule)
        if self.land_unconfirmed:
            # It lands, but neither the echo nor a read comes back.
            self.land_unconfirmed -= 1
            self.schedule = copy.deepcopy(schedule)
            return None
        self.person_sets(schedule)
        return copy.deepcopy(schedule)

    async def async_restore_state(self, mode: str, setpoint_raw: int) -> bool:
        self.states.append((mode, setpoint_raw))
        if self.restore_ok and self.applies_state:
            status = self.coordinator.data[MAC]["status"]
            status.dhw_operation_setting = {
                "heat_pump": 1,
                "electric": 2,
                "energy_saver": 3,
                "high_demand": 4,
            }[mode]
            status.dhw_target_temperature_setting_raw = setpoint_raw
        return self.restore_ok

    async def async_request_status(self) -> None:
        self.status_requests += 1


@pytest.fixture
def live_gate(monkeypatch):
    """Open the gate for these tests only."""
    monkeypatch.setattr(device_module, "CONTROL_LIVE_AVAILABLE", True)


@pytest.fixture
async def live_factory(
    hass: HomeAssistant, hass_storage, mock_device, live_gate
):
    started: list[DeviceControl] = []
    state: dict[str, Any] = {}

    async def _make(
        *, schedule=OWNER_LIST, status=None, reuse=False, **options
    ) -> tuple[FakeHeater, DeviceControl]:
        if not reuse or "heater" not in state:
            coordinator = _coordinator(
                mock_device, status or _status(), schedule
            )
            state["heater"] = FakeHeater(coordinator, schedule)
        heater: FakeHeater = state["heater"]
        merged = {
            CONF_CONTROL_MODE: CONTROL_MODE_LIVE,
            CONF_CONTROL_LIVE_SEGMENTS: True,
            CONF_CONTROL_OWNER_PROGRAM: {MAC: OWNER_DOC},
            **options,
        }
        hass.config_entries.async_entries()
        for old in hass.config_entries.async_entries("nwp500"):
            await hass.config_entries.async_remove(old.entry_id)
        entry = _entry(hass, **merged)
        store = ControlStore(hass, entry.entry_id)
        await store.async_load()
        control = DeviceControl(
            hass,
            entry,
            heater.coordinator,
            MAC,
            mock_device,
            store,
            writer=heater,
        )
        await control.async_start()
        await hass.async_block_till_done()
        started.append(control)
        return heater, control

    yield _make

    for control in started:
        await control.async_stop()


def _two_segments(now, **kwargs) -> dict:
    return make_document(
        now,
        [
            segment(now, "now", -5, mode="heat_pump", setpoint_f=140),
            segment(now, "later", 120, setpoint_f=130),
        ],
        **kwargs,
    )


def _entries(schedule: dict[str, Any], **match) -> list[dict[str, int]]:
    return [
        e
        for e in schedule["reservation"]
        if all(e.get(k) == v for k, v in match.items())
    ]


async def _tick(hass: HomeAssistant, freezer, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


class TestGate:
    @pytest.mark.asyncio
    async def test_live_runs_as_shadow_while_the_gate_is_closed(
        self, hass, hass_storage, mock_device, now, monkeypatch
    ):
        monkeypatch.setattr(device_module, "CONTROL_LIVE_AVAILABLE", False)
        coordinator = _coordinator(mock_device, _status(), OWNER_LIST)
        heater = FakeHeater(coordinator, OWNER_LIST)
        entry = _entry(
            hass,
            **{
                CONF_CONTROL_MODE: CONTROL_MODE_LIVE,
                CONF_CONTROL_LIVE_SEGMENTS: True,
                CONF_CONTROL_OWNER_PROGRAM: {MAC: OWNER_DOC},
            },
        )
        store = ControlStore(hass, entry.entry_id)
        await store.async_load()
        _publish(hass, _two_segments(now))
        control = DeviceControl(
            hass, entry, coordinator, MAC, mock_device, store, writer=heater
        )
        await control.async_start()
        await hass.async_block_till_done()
        try:
            assert control.mode == "shadow"
            assert control.planner.shadow is True
            assert heater.writes == []
            assert heater.reads == 0
            assert control.last_write.simulated is True
        finally:
            await control.async_stop()

    @pytest.mark.asyncio
    async def test_segments_switch_off_writes_nothing(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory(
            **{CONF_CONTROL_LIVE_SEGMENTS: False}
        )
        # It writes nothing, so it runs, and is declared, as shadow.
        assert control.mode == "shadow"
        assert control.planner.shadow is True
        assert heater.writes == []


class TestStopped:
    """Stopped stops applying plans, and nothing else (#192)."""

    @pytest.mark.asyncio
    async def test_it_writes_nothing_and_leaves_the_heater_as_it_is(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        heater, live = await live_factory()
        written = copy.deepcopy(heater.schedule)
        writes = len(heater.writes)
        await live.async_stop()

        _, stopped = await live_factory(
            reuse=True, **{CONF_CONTROL_MODE: CONTROL_MODE_DISABLED}
        )
        _publish(
            hass,
            make_document(
                now,
                [segment(now, "x", 60, mode="heat_pump", setpoint_f=125)],
                intent_id="i-2",
                issued_at=now + timedelta(seconds=1),
            ),
        )
        await hass.async_block_till_done()

        assert len(heater.writes) == writes
        assert heater.states == []
        assert heater.schedule == written
        assert stopped.plan is None or stopped.plan.intent_id != "i-2"


class TestLiveWrites:
    @pytest.mark.asyncio
    async def test_the_first_plan_takes_the_list_over(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory()

        assert len(heater.writes) == 1
        written = heater.writes[0]
        assert written["reservation_use"] == 2
        # Every entry that is not the feature's is kept as read.
        assert _entries(written, hour=6, min=0) == [OWNER_ENTRY]
        assert len(written["reservation"]) == 3
        assert control.last_write.simulated is False
        assert control.last_write.confirmed is True
        assert control.store.took_over(MAC) is True
        assert sorted(e.kind for e in control.planner.owned) == [
            KIND_NEAR_TERM,
            KIND_PLAN,
        ]
        assert control.ack.state == "programmed"
        assert statuses(control.ack) == {
            "now": ("programmed", None),
            "later": ("programmed", None),
        }
        assert control.capabilities.mode == "live"

    @pytest.mark.asyncio
    async def test_reads_the_list_before_writing(self, hass, live_factory, now):
        heater, control = await live_factory()
        # A person adds an entry that the coordinator has not read yet.
        heater.person_sets(
            {
                "reservation_use": 2,
                "reservation": [OWNER_ENTRY, FOREIGN_ENTRY],
            },
            seen=False,
        )
        _publish(hass, _two_segments(now))
        await hass.async_block_till_done()

        assert heater.reads >= 1
        assert _entries(heater.writes[-1], hour=9, min=30) == [FOREIGN_ENTRY]

    @pytest.mark.asyncio
    async def test_nothing_is_sent_when_the_device_already_has_it(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory()
        writes = len(heater.writes)
        await control._async_evaluate(dt_util.utcnow())
        assert len(heater.writes) == writes

    @pytest.mark.asyncio
    async def test_a_lost_write_is_retried_after_a_minute(
        self, hass, live_factory, now, freezer
    ):
        heater, control = await live_factory()
        heater.lose = 1
        _publish(hass, _two_segments(now))
        await hass.async_block_till_done()

        assert len(heater.writes) == 1
        assert control.planner.owned == []
        assert control.last_write.confirmed is False
        # It may have landed: disabling would hand the heater back.
        assert control.store.took_over(MAC) is True
        assert control.planner.took_over is False
        assert statuses(control.ack)["later"] == ("pending", None)
        first_near_term = next(
            e for e in control.last_write.added if e.kind == KIND_NEAR_TERM
        )

        # Nothing is written again before the retry is due.
        await control._async_evaluate(dt_util.utcnow())
        assert len(heater.writes) == 1

        await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))

        assert len(heater.writes) == 2
        assert control.last_write.confirmed is True
        near_term = next(
            e for e in control.planner.owned if e.kind == KIND_NEAR_TERM
        )
        assert near_term.fires_at > first_near_term.fires_at
        assert statuses(control.ack)["later"] == ("programmed", None)

    @pytest.mark.asyncio
    async def test_two_lost_writes_fail_then_pause(
        self, hass, live_factory, now, freezer
    ):
        heater, control = await live_factory()
        heater.lose = 2
        _publish(hass, _two_segments(now))
        await hass.async_block_till_done()
        await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))

        assert len(heater.writes) == 2
        assert statuses(control.ack) == {
            "now": ("failed", REASON_WRITE_NOT_CONFIRMED),
            "later": ("failed", REASON_WRITE_NOT_CONFIRMED),
        }
        assert control.ack.state == "partly_programmed"

        await _tick(hass, freezer, timedelta(minutes=5))
        assert len(heater.writes) == 2

        await _tick(hass, freezer, WRITE_PAUSE)
        assert len(heater.writes) == 3
        assert control.last_write.confirmed is True
        assert statuses(control.ack) == {
            "now": ("programmed", None),
            "later": ("programmed", None),
        }

    @pytest.mark.asyncio
    async def test_changes_during_a_write_are_folded_into_the_next(
        self, hass, live_factory, now
    ):
        heater, control = await live_factory()
        heater.gate = asyncio.Event()
        _publish(hass, _two_segments(now))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        _publish(
            hass,
            make_document(
                now,
                [
                    segment(now, "now", -5, mode="heat_pump", setpoint_f=140),
                    segment(now, "later", 120, setpoint_f=130),
                    segment(now, "evening", 240, setpoint_f=135),
                ],
                intent_id="i-2",
                issued_at=now + timedelta(seconds=1),
            ),
        )
        await asyncio.sleep(0)
        heater.gate.set()
        await hass.async_block_till_done()

        assert control.plan.intent_id == "i-2"
        assert {e.serves for e in control.planner.owned} >= {
            "later",
            "evening",
        }
        assert control.planner.program_hash(control.observe()) == (
            schedule_hash(heater.schedule)
        )

    @pytest.mark.asyncio
    async def test_a_person_turning_reservations_off_is_respected(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory()
        heater.person_sets({**heater.schedule, "reservation_use": 1})
        await control._async_evaluate(dt_util.utcnow())
        assert REPORT_SWITCHED_OFF in control.reports

        _publish(
            hass,
            make_document(
                now,
                [
                    segment(now, "now", -5, mode="heat_pump", setpoint_f=140),
                    segment(now, "later", 180, setpoint_f=130),
                ],
                intent_id="i-2",
                issued_at=now + timedelta(seconds=1),
            ),
        )
        await hass.async_block_till_done()
        assert heater.writes[-1]["reservation_use"] == 1

    @pytest.mark.asyncio
    async def test_a_removed_entry_is_not_written_again(
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
        writes = len(heater.writes)
        await control._async_evaluate(dt_util.utcnow())

        assert statuses(control.ack)["later"] == ("removed", None)
        assert len(heater.writes) == writes


class TestReviewFindings:
    """Regressions for the review of the first live-mode commit."""

    @pytest.mark.asyncio
    async def test_a_write_that_landed_unconfirmed_is_recognised(
        self, hass, live_factory, now, freezer
    ):
        heater, control = await live_factory()
        heater.land_unconfirmed = 1
        _publish(hass, _two_segments(now))
        await hass.async_block_till_done()
        assert control.last_write.confirmed is False
        landed = copy.deepcopy(heater.schedule)

        await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))

        assert control.last_write.confirmed is True
        # No duplicate entries, and nothing of its own reported as foreign.
        assert len(heater.schedule["reservation"]) == len(landed["reservation"])
        assert not [k for k in control.reports if k.startswith("foreign")]

    @pytest.mark.asyncio
    async def test_a_stopped_controller_arms_no_retry(
        self, hass, live_factory, now, freezer
    ):
        heater, control = await live_factory()
        heater.gate = asyncio.Event()
        heater.lose = 1
        _publish(hass, _two_segments(now))
        for _ in range(5):
            await asyncio.sleep(0)
        stop = hass.async_create_task(control.async_stop())
        for _ in range(5):
            await asyncio.sleep(0)
        heater.gate.set()
        await hass.async_block_till_done()
        assert stop.done()
        assert len(heater.writes) == 1

        await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))
        await _tick(hass, freezer, WRITE_PAUSE)

        assert len(heater.writes) == 1

    @pytest.mark.asyncio
    async def test_a_segment_begun_during_a_pause_is_put_in_force(
        self, hass, live_factory, now, freezer
    ):
        heater, control = await live_factory()
        heater.lose = 2
        _publish(hass, _two_segments(now))
        await hass.async_block_till_done()
        await _tick(hass, freezer, WRITE_RETRY + timedelta(seconds=1))
        assert len(heater.writes) == 2

        _publish(
            hass,
            make_document(
                now,
                [segment(now, "now2", -5, mode="heat_pump", setpoint_f=120)],
                intent_id="i-2",
                issued_at=now + timedelta(minutes=1),
            ),
        )
        await hass.async_block_till_done()
        await _tick(hass, freezer, timedelta(minutes=5))
        await _tick(hass, freezer, WRITE_PAUSE)

        assert control.last_write.confirmed is True
        near_terms = [
            e
            for e in control.planner.owned
            if e.kind == KIND_NEAR_TERM and e.serves == "now2"
        ]
        assert len(near_terms) == 1
        assert near_terms[0].fires_at > dt_util.utcnow()

    @pytest.mark.asyncio
    async def test_an_older_document_waiting_on_a_write_is_superseded(
        self, hass, live_factory, now
    ):
        heater, control = await live_factory()
        _publish(hass, _two_segments(now))
        await hass.async_block_till_done()
        heater.gate = asyncio.Event()
        # A pass that writes holds the lock while both documents arrive.
        control.coordinator.data[MAC]["status"].dhw_operation_setting = 6
        evaluating = hass.async_create_task(
            control._async_evaluate(dt_util.utcnow())
        )
        for _ in range(5):
            await asyncio.sleep(0)
        newer = hass.async_create_task(
            control.async_receive(
                _two_segments(
                    now, intent_id="new", issued_at=now + timedelta(seconds=2)
                ),
                dt_util.utcnow(),
            )
        )
        older = hass.async_create_task(
            control.async_receive(
                _two_segments(
                    now, intent_id="old", issued_at=now + timedelta(seconds=1)
                ),
                dt_util.utcnow(),
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
        heater.gate.set()
        await hass.async_block_till_done()

        assert evaluating.done()
        assert newer.result() is True
        assert older.result() is False
        assert control.plan.intent_id == "new"

    @pytest.mark.asyncio
    async def test_shadow_does_not_overwrite_the_live_entries(
        self, hass, live_factory, now
    ):
        _publish(hass, _two_segments(now))
        heater, control = await live_factory()
        live_owned = list(control.planner.owned)
        await control.async_stop()

        _, shadow = await live_factory(
            reuse=True, **{CONF_CONTROL_LIVE_SEGMENTS: False}
        )
        _publish(
            hass,
            make_document(
                now,
                [segment(now, "x", 60, mode="heat_pump", setpoint_f=125)],
                intent_id="i-2",
                issued_at=now + timedelta(seconds=1),
            ),
        )
        await hass.async_block_till_done()

        assert shadow.holds_device is True
        assert shadow.planner.owned == live_owned


class TestTrialFindings:
    """Regressions for what the first live trial on the real heater showed."""

    def _planner(self) -> Planner:
        planner = planner_with(shadow=False, **LIVE)
        give(
            planner,
            [
                segment(NOW, "a", -5, mode="energy_saver", setpoint_c=59.5),
                segment(NOW, "b", 10, mode="heat_pump", setpoint_f=140),
            ],
        )
        return planner

    def test_an_entry_whose_minute_came_is_not_removed_at_once(self):
        planner = self._planner()
        entry = next(e for e in planner.owned if e.serves == "b")
        fired = device_obs(planner, mode="heat_pump", setpoint_raw=120)
        # 5 s past its minute the segment has begun; its entry stays.
        assert planner.step(minutes(10) + timedelta(seconds=5), fired) is None
        assert entry in planner.owned
        planner.step(minutes(11.6), fired)
        assert planner.readback == {}

    def test_a_change_reported_just_before_the_minute_is_the_entry(self):
        planner = self._planner()
        early = device_obs(planner, mode="heat_pump", setpoint_raw=120)
        # The heater's clock runs ahead: the change arrives 4 s early.
        planner.step(minutes(10) - timedelta(seconds=4), early)
        assert planner.reports == {}
