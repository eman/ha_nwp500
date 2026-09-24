"""The per-device controller: intake, the planner's clock, and storage."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nwp500.const import (
    CONF_CONTROL_ALLOWED_MODES,
    CONF_CONTROL_ENABLED,
    CONF_CONTROL_INTENT_ENTITY,
    CONF_CONTROL_MODE,
    CONTROL_MODE_DISABLED,
    DOMAIN,
)
from custom_components.nwp500.control.device import (
    HEARTBEAT_INTERVAL,
    DeviceControl,
)
from custom_components.nwp500.control.entries import KIND_NEAR_TERM, KIND_PLAN
from custom_components.nwp500.control.intent import (
    REASON_MODE_NOT_ALLOWED,
    REASON_SUPERSEDED,
    REASON_UNSUPPORTED_PROTOCOL,
)
from custom_components.nwp500.control.store import ControlStore

from .conftest import FakeFeatures, make_document, segment

MAC = "AA:BB:CC:DD:EE:FF"
INTENT_ENTITY = "sensor.water_heater_intent"
ALL_MODES = ["heat_pump", "energy_saver", "high_demand", "electric"]


def _status(**overrides) -> MagicMock:
    """A device status: energy saver at 139.1 degF, idle."""
    status = MagicMock()
    status.dhw_operation_setting = 3
    status.dhw_target_temperature_setting_raw = 119
    status.tou_status = False
    status.comp_use = False
    status.tank_upper_temperature_raw = 575
    status.anti_legionella_operation_busy = False
    for key, value in overrides.items():
        setattr(status, key, value)
    return status


def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={
            CONF_CONTROL_ENABLED: True,
            CONF_CONTROL_INTENT_ENTITY: INTENT_ENTITY,
            CONF_CONTROL_ALLOWED_MODES: ALL_MODES,
            **options,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(mock_device, status, schedule) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {MAC: {"device": mock_device, "status": status}}
    coordinator.device_features = {MAC: FakeFeatures()}
    coordinator.reservation_schedules = {MAC: schedule} if schedule else {}
    coordinator.tou_schedules = {}
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    return coordinator


EMPTY_LIST = {"reservation_use": 1, "reservation": []}


@pytest.fixture
async def control_factory(hass: HomeAssistant, hass_storage, mock_device):
    """Build and start a controller; the caller sets the scene first."""
    started: list[DeviceControl] = []

    async def _make(
        status="typical", schedule=EMPTY_LIST, **options
    ) -> DeviceControl:
        entry = _entry(hass, **options)
        store = ControlStore(hass, entry.entry_id)
        await store.async_load()
        control = DeviceControl(
            hass,
            entry,
            _coordinator(
                mock_device,
                _status() if status == "typical" else status,
                schedule,
            ),
            MAC,
            mock_device,
            store,
        )
        await control.async_start()
        await hass.async_block_till_done()
        started.append(control)
        return control

    yield _make

    for control in started:
        await control.async_stop()


def _publish(hass: HomeAssistant, document: dict) -> None:
    """Publish a document the way an MQTT sensor does."""
    hass.states.async_set(
        INTENT_ENTITY,
        document["intent_id"],
        {**document, "friendly_name": "Water heater intent"},
    )


def _plan(now, **kwargs) -> dict:
    return make_document(
        now,
        [
            segment(now, "now", -5, mode="heat_pump", setpoint_f=140),
            segment(now, "later", 120, setpoint_f=130),
        ],
        **kwargs,
    )


def _kinds(control: DeviceControl) -> list[str]:
    return sorted(e.kind for e in control.planner.owned)


class TestStart:
    @pytest.mark.asyncio
    async def test_nothing_to_adopt(self, control_factory):
        control = await control_factory()
        assert control.plan is None
        assert control.ack.state == "none"
        assert control.heartbeat is not None
        assert control.planner.owner is not None
        assert control.planner.owner.declared is False
        assert control.store.stored_owner(MAC)["mode"] == "energy_saver"

    @pytest.mark.asyncio
    async def test_the_owner_waits_for_the_reservation_list(
        self, control_factory
    ):
        control = await control_factory(schedule=None)
        assert control.planner.owner is None

    @pytest.mark.asyncio
    async def test_adopts_the_entity_document(self, hass, control_factory, now):
        _publish(hass, _plan(now))
        control = await control_factory()

        assert control.plan is not None
        assert control.ack.state == "shadow"
        assert _kinds(control) == [KIND_NEAR_TERM, KIND_PLAN]
        assert control.last_write is not None
        assert control.last_write.simulated is True
        assert (
            control.store.stored_intent(MAC)["document"]["intent_id"] == "i-1"
        )
        assert control.store.stored_engine(MAC)["owned"]

    @pytest.mark.asyncio
    async def test_restores_the_stored_plan_without_re_asserting(
        self, hass, hass_storage, control_factory, now
    ):
        hass.states.async_set(INTENT_ENTITY, STATE_UNAVAILABLE)
        _publish(hass, _plan(now))
        first = await control_factory()
        owned = list(first.planner.owned)
        await first.async_stop()
        hass.states.async_set(INTENT_ENTITY, STATE_UNAVAILABLE)

        second = await control_factory()

        assert second.plan is not None
        assert second.plan.intent_id == "i-1"
        assert second.planner.owned == owned

    @pytest.mark.asyncio
    async def test_a_newer_entity_document_replaces_the_stored_one(
        self, hass, control_factory, now
    ):
        _publish(hass, _plan(now))
        first = await control_factory()
        await first.async_stop()
        _publish(
            hass,
            _plan(now, intent_id="i-2", issued_at=now + timedelta(minutes=1)),
        )

        second = await control_factory()

        assert second.plan.intent_id == "i-2"

    @pytest.mark.asyncio
    async def test_drops_a_stored_plan_that_no_longer_fits(
        self, hass, control_factory, now
    ):
        _publish(hass, _plan(now))
        first = await control_factory()
        await first.async_stop()
        hass.states.async_set(INTENT_ENTITY, STATE_UNAVAILABLE)

        second = await control_factory(
            **{CONF_CONTROL_ALLOWED_MODES: ["energy_saver"]}
        )

        assert second.plan is None
        assert second.store.stored_intent(MAC) is None

    @pytest.mark.asyncio
    async def test_disabled_cleans_up_once(self, hass, control_factory, now):
        _publish(hass, _plan(now))
        first = await control_factory()
        await first.async_stop()

        disabled = await control_factory(
            **{CONF_CONTROL_MODE: CONTROL_MODE_DISABLED}
        )

        assert disabled.plan is None
        assert disabled.planner.owned == []
        assert disabled.last_write.reason == "disable"
        assert disabled.last_write.owner_state == ("energy_saver", 119)
        assert disabled.store.disabled_done(MAC) is True
        write = disabled.last_write
        await disabled.async_stop()

        again = await control_factory(
            **{CONF_CONTROL_MODE: CONTROL_MODE_DISABLED}
        )
        assert again.last_write == write

    @pytest.mark.asyncio
    async def test_state_from_an_earlier_version_is_discarded(
        self, hass, hass_storage, control_factory
    ):
        store = ControlStore(hass, "entry1")
        await store.async_load()
        await store.async_set_engine(
            MAC,
            {
                "owned": [
                    {"kind": "closing", "directive_id": "d1", "param": 119}
                ]
            },
        )
        control = await control_factory()
        assert control.planner.owned == []

    @pytest.mark.asyncio
    async def test_live_is_run_as_shadow(self, control_factory):
        control = await control_factory(**{CONF_CONTROL_MODE: "live"})
        assert control.mode == "shadow"
        # The declaration says what actually runs (#162).
        assert control.capabilities.mode == "shadow"
        assert control.planner.capabilities.mode == "shadow"


class TestIntake:
    @pytest.mark.asyncio
    async def test_a_new_state_is_a_new_plan(self, hass, control_factory, now):
        control = await control_factory()
        _publish(hass, _plan(now))
        await hass.async_block_till_done()
        assert control.plan is not None
        assert control.ack.state == "shadow"

    @pytest.mark.asyncio
    async def test_attribute_only_change_is_ignored(
        self, hass, control_factory, now
    ):
        _publish(hass, _plan(now))
        control = await control_factory()
        _publish(hass, _plan(now, plan_id="changed"))
        await hass.async_block_till_done()
        assert control.plan.extra == {}

    @pytest.mark.asyncio
    async def test_source_unavailable_keeps_the_plan(
        self, hass, control_factory, now
    ):
        _publish(hass, _plan(now))
        control = await control_factory()
        hass.states.async_set(INTENT_ENTITY, STATE_UNAVAILABLE)
        await hass.async_block_till_done()
        assert control.plan is not None

    @pytest.mark.asyncio
    async def test_a_rejected_document_leaves_the_plan(
        self, hass, control_factory, now
    ):
        _publish(hass, _plan(now))
        control = await control_factory()
        _publish(hass, _plan(now, intent_id="i-2", protocol="9"))
        await hass.async_block_till_done()
        assert control.plan.intent_id == "i-1"
        assert control.ack.state == "rejected"
        assert control.ack.reason == REASON_UNSUPPORTED_PROTOCOL
        assert control.ack.intent_id == "i-2"

    @pytest.mark.asyncio
    async def test_an_older_document_is_superseded(
        self, hass, control_factory, now
    ):
        _publish(hass, _plan(now))
        control = await control_factory()
        _publish(
            hass,
            _plan(now, intent_id="i-0", issued_at=now - timedelta(minutes=5)),
        )
        await hass.async_block_till_done()
        assert control.ack.reason == REASON_SUPERSEDED
        assert control.plan.intent_id == "i-1"

    @pytest.mark.asyncio
    async def test_a_mode_not_allowed_rejects_the_plan(
        self, hass, control_factory, now
    ):
        control = await control_factory(
            **{CONF_CONTROL_ALLOWED_MODES: ["energy_saver"]}
        )
        _publish(hass, _plan(now))
        await hass.async_block_till_done()
        assert control.plan is None
        assert control.ack.reason == REASON_MODE_NOT_ALLOWED

    @pytest.mark.asyncio
    async def test_an_accepted_plan_clears_a_rejection(
        self, hass, control_factory, now
    ):
        control = await control_factory()
        _publish(hass, _plan(now, protocol="9"))
        await hass.async_block_till_done()
        assert control.ack.state == "rejected"
        _publish(hass, _plan(now, intent_id="i-2"))
        await hass.async_block_till_done()
        assert control.ack.state == "shadow"


class TestTiming:
    @pytest.mark.asyncio
    async def test_the_plan_advances_on_its_own_timer(
        self, hass, control_factory, now, freezer
    ):
        freezer.move_to(now)
        _publish(hass, _plan(now))
        control = await control_factory()
        assert control.wanted.setpoint_raw == 120

        later = now + timedelta(minutes=121)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

        assert control.wanted.setpoint_raw == 109

    @pytest.mark.asyncio
    async def test_heartbeat(self, hass, control_factory, now, freezer):
        freezer.move_to(now)
        control = await control_factory()
        first = control.heartbeat
        later = now + HEARTBEAT_INTERVAL + timedelta(seconds=1)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()
        assert control.heartbeat > first

    @pytest.mark.asyncio
    async def test_coordinator_updates_evaluate(self, hass, control_factory):
        control = await control_factory()
        listener = control.coordinator.async_add_listener.call_args.args[0]
        control.coordinator.data[MAC]["status"] = _status(
            dhw_target_temperature_setting_raw=100
        )
        listener()
        await hass.async_block_till_done()
        assert "setpoint" in control.reports

    @pytest.mark.asyncio
    async def test_stop_cancels_the_timers(
        self, hass, control_factory, now, freezer
    ):
        freezer.move_to(now)
        _publish(hass, _plan(now))
        control = await control_factory()
        await control.async_stop()
        steps = control.planner.last_step
        later = now + timedelta(hours=3)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()
        assert control.planner.last_step == steps


class TestReading:
    @pytest.mark.asyncio
    async def test_surplus_entity(self, hass, control_factory):
        hass.states.async_set("sensor.export", "0.6")
        control = await control_factory(
            control_surplus_entity="sensor.export",
            control_surplus_threshold_kw=0.45,
        )
        assert control.observe().surplus_on is True
        hass.states.async_set("sensor.export", "0.1")
        await hass.async_block_till_done()
        assert control.observe().surplus_on is False
        hass.states.async_set("sensor.export", "unavailable")
        await hass.async_block_till_done()
        assert control.observe().surplus_on is None
        hass.states.async_set("sensor.export", "junk")
        assert control.observe().surplus_on is None

    @pytest.mark.asyncio
    async def test_binary_surplus_entity(self, hass, control_factory):
        hass.states.async_set("binary_sensor.surplus", "on")
        control = await control_factory(
            control_surplus_entity="binary_sensor.surplus"
        )
        assert control.observe().surplus_on is True
        assert control.capabilities.grants_supported

    @pytest.mark.asyncio
    async def test_program_details_and_sync(self, hass, control_factory, now):
        _publish(hass, _plan(now))
        control = await control_factory()
        details = control.program_details()
        assert details["entry_count"] == 2
        assert {e["owner"] for e in details["entries"]} == {"near_term", "plan"}
        assert details["device_hash"] is not None
        assert details["device_hash"] != details["hash"]

    @pytest.mark.asyncio
    async def test_capabilities_carry_the_owner_and_room(self, control_factory):
        control = await control_factory()
        attrs = control.capabilities.as_attributes()
        assert attrs["owner_program"]["mode"] == "energy_saver"
        assert attrs["entries_available"] == 5

    @pytest.mark.asyncio
    async def test_telemetry_resolves_registered_entities(
        self, control_factory, entity_registry: er.EntityRegistry
    ):
        upper = entity_registry.async_get_or_create(
            "sensor", DOMAIN, f"{MAC}_tank_upper_temperature"
        )
        control = await control_factory()
        telemetry = control.capabilities.as_attributes()["telemetry"]
        assert telemetry["delivery_temperature"] == upper.entity_id
        assert telemetry["power"] is None

    @pytest.mark.asyncio
    async def test_listeners(self, control_factory):
        control = await control_factory()
        calls: list[int] = []
        remove = control.async_add_listener(lambda: calls.append(1))
        control._notify()
        remove()
        control._notify()
        assert calls == [1]
