"""The feature's entities, and the platform hooks that add them."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_bytes
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
    MAX_CONTROL_RESERVATION_ENTRY_LIMIT,
)
from custom_components.nwp500.control import ENTITY_KEYS
from custom_components.nwp500.control.binary_sensor import (
    BINARY_SENSOR_KEYS,
    ControlInSyncBinarySensor,
    ControlOverrideBinarySensor,
)
from custom_components.nwp500.control.button import (
    ControlDisableButton,
    create_control_buttons,
)
from custom_components.nwp500.control.engine import (
    Report,
    State,
    Write,
)
from custom_components.nwp500.control.entries import OwnedEntry
from custom_components.nwp500.control.evaluate import NO_ACK, Ack, ItemAck
from custom_components.nwp500.control.intent import ITEM_ID_MAX_LENGTH
from custom_components.nwp500.control.sensor import (
    ATTRIBUTE_BUDGET,
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


def _override_schema() -> Draft202012Validator:
    schema = json.loads(
        Path("docs/external-control-protocol-1.schema.json").read_text()
    )
    return Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": "#/$defs/override_attributes"}
    )


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
    control.started_at = WHEN
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
        # The segment in force on the heater, not the plan's alone.
        control.planner.segment_in_force.return_value = "s1"
        assert setpoint.extra_state_attributes == {
            "segment": "s1",
        }
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
        assert (
            attrs["added_count"],
            attrs["removed_count"],
            attrs["truncated"],
        ) == (1, 0, False)
        # WHEN is a Sunday: weekday bit 128 (section 4.2).
        assert attrs["added"] == [
            {
                "kind": "plan",
                "owner": "plan",
                "serves": "s2",
                "fires_at": WHEN.isoformat(),
                "mode": "heat_pump",
                "setpoint_raw": 120,
                "setpoint_f": 140.0,
                "setpoint_c": 60.0,
                "enabled": True,
                "week": 128,
                "hour": 12,
                "min": 0,
            }
        ]

    @pytest.mark.parametrize(
        ("count", "added", "removed"), [(32, True, False), (64, False, False)]
    )
    def test_last_write_stays_within_the_recorder_limit(
        self, control, count, added, removed
    ):
        """Too large, the lists go, `removed` first; the counts stay."""
        entries = tuple(
            OwnedEntry(
                "plan",
                f"segment-{i:04d}-{'x' * 30}",
                WHEN + timedelta(minutes=i),
                "energy_saver",
                114,
            )
            for i in range(count)
        )
        control.last_write = Write("plan", WHEN, entries, entries, entries)
        attrs = ControlLastWriteSensor(
            control, "last_write"
        ).extra_state_attributes
        assert len(json_bytes(attrs)) <= ATTRIBUTE_BUDGET
        assert attrs["truncated"] is True
        assert (attrs["added"] is not None) is added
        assert (attrs["removed"] is not None) is removed
        assert (attrs["added_count"], attrs["removed_count"]) == (count, count)

    def test_a_full_program_stays_within_the_recorder_limit(self):
        """A full program needs no truncation (section 4.2).

        It lists every entry, with the longest ids and the largest limit.
        """
        entries = [
            {
                **entry.as_attributes(),
                **entry.as_entry(),
                "mode_name": entry.mode,
            }
            for entry in (
                OwnedEntry(
                    "precedence_exit",
                    f"{i:02d}".ljust(ITEM_ID_MAX_LENGTH, "x"),
                    WHEN + timedelta(hours=i),
                    "energy_saver",
                    114,
                    enabled=False,
                )
                for i in range(MAX_CONTROL_RESERVATION_ENTRY_LIMIT)
            )
        ]
        attrs = {"entry_count": len(entries), "entries": entries}
        assert len(json_bytes(attrs)) <= ATTRIBUTE_BUDGET

    def test_heartbeat(self, control):
        sensor = ControlHeartbeatSensor(control, "heartbeat")
        assert sensor.native_value == WHEN

    def test_no_device_attributes_leak(self, control):
        """Only when the feature started, nothing of the device's."""
        sensor = ControlHeartbeatSensor(control, "heartbeat")
        assert sensor.extra_state_attributes == {"started_at": WHEN.isoformat()}

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
        assert (attrs["report_count"], attrs["truncated"]) == (1, False)
        _override_schema().validate(attrs)
        # Off, nothing is kept (section 4.2).
        control.reports = {}
        _override_schema().validate(sensor.extra_state_attributes)
        assert sensor.extra_state_attributes == {
            "field": None,
            "value": None,
            "detected_at": None,
            "segment": None,
            "reports": [],
            "report_count": 0,
            "truncated": False,
        }

    def test_every_kind_of_override_report_fits_the_schema(self, control):
        entry = OwnedEntry("guard", "g1", WHEN, "heat_pump", 120)
        added = {"enable": 2, "week": 2, "hour": 7, "min": 0}
        added |= {"mode": 1, "param": 100}
        reports = [
            Report("setpoint", 100, WHEN, "s1"),
            Report("mode", "vacation", WHEN, None),
            Report("removed", entry.as_attributes(), WHEN, "g1"),
            Report("reservations_switched_off", False, WHEN, None),
        ]
        control.reports = {str(i): r for i, r in enumerate(reports)}
        sensor = ControlOverrideBinarySensor(control, "override")
        attrs = sensor.extra_state_attributes
        _override_schema().validate(attrs)
        # A mode that is not one is not a report.
        broken = [{**attrs["reports"][1], "value": "not_a_mode"}]
        for report in broken:
            assert not _override_schema().is_valid(
                {**attrs, "reports": [report]}
            )

    def test_override_stays_within_the_recorder_limit(self, control):
        """Too large, the oldest reports go; the latest and count stay."""
        entries = [
            OwnedEntry(
                "plan",
                f"{i:02d}".ljust(ITEM_ID_MAX_LENGTH, "x"),
                WHEN + timedelta(minutes=i),
                "energy_saver",
                114,
            )
            for i in range(64)
        ]
        control.reports = {
            f"removed:plan:{e.serves}": Report(
                "removed", e.as_attributes(), e.fires_at, e.serves
            )
            for e in entries
        }
        sensor = ControlOverrideBinarySensor(control, "override")
        attrs = sensor.extra_state_attributes
        assert len(json_bytes(attrs)) <= ATTRIBUTE_BUDGET
        assert attrs["truncated"] is True
        assert attrs["report_count"] == 64
        assert 0 < len(attrs["reports"]) < 64
        # The newest are kept, oldest first.
        assert attrs["reports"][-1]["segment"] == entries[-1].serves
        assert attrs["segment"] == entries[-1].serves
        _override_schema().validate(attrs)


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


def test_every_entity_suggests_its_documented_id(control):
    """Spec section 4: `<domain>.<device>_control_<key>`, by key."""
    from homeassistant.util import slugify

    from custom_components.nwp500.control.button import (
        ControlDisableButton,
    )

    control.device.device_info.device_name = "Garage Heater"
    entities = [
        *((cls(control, key), "sensor", key) for key, cls in SENSOR_KEYS),
        *(
            (cls(control, key), "binary_sensor", key)
            for key, cls in BINARY_SENSOR_KEYS
        ),
        (ControlDisableButton(control, "disable"), "button", "disable"),
    ]
    for entity, domain, key in entities:
        assert entity.entity_id == (
            f"{domain}.{slugify('Garage Heater')}_control_{key}"
        )
    assert {e.entity_id for e, _, _ in entities} >= {
        "sensor.garage_heater_control_ack",
        "sensor.garage_heater_control_intent",
        "binary_sensor.garage_heater_control_override",
        "button.garage_heater_control_disable",
    }


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


class TestDisplayLabels:
    """Labels translate the protocol's raw values; they never replace them.

    A scheduler reads the raw states and attribute values (spec section 4),
    so every label must name a value the feature really produces.
    """

    @staticmethod
    def _strings(name: str) -> dict:
        path = Path(__file__).parents[3] / "custom_components" / "nwp500" / name
        return json.loads(path.read_text())

    @pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
    def test_states_labelled_are_the_raw_ones(self, name):
        from custom_components.nwp500.const import CONTROL_MODE_NAMES
        from custom_components.nwp500.control import engine, entries, evaluate

        entity = self._strings(name)["entity"]
        ack_states = {
            getattr(evaluate, n)
            for n in dir(evaluate)
            if n.startswith("STATE_")
        }
        kinds = {
            getattr(entries, n) for n in dir(entries) if n.startswith("KIND_")
        }
        reasons = {
            getattr(engine, n)
            for n in dir(engine)
            if n.startswith("WRITE_") and isinstance(getattr(engine, n), str)
        }
        sensor = entity["sensor"]
        assert set(sensor["control_ack"]["state"]) == ack_states
        assert set(sensor["control_intent"]["state"]) == {evaluate.STATE_NONE}
        assert set(sensor["control_wanted_mode"]["state"]) == set(
            CONTROL_MODE_NAMES
        )
        next_entry = sensor["control_next_entry"]["state_attributes"]
        assert set(next_entry["kind"]["state"]) == kinds
        assert set(next_entry["mode"]["state"]) == set(CONTROL_MODE_NAMES)
        last_write = sensor["control_last_write"]["state_attributes"]
        assert set(last_write["reason"]["state"]) == reasons

    @pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
    def test_every_control_entity_is_named(self, name):
        from custom_components.nwp500.control import ENTITY_KEYS

        entity = self._strings(name)["entity"]
        named = {
            key.removeprefix("control_")
            for domain in ("sensor", "binary_sensor", "button")
            for key, value in entity[domain].items()
            if key.startswith("control_") and value.get("name")
        }
        assert named == ENTITY_KEYS

    def test_the_mode_choices_are_labelled(self):
        from custom_components.nwp500.const import (
            CONTROL_MODE_DISABLED,
            CONTROL_MODE_LIVE,
            CONTROL_MODE_SHADOW,
        )

        labels = self._strings("strings.json")["selector"]["control_mode"]
        assert set(labels["options"]) == {
            CONTROL_MODE_SHADOW,
            CONTROL_MODE_LIVE,
            CONTROL_MODE_DISABLED,
        }

    def test_technical_entities_are_diagnostic(self, control):
        """Kept enabled, so a scheduler can still read them."""
        from homeassistant.const import EntityCategory

        feature = MagicMock()
        feature.devices = {MAC: control}
        diagnostic = {
            e.unique_id.split("_control_", 1)[1]
            for e in create_control_sensors(feature)
            if e.entity_category == EntityCategory.DIAGNOSTIC
        }
        assert diagnostic == {
            "capabilities",
            "program_hash",
            "last_write",
            "heartbeat",
        }
        assert all(
            e.entity_registry_enabled_default
            for e in create_control_sensors(feature)
        )
