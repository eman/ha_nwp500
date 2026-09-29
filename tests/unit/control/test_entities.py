"""The feature's entities, and the platform hooks that add them."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from jsonschema import Draft202012Validator

from custom_components.nwp500 import binary_sensor as binary_platform
from custom_components.nwp500 import button as button_platform
from custom_components.nwp500 import sensor as sensor_platform
from custom_components.nwp500.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_DISABLED,
    DATA_CONTROL,
    DOMAIN,
)
from custom_components.nwp500.control import ENTITY_KEYS
from custom_components.nwp500.control.binary_sensor import (
    BINARY_SENSOR_KEYS,
    ControlGrantRaisedBinarySensor,
    ControlInSyncBinarySensor,
    ControlOverrideBinarySensor,
)
from custom_components.nwp500.control.button import (
    ControlDisableButton,
    create_control_buttons,
)
from custom_components.nwp500.control.engine import (
    RaiseState,
    Report,
    State,
    Write,
)
from custom_components.nwp500.control.entries import OwnedEntry
from custom_components.nwp500.control.evaluate import NO_ACK, Ack, ItemAck
from custom_components.nwp500.control.sensor import (
    SENSOR_KEYS,
    ControlAckSensor,
    ControlCapabilitiesSensor,
    ControlHeartbeatSensor,
    ControlIntentSensor,
    ControlLastWriteSensor,
    ControlNextEntrySensor,
    ControlProgramHashSensor,
    ControlProgrammedUntilSensor,
    ControlWantedModeSensor,
    ControlWantedSetpointSensor,
    create_control_sensors,
)

from .conftest import capabilities, make_document, segment

MAC = "AA:BB:CC:DD:EE:FF"
WHEN = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
LOCAL = ZoneInfo("America/Los_Angeles")


def _local(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 4, hour, minute, second, tzinfo=LOCAL)


# The writes behind docs/examples/last-write-*.json, for plan-day.json.
_EXAMPLE_WRITES = {
    "last-write-plan": Write(
        "plan",
        _local(5, 0, 12),
        added=(
            OwnedEntry("near_term", "s1", _local(5, 3), "heat_pump", 81),
            OwnedEntry("plan", "s2", _local(10, 30), "heat_pump", 120),
            OwnedEntry("plan", "s3", _local(14, 30), "energy_saver", 114),
            OwnedEntry("plan", "s4", _local(22, 0), "heat_pump", 81),
        ),
        removed=(),
        result=(),
        simulated=False,
        confirmed=True,
    ),
    "last-write-grant-raise": Write(
        "grant_raise",
        _local(11, 20, 5),
        added=(
            OwnedEntry("grant_raise", "g1", _local(11, 23), "heat_pump", 127),
            OwnedEntry("guard", "g1", _local(14, 0), "heat_pump", 120),
        ),
        removed=(
            OwnedEntry("near_term", "s1", _local(5, 3), "heat_pump", 81),
            OwnedEntry("plan", "s2", _local(10, 30), "heat_pump", 120),
        ),
        result=(),
        simulated=False,
        confirmed=True,
    ),
}


@pytest.fixture
def entry_(now):
    return OwnedEntry("plan", "s2", WHEN, "heat_pump", 120)


@pytest.fixture
def control(
    mock_coordinator, mock_device, mock_config_entry, now, parse, entry_
):
    """A controller as the entities see it."""
    control = MagicMock()
    control.coordinator = mock_coordinator
    control.mac_address = MAC
    control.device = mock_device
    control.entry = mock_config_entry
    control.capabilities = capabilities()
    control.plan = parse(
        make_document(
            now,
            [segment(now, "s1", 0, mode="heat_pump", setpoint_f=140)],
            plan_id="p",
        )
    )
    control.received_at = now
    control.ack = Ack(
        intent_id="i-1",
        state="shadow",
        segments=(ItemAck(id="s1", status="shadow"),),
    )
    control.heartbeat = WHEN
    control.wanted = State("heat_pump", 120)
    control.raise_state = None
    control.last_write = None
    control.reports = {}
    control.program_details = MagicMock(
        return_value={
            "hash": "abc",
            "entry_count": 1,
            "entries": [{"owner": "plan"}],
            "device_hash": "def",
            "read_at": WHEN,
        }
    )
    planner = MagicMock()
    planner.programmed_until = WHEN
    planner.programmed_complete = True
    planner.scheduled_count = 0
    planner.plan = control.plan
    planner.raise_state = None
    planner.next_entry = MagicMock(return_value=entry_)
    control.planner = planner
    control.async_add_listener = MagicMock(return_value=lambda: None)
    return control


class TestSensors:
    def test_capabilities(self, control):
        sensor = ControlCapabilitiesSensor(control, "capabilities")
        assert sensor.unique_id == f"{MAC}_control_capabilities"
        assert sensor.translation_key == "control_capabilities"
        assert sensor.native_value == capabilities().version
        assert sensor.extra_state_attributes["protocols"] == ["1", "0"]
        assert sensor.available is True

    def test_intent(self, control, now):
        sensor = ControlIntentSensor(control, "intent")
        assert sensor.native_value == "i-1"
        attrs = sensor.extra_state_attributes
        assert attrs["plan_id"] == "p"
        assert attrs["received_at"] == now.isoformat()
        assert attrs["segment_count"] == 1
        control.plan = None
        assert sensor.native_value == "none"

    def test_ack(self, control):
        sensor = ControlAckSensor(control, "ack")
        assert sensor.native_value == "shadow"
        assert sensor.extra_state_attributes["segments"][0]["id"] == "s1"
        control.ack = NO_ACK
        assert sensor.native_value == "none"

    def test_program_hash(self, control):
        sensor = ControlProgramHashSensor(control, "program_hash")
        assert sensor.native_value == "abc"
        assert sensor.extra_state_attributes == {
            "entry_count": 1,
            "entries": [{"owner": "plan"}],
        }

    def test_programmed_until(self, control):
        sensor = ControlProgrammedUntilSensor(control, "programmed_until")
        assert sensor.native_value == WHEN
        assert sensor.extra_state_attributes == {
            "complete": True,
            "scheduled": 0,
        }

    def test_next_entry(self, control):
        sensor = ControlNextEntrySensor(control, "next_entry")
        assert sensor.native_value == WHEN
        attrs = sensor.extra_state_attributes
        assert attrs["mode"] == "heat_pump"
        assert attrs["setpoint_f"] == 140.0
        assert attrs["serves"] == "s2"
        control.planner.next_entry.return_value = None
        assert sensor.native_value is None

    def test_wanted(self, control, hass):
        mode = ControlWantedModeSensor(control, "wanted_mode")
        assert mode.native_value == "heat_pump"
        setpoint = ControlWantedSetpointSensor(control, "wanted_setpoint")
        setpoint.hass = hass
        hass.config.units = US_CUSTOMARY_SYSTEM
        assert setpoint.native_value == 140.0
        hass.config.units = METRIC_SYSTEM
        assert setpoint.native_value == 60.0
        control.wanted = None
        assert mode.native_value is None
        assert setpoint.native_value is None

    def test_last_write(self, control, entry_):
        sensor = ControlLastWriteSensor(control, "last_write")
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {"reason": None}
        control.last_write = Write("plan", WHEN, (entry_,), (), (entry_,))
        assert sensor.native_value == WHEN
        attrs = sensor.extra_state_attributes
        assert attrs["reason"] == "plan"
        assert attrs["simulated"] is True
        assert attrs["confirmed"] is None
        assert attrs["removed"] == []
        # WHEN is a Sunday: weekday bit 128 (section 4.2).
        assert attrs["added"] == [
            {
                "kind": "plan",
                "owner": "plan",
                "serves": "s2",
                "fires_at": WHEN.isoformat(),
                "mode": "heat_pump",
                "mode_name": "heat_pump",
                "setpoint_raw": 120,
                "setpoint_f": 140.0,
                "setpoint_c": 60.0,
                "enabled": True,
                "week": 128,
                "hour": 12,
                "min": 0,
            }
        ]

    def test_last_write_guard_serves_its_grant(self, control):
        sensor = ControlLastWriteSensor(control, "last_write")
        guard = OwnedEntry("guard", "g1", WHEN, "heat_pump", 116)
        control.last_write = Write("grant_raise", WHEN, (guard,), (), (guard,))
        added = sensor.extra_state_attributes["added"][0]
        assert (added["kind"], added["owner"], added["serves"]) == (
            "guard",
            "guard",
            "g1",
        )
        assert added["setpoint_c"] == 58.0

    @pytest.mark.parametrize("name", sorted(_EXAMPLE_WRITES))
    def test_last_write_examples(self, control, name):
        """The docs' examples are what the entity reports, and fit the schema.

        Both follow docs/examples/plan-day.json, received at 05:00:12 local
        time, after its first segment had begun (spec section 4.2).
        """
        path = Path("docs/examples") / f"{name}.json"
        example = json.loads(path.read_text())
        control.last_write = _EXAMPLE_WRITES[name]
        sensor = ControlLastWriteSensor(control, "last_write")
        # A timestamp sensor's state is in UTC.
        at = sensor.native_value
        assert at is not None
        assert example["state"] == at.astimezone(UTC).isoformat()
        assert example["attributes"] == sensor.extra_state_attributes
        schema = json.loads(
            Path("docs/external-control-protocol-1.schema.json").read_text()
        )
        Draft202012Validator(
            {"$defs": schema["$defs"], "$ref": "#/$defs/last_write_attributes"}
        ).validate(example["attributes"])

    def test_heartbeat(self, control):
        sensor = ControlHeartbeatSensor(control, "heartbeat")
        assert sensor.native_value == WHEN

    def test_no_device_attributes_leak(self, control):
        sensor = ControlHeartbeatSensor(control, "heartbeat")
        assert sensor.extra_state_attributes in (None, {})

    @pytest.mark.asyncio
    async def test_follows_the_controller(self, hass: HomeAssistant, control):
        sensor = ControlAckSensor(control, "ack")
        sensor.hass = hass
        sensor.entity_id = "sensor.test_ack"
        sensor.platform = MagicMock()
        await sensor.async_added_to_hass()
        control.async_add_listener.assert_called_once_with(
            sensor.async_write_ha_state
        )


class TestBinarySensors:
    def test_in_sync(self, control):
        sensor = ControlInSyncBinarySensor(control, "in_sync")
        assert sensor.is_on is False
        assert sensor.extra_state_attributes == {
            "device_hash": "def",
            "read_at": WHEN.isoformat(),
        }
        control.program_details.return_value["device_hash"] = None
        assert sensor.is_on is None

    def test_grant_raised(self, control, entry_):
        sensor = ControlGrantRaisedBinarySensor(control, "grant_raised")
        assert sensor.is_on is False
        assert sensor.extra_state_attributes["grant"] is None
        control.raise_state = RaiseState("g1", WHEN, entry_, None)
        assert sensor.is_on is True
        assert sensor.extra_state_attributes["setpoint_f"] == 140.0

    def test_override(self, control):
        sensor = ControlOverrideBinarySensor(control, "override")
        assert sensor.is_on is False
        assert sensor.extra_state_attributes["reports"] == []
        control.reports = {
            "setpoint": Report("setpoint", 100, WHEN, "s1"),
        }
        assert sensor.is_on is True
        attrs = sensor.extra_state_attributes
        assert attrs["field"] == "setpoint"
        assert attrs["value"] == 100
        assert len(attrs["reports"]) == 1


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
        assert [e.unique_id for e in create_control_buttons(feature)] == [
            f"{MAC}_control_disable"
        ]


def test_every_entity_key_is_known():
    keys = (
        {k for k, _ in SENSOR_KEYS}
        | {k for k, _ in BINARY_SENSOR_KEYS}
        | {"disable"}
    )
    assert keys == ENTITY_KEYS


def test_create_control_sensors(control):
    feature = MagicMock()
    feature.devices = {MAC: control}
    entities = create_control_sensors(feature)
    assert [e.unique_id for e in entities] == [
        f"{MAC}_control_{key}" for key, _ in SENSOR_KEYS
    ]


class TestPlatformHooks:
    @pytest.mark.asyncio
    async def test_button_platform_without_the_feature(self, mock_config_entry):
        hass = MagicMock()
        hass.data = {}
        add_entities = MagicMock()
        await button_platform.async_setup_entry(
            hass, mock_config_entry, add_entities
        )
        add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_button_platform_with_the_feature(
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
    @pytest.mark.parametrize(
        ("platform", "keys"),
        [(sensor_platform, SENSOR_KEYS), (binary_platform, BINARY_SENSOR_KEYS)],
    )
    async def test_sensor_platforms_add_the_control_entities(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_config_entry,
        mock_device,
        mock_device_status,
        control,
        platform,
        keys,
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

        await platform.async_setup_entry(hass, mock_config_entry, add_entities)

        entities = add_entities.call_args.args[0]
        control_ids = [
            e.unique_id for e in entities if "_control_" in (e.unique_id or "")
        ]
        assert control_ids == [f"{MAC}_control_{key}" for key, _ in keys]
