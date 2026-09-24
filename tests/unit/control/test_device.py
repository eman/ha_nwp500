"""The per-device controller: intake, storage, staleness, heartbeat."""

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
from custom_components.nwp500.control.evaluate import (
    STATUS_NONE,
    STATUS_REJECTED,
    STATUS_SHADOW,
)
from custom_components.nwp500.control.intent import REASON_UNSUPPORTED_PROTOCOL
from custom_components.nwp500.control.store import ControlStore

from .conftest import FakeFeatures, directive, make_document

MAC = "AA:BB:CC:DD:EE:FF"
INTENT_ENTITY = "sensor.water_heater_intent"


def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={
            CONF_CONTROL_ENABLED: True,
            CONF_CONTROL_INTENT_ENTITY: INTENT_ENTITY,
            **options,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(mock_device, features=None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {MAC: {"device": mock_device, "status": None}}
    coordinator.device_features = {MAC: features} if features else {}
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    return coordinator


@pytest.fixture
async def control_factory(hass: HomeAssistant, hass_storage, mock_device):
    """Build and start a controller; the caller sets the scene first."""
    started: list[DeviceControl] = []

    async def _make(**options) -> DeviceControl:
        entry = _entry(hass, **options)
        store = ControlStore(hass, entry.entry_id)
        await store.async_load()
        control = DeviceControl(
            hass,
            entry,
            _coordinator(mock_device, FakeFeatures()),
            MAC,
            mock_device,
            store,
        )
        await control.async_start()
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


class TestStart:
    @pytest.mark.asyncio
    async def test_nothing_to_adopt(self, control_factory):
        control = await control_factory()

        assert control.intent is None
        assert control.ack.state == STATUS_NONE
        assert control.heartbeat is not None
        assert isinstance(control.capabilities.feature_version, str)

    @pytest.mark.asyncio
    async def test_adopts_the_entity_current_document(
        self, hass, control_factory, now
    ):
        _publish(
            hass, make_document(now, [directive(now, "charge", target_f=130)])
        )

        control = await control_factory()

        assert control.intent is not None
        assert control.intent.intent_id == "i-1"
        assert control.ack.state == STATUS_SHADOW
        assert (
            control.store.stored_intent(MAC)["document"]["intent_id"] == "i-1"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_the_stored_intent(
        self, hass, hass_storage, control_factory, now
    ):
        """A restart with the source unavailable keeps the intent."""
        hass.states.async_set(INTENT_ENTITY, STATE_UNAVAILABLE)
        store = ControlStore(hass, "entry1")
        await store.async_load()
        received = (now - timedelta(minutes=5)).isoformat()
        await store.async_set_intent(MAC, make_document(now), received)

        control = await control_factory()

        assert control.intent is not None
        assert control.intent.intent_id == "i-1"
        assert control.received_at == now - timedelta(minutes=5)

    @pytest.mark.asyncio
    async def test_keeps_receipt_time_when_entity_and_store_agree(
        self, hass, control_factory, now
    ):
        document = make_document(now)
        _publish(hass, document)
        store = ControlStore(hass, "entry1")
        await store.async_load()
        received = (now - timedelta(minutes=7)).isoformat()
        await store.async_set_intent(MAC, document, received)

        control = await control_factory()

        assert control.received_at == now - timedelta(minutes=7)

    @pytest.mark.asyncio
    async def test_drops_a_stored_intent_that_went_stale(
        self, hass, control_factory, now
    ):
        store = ControlStore(hass, "entry1")
        await store.async_load()
        stale = make_document(now - timedelta(hours=3), valid_for=60)
        await store.async_set_intent(MAC, stale, now.isoformat())

        control = await control_factory()

        assert control.intent is None
        assert control.store.stored_intent(MAC) is None

    @pytest.mark.asyncio
    async def test_disabled_reads_nothing(self, hass, control_factory, now):
        _publish(hass, make_document(now))
        store = ControlStore(hass, "entry1")
        await store.async_load()
        await store.async_set_intent(MAC, make_document(now), now.isoformat())

        control = await control_factory(
            **{CONF_CONTROL_MODE: CONTROL_MODE_DISABLED}
        )

        assert control.intent is None
        assert control.store.stored_intent(MAC) is None
        assert control.heartbeat is not None
        assert control.capabilities.mode == CONTROL_MODE_DISABLED

        _publish(hass, make_document(now, intent_id="i-2"))
        await hass.async_block_till_done()
        assert control.intent is None


class TestIntake:
    @pytest.mark.asyncio
    async def test_a_new_state_is_a_new_intent(
        self, hass, control_factory, now
    ):
        control = await control_factory()
        seen: list[str] = []
        control.async_add_listener(lambda: seen.append(control.ack.state))

        _publish(
            hass, make_document(now, [directive(now, "charge", target_f=130)])
        )
        await hass.async_block_till_done()

        assert control.intent is not None
        assert control.ack.state == STATUS_SHADOW
        assert seen[-1] == STATUS_SHADOW
        assert (
            control.store.stored_intent(MAC)["document"]["intent_id"] == "i-1"
        )

    @pytest.mark.asyncio
    async def test_attribute_only_change_is_ignored(
        self, hass, control_factory, now
    ):
        _publish(hass, make_document(now))
        control = await control_factory()

        _publish(hass, make_document(now, plan_id="changed"))
        await hass.async_block_till_done()

        assert control.intent is not None
        assert control.intent.extra == {}

    @pytest.mark.asyncio
    async def test_source_going_unavailable_keeps_the_intent(
        self, hass, control_factory, now
    ):
        _publish(hass, make_document(now))
        control = await control_factory()

        hass.states.async_set(INTENT_ENTITY, STATE_UNAVAILABLE)
        await hass.async_block_till_done()

        assert control.intent is not None

    @pytest.mark.asyncio
    async def test_a_rejected_document_leaves_the_intent_in_force(
        self, hass, control_factory, now
    ):
        _publish(hass, make_document(now))
        control = await control_factory()

        _publish(hass, make_document(now, intent_id="i-2", protocol="9"))
        await hass.async_block_till_done()

        assert control.intent is not None
        assert control.intent.intent_id == "i-1"
        assert control.ack.state == STATUS_REJECTED
        assert control.ack.intent_id == "i-2"
        assert control.ack.reason == REASON_UNSUPPORTED_PROTOCOL
        assert (
            control.store.stored_intent(MAC)["document"]["intent_id"] == "i-1"
        )

    @pytest.mark.asyncio
    async def test_a_new_intent_replaces_the_old(
        self, hass, control_factory, now
    ):
        _publish(hass, make_document(now))
        control = await control_factory()

        _publish(hass, make_document(now, intent_id="i-2", valid_for=30))
        await hass.async_block_till_done()

        assert control.intent.intent_id == "i-2"
        assert (
            control.store.stored_intent(MAC)["document"]["intent_id"] == "i-2"
        )


class TestStaleness:
    @pytest.mark.asyncio
    async def test_the_intent_goes_stale_at_valid_until(
        self, hass, control_factory, now, freezer
    ):
        freezer.move_to(now)
        _publish(hass, make_document(now, valid_for=30))
        control = await control_factory()
        assert control.intent is not None

        later = now + timedelta(minutes=31)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

        assert control.intent is None
        assert control.ack.state == STATUS_NONE
        assert control.store.stored_intent(MAC) is None

    @pytest.mark.asyncio
    async def test_a_replacement_cancels_the_old_expiry(
        self, hass, control_factory, now, freezer
    ):
        freezer.move_to(now)
        _publish(hass, make_document(now, valid_for=30))
        control = await control_factory()
        _publish(hass, make_document(now, intent_id="i-2", valid_for=90))
        await hass.async_block_till_done()

        later = now + timedelta(minutes=31)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

        assert control.intent is not None
        assert control.intent.intent_id == "i-2"

    @pytest.mark.asyncio
    async def test_heartbeat_ticks_and_catches_staleness(
        self, hass, control_factory, now, freezer
    ):
        freezer.move_to(now)
        control = await control_factory()
        first = control.heartbeat

        later = now + HEARTBEAT_INTERVAL + timedelta(seconds=1)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

        assert control.heartbeat is not None
        assert control.heartbeat > first

    @pytest.mark.asyncio
    async def test_stop_cancels_the_timers(
        self, hass, control_factory, now, freezer
    ):
        freezer.move_to(now)
        _publish(hass, make_document(now, valid_for=30))
        control = await control_factory()
        await control.async_stop()

        later = now + timedelta(hours=1)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

        # Nothing ran: the intent object was left as it was.
        assert control.intent is not None
        _publish(hass, make_document(now, intent_id="i-2", valid_for=200))
        await hass.async_block_till_done()
        assert control.intent.intent_id == "i-1"


class TestCapabilities:
    @pytest.mark.asyncio
    async def test_telemetry_resolves_registered_entities(
        self, hass, control_factory, entity_registry: er.EntityRegistry
    ):
        upper = entity_registry.async_get_or_create(
            "sensor", DOMAIN, f"{MAC}_tank_upper_temperature"
        )
        compressor = entity_registry.async_get_or_create(
            "binary_sensor", DOMAIN, f"{MAC}_comp_use"
        )
        control = await control_factory()

        telemetry = control.capabilities.as_attributes()["telemetry"]

        assert telemetry["delivery_temperature"] == upper.entity_id
        assert telemetry["compressor_running"] == compressor.entity_id
        assert telemetry["power"] is None

    @pytest.mark.asyncio
    async def test_bounds_come_from_the_device(self, control_factory):
        control = await control_factory()
        assert control.capabilities.setpoint_min_raw == 81

    @pytest.mark.asyncio
    async def test_coordinator_updates_notify_listeners(self, control_factory):
        control = await control_factory()
        listener = control.coordinator.async_add_listener.call_args.args[0]
        calls: list[int] = []
        control.async_add_listener(lambda: calls.append(1))

        listener()

        assert calls == [1]

    @pytest.mark.asyncio
    async def test_removing_a_listener(self, control_factory):
        control = await control_factory()
        calls: list[int] = []
        remove = control.async_add_listener(lambda: calls.append(1))
        remove()
        control._notify()
        assert calls == []
