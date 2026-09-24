"""The feature's entities, and the platform hooks that add them."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nwp500 import button as button_platform
from custom_components.nwp500 import sensor as sensor_platform
from custom_components.nwp500.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_DISABLED,
    DATA_CONTROL,
    DOMAIN,
)
from custom_components.nwp500.control.button import (
    ControlDisableButton,
    create_control_buttons,
)
from custom_components.nwp500.control.evaluate import NO_ACK, Ack, DirectiveAck
from custom_components.nwp500.control.sensor import (
    ControlAckSensor,
    ControlCapabilitiesSensor,
    ControlHeartbeatSensor,
    ControlIntentSensor,
    create_control_sensors,
)

from .conftest import capabilities, directive, make_document

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def control(mock_coordinator, mock_device, mock_config_entry, now, parse):
    """A controller with an accepted intent, as the entities see it."""
    control = MagicMock()
    control.coordinator = mock_coordinator
    control.mac_address = MAC
    control.device = mock_device
    control.entry = mock_config_entry
    control.capabilities = capabilities()
    control.intent = parse(
        make_document(
            now, [directive(now, "charge", target_f=130)], plan_id="p"
        )
    )
    control.received_at = now
    control.ack = Ack(
        intent_id="i-1",
        state="shadow",
        directives=(DirectiveAck(id="d1", type="charge", status="shadow"),),
    )
    control.heartbeat = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    control.async_add_listener = MagicMock(return_value=lambda: None)
    return control


class TestSensors:
    def test_capabilities(self, control):
        sensor = ControlCapabilitiesSensor(control, "capabilities")
        assert sensor.unique_id == f"{MAC}_control_capabilities"
        assert sensor.translation_key == "control_capabilities"
        assert sensor.native_value == capabilities().version
        assert sensor.extra_state_attributes["protocols"] == ["0"]
        assert sensor.available is True

    def test_intent(self, control, now):
        sensor = ControlIntentSensor(control, "intent")
        assert sensor.native_value == "i-1"
        attrs = sensor.extra_state_attributes
        assert attrs["plan_id"] == "p"
        assert attrs["issued_at"] == now.isoformat()
        assert attrs["received_at"] == now.isoformat()
        assert attrs["directive_count"] == 1

    def test_intent_none(self, control):
        control.intent = None
        sensor = ControlIntentSensor(control, "intent")
        assert sensor.native_value == "none"
        assert sensor.extra_state_attributes["valid_until"] is None

    def test_ack(self, control):
        sensor = ControlAckSensor(control, "ack")
        assert sensor.native_value == "shadow"
        assert sensor.extra_state_attributes["directives"][0]["id"] == "d1"
        control.ack = NO_ACK
        assert sensor.native_value == "none"

    def test_heartbeat(self, control):
        sensor = ControlHeartbeatSensor(control, "heartbeat")
        assert sensor.native_value == control.heartbeat
        assert sensor.device_class == "timestamp"

    def test_no_device_attributes_leak(self, control):
        """The base entity's device-info attributes are not these entities'."""
        control.coordinator.device_features = {MAC: MagicMock(volume_code=67)}
        sensor = ControlHeartbeatSensor(control, "heartbeat")
        assert sensor.extra_state_attributes in (None, {})
        button = ControlDisableButton(control, "disable")
        assert button.extra_state_attributes in (None, {})

    def test_create_control_sensors(self, control):
        feature = MagicMock()
        feature.devices = {MAC: control, "11:22:33:44:55:66": control}
        entities = create_control_sensors(feature)
        assert len(entities) == 16


class TestButton:
    @pytest.mark.asyncio
    async def test_press_sets_the_mode_to_disabled(self, control):
        entry = MagicMock()
        entry.options = {"control_enabled": True, CONF_CONTROL_MODE: "shadow"}
        control.entry = entry
        button = ControlDisableButton(control, "disable")
        button.hass = MagicMock()

        await button.async_press()

        button.hass.config_entries.async_update_entry.assert_called_once_with(
            entry,
            options={
                "control_enabled": True,
                CONF_CONTROL_MODE: CONTROL_MODE_DISABLED,
            },
        )

    def test_create_control_buttons(self, control):
        feature = MagicMock()
        feature.devices = {MAC: control}
        entities = create_control_buttons(feature)
        assert len(entities) == 1
        assert entities[0].unique_id == f"{MAC}_control_disable"


class TestPlatformHooks:
    @pytest.mark.asyncio
    async def test_button_platform_adds_nothing_without_the_feature(
        self, mock_config_entry
    ):
        hass = MagicMock()
        hass.data = {}
        add_entities = MagicMock()
        await button_platform.async_setup_entry(
            hass, mock_config_entry, add_entities
        )
        add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_button_platform_adds_the_disable_button(
        self, mock_config_entry, control
    ):
        hass = MagicMock()
        feature = MagicMock()
        feature.devices = {MAC: control}
        hass.data = {
            DOMAIN: {mock_config_entry.entry_id: {DATA_CONTROL: feature}}
        }
        add_entities = MagicMock()

        await button_platform.async_setup_entry(
            hass, mock_config_entry, add_entities
        )

        (entities,) = add_entities.call_args.args
        assert [e.unique_id for e in entities] == [f"{MAC}_control_disable"]

    @pytest.mark.asyncio
    async def test_sensor_platform_adds_the_control_sensors(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_config_entry,
        mock_device,
        mock_device_status,
        control,
    ):
        mock_coordinator.data = {
            MAC: {"device": mock_device, "status": mock_device_status}
        }
        mock_config_entry.runtime_data = mock_coordinator
        feature = MagicMock()
        feature.devices = {MAC: control}
        hass.data[DOMAIN] = {
            mock_config_entry.entry_id: {DATA_CONTROL: feature}
        }
        add_entities = MagicMock()

        await sensor_platform.async_setup_entry(
            hass, mock_config_entry, add_entities
        )

        entities = add_entities.call_args.args[0]
        control_ids = [
            e.unique_id for e in entities if "_control_" in (e.unique_id or "")
        ]
        assert control_ids == [
            f"{MAC}_control_capabilities",
            f"{MAC}_control_intent",
            f"{MAC}_control_ack",
            f"{MAC}_control_heartbeat",
            f"{MAC}_control_wanted_mode",
            f"{MAC}_control_wanted_setpoint",
            f"{MAC}_control_wanted_reservation_hash",
            f"{MAC}_control_last_restore",
        ]


class TestStateEntities:
    """The wanted, restore and override entities (spec section 4.2)."""

    @pytest.fixture
    def planned(self, control, now):
        from zoneinfo import ZoneInfo

        from custom_components.nwp500.control.engine import (
            Override,
            Restore,
            Wanted,
        )
        from custom_components.nwp500.control.entries import (
            ALL_DAYS,
            OwnedEntry,
        )

        entry = OwnedEntry(
            kind="daily_revert",
            directive_id=None,
            fires_at=now.astimezone(ZoneInfo("UTC")),
            mode="energy_saver",
            param=119,
            week=ALL_DAYS,
        )
        control.wanted = Wanted(
            mode="heat_pump",
            setpoint_raw=120,
            tou_on=False,
            schedule={"reservation_use": 2, "reservation": [entry.as_entry()]},
            entries=(entry,),
            holds=("compressor_min_run",),
            restore_pending="expiry",
            surplus_raised=True,
        )
        control.baseline = MagicMock(version="b1", provisional=True)
        control.last_restore = Restore(
            reason="expiry", at=now, matches_baseline=True
        )
        control.overrides = {
            "setpoint": Override("setpoint", 100, now, now),
        }
        return control

    def test_wanted_mode(self, planned):
        from custom_components.nwp500.control.sensor import (
            ControlWantedModeSensor,
        )

        sensor = ControlWantedModeSensor(planned, "wanted_mode")
        assert sensor.native_value == "heat_pump"
        attrs = sensor.extra_state_attributes
        assert attrs["holds"] == ["compressor_min_run"]
        assert attrs["restore_pending"] == "expiry"
        assert attrs["baseline_provisional"] is True

    def test_wanted_setpoint_in_the_configured_unit(self, planned, hass):
        from homeassistant.util.unit_system import (
            METRIC_SYSTEM,
            US_CUSTOMARY_SYSTEM,
        )

        from custom_components.nwp500.control.sensor import (
            ControlWantedSetpointSensor,
        )

        sensor = ControlWantedSetpointSensor(planned, "wanted_setpoint")
        sensor.hass = hass
        hass.config.units = US_CUSTOMARY_SYSTEM
        assert sensor.native_value == 140.0
        assert sensor.native_unit_of_measurement == "°F"
        hass.config.units = METRIC_SYSTEM
        assert sensor.native_value == 60.0
        assert sensor.extra_state_attributes["surplus_raised"] is True

    def test_wanted_reservation_hash(self, planned):
        from custom_components.nwp500.control.entries import schedule_hash
        from custom_components.nwp500.control.sensor import (
            ControlWantedReservationHashSensor,
        )

        sensor = ControlWantedReservationHashSensor(
            planned, "wanted_reservation_hash"
        )
        assert sensor.native_value == schedule_hash(planned.wanted.schedule)
        attrs = sensor.extra_state_attributes
        assert attrs["entry_count"] == 1
        assert attrs["enabled"] is True
        assert attrs["owned"][0]["kind"] == "daily_revert"

    def test_last_restore(self, planned, now):
        from custom_components.nwp500.control.sensor import (
            ControlLastRestoreSensor,
        )

        sensor = ControlLastRestoreSensor(planned, "last_restore")
        assert sensor.native_value == "expiry"
        assert sensor.extra_state_attributes["at"] == now.isoformat()
        assert sensor.extra_state_attributes["pending"] == "expiry"
        planned.last_restore = None
        assert sensor.native_value == "none"

    def test_binary_sensors(self, planned, now):
        from custom_components.nwp500.control.binary_sensor import (
            ControlOverrideBinarySensor,
            ControlRestoreMatchedBinarySensor,
            ControlWantedTouBinarySensor,
            create_control_binary_sensors,
        )

        assert (
            ControlWantedTouBinarySensor(planned, "wanted_tou").is_on is False
        )
        assert (
            ControlRestoreMatchedBinarySensor(planned, "restore_matched").is_on
            is True
        )
        override = ControlOverrideBinarySensor(planned, "override")
        assert override.is_on is True
        assert override.extra_state_attributes["field"] == "setpoint"
        assert override.extra_state_attributes["fields"] == ["setpoint"]
        planned.overrides = {}
        assert override.is_on is False
        assert override.extra_state_attributes["fields"] == []
        feature = MagicMock()
        feature.devices = {MAC: planned}
        assert len(create_control_binary_sensors(feature)) == 3

    @pytest.mark.asyncio
    async def test_binary_sensor_platform_adds_the_control_entities(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_config_entry,
        mock_device,
        mock_device_status,
        planned,
    ):
        from custom_components.nwp500 import binary_sensor as binary_platform

        mock_coordinator.data = {
            MAC: {"device": mock_device, "status": mock_device_status}
        }
        mock_config_entry.runtime_data = mock_coordinator
        feature = MagicMock()
        feature.devices = {MAC: planned}
        hass.data[DOMAIN] = {
            mock_config_entry.entry_id: {DATA_CONTROL: feature}
        }
        add_entities = MagicMock()

        await binary_platform.async_setup_entry(
            hass, mock_config_entry, add_entities
        )

        entities = add_entities.call_args.args[0]
        control_ids = [
            e.unique_id for e in entities if "_control_" in (e.unique_id or "")
        ]
        assert control_ids == [
            f"{MAC}_control_wanted_tou",
            f"{MAC}_control_restore_matched",
            f"{MAC}_control_override",
        ]
