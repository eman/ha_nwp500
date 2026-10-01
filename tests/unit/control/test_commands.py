"""Device commands in the plan (protocol 1.3, spec section 3.7, #196).

Each command is applied once, as sent, and reported from what the heater
reports. Nothing here reaches a real heater: live tests send through the
`FakeHeater` of `test_live`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from custom_components.nwp500.control.commands import (
    STATUS_APPLIED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SHADOW,
    Commands,
)
from custom_components.nwp500.control.device import DeviceControl
from custom_components.nwp500.control.evaluate import (
    REASON_NOT_APPLIED,
    REASON_WRITE_NOT_CONFIRMED,
)
from custom_components.nwp500.control.intent import (
    REASON_DUPLICATE_ID,
    REASON_INVALID_COMMAND,
    REASON_INVALID_DOCUMENT,
    REASON_UNSUPPORTED_COMMAND,
    IntentRejected,
    parse_plan,
)
from custom_components.nwp500.control.observed import Observed
from custom_components.nwp500.control.store import ControlStore

from . import test_live
from .conftest import make_document, segment
from .test_device import MAC, _publish
from .test_engine import NOW
from .test_live import FakeHeater, _tick

WINDOW = timedelta(seconds=90)

# The live tests' fixtures, used by name.
live_gate = test_live.live_gate
live_factory = test_live.live_factory


def _doc(*commands: dict[str, Any], base=NOW, **kwargs: Any) -> dict[str, Any]:
    return make_document(
        base,
        [segment(base, "s", -5, mode="heat_pump", setpoint_f=140)],
        commands=list(commands),
        **kwargs,
    )


def _live_doc(now, *commands: dict[str, Any], **kwargs: Any):
    return _doc(*commands, base=now, **kwargs)


def _commands(*commands: dict[str, Any]):
    return parse_plan(_doc(*commands)).commands


def _statuses(runner: Commands) -> dict[str, tuple[str, str | None]]:
    return {i.id: (i.status, i.reason) for i in runner.ack()}


# -- parsing --------------------------------------------------------------


class TestParsing:
    def test_each_command_and_its_keys(self):
        commands = _commands(
            {"id": "v", "command": "vacation", "days": 5},
            {"id": "p", "command": "power", "on": False},
            {
                "id": "a",
                "command": "anti_legionella",
                "enabled": True,
                "period_days": 7,
            },
            {"id": "b", "command": "anti_legionella", "enabled": False},
            {"id": "t", "command": "tou", "enabled": True},
            {"id": "d", "command": "demand_response", "enabled": False},
        )
        assert [(c.id, c.command, c.params, c.rejection) for c in commands] == [
            ("v", "vacation", {"days": 5}, None),
            ("p", "power", {"on": False}, None),
            ("a", "anti_legionella", {"enabled": True, "period_days": 7}, None),
            ("b", "anti_legionella", {"enabled": False}, None),
            ("t", "tou", {"enabled": True}, None),
            ("d", "demand_response", {"enabled": False}, None),
        ]

    def test_no_commands_is_none(self):
        doc = _doc()
        del doc["commands"]
        assert parse_plan(doc).commands == ()

    def test_opaque_keys_are_kept_apart(self):
        (command,) = _commands(
            {"id": "v", "command": "vacation", "days": 5, "why": "trip"}
        )
        assert command.params == {"days": 5}
        assert command.extra == {"why": "trip"}

    @pytest.mark.parametrize(
        "raw",
        [
            {"id": "x", "command": "vacation"},
            {"id": "x", "command": "vacation", "days": "5"},
            {"id": "x", "command": "vacation", "days": True},
            {"id": "x", "command": "vacation", "days": 5.0},
            {"id": "x", "command": "power", "on": 1},
            {"id": "x", "command": "anti_legionella", "enabled": True},
            {
                "id": "x",
                "command": "anti_legionella",
                "enabled": False,
                "period_days": 7,
            },
            {"id": "x", "command": 3},
            {"id": "x"},
        ],
    )
    def test_a_malformed_command_is_rejected_on_its_own(self, raw):
        """The plan proceeds: a bad command never costs the heater its plan."""
        plan = parse_plan(_doc(raw))
        assert [s.id for s in plan.segments] == ["s"]
        (command,) = plan.commands
        assert command.rejection == REASON_INVALID_COMMAND
        assert command.detail

    def test_an_unknown_command_is_rejected_on_its_own(self):
        (command,) = _commands({"id": "r", "command": "recirculation"})
        assert command.rejection == REASON_UNSUPPORTED_COMMAND

    @pytest.mark.parametrize(
        "commands",
        [
            {"id": "x"},
            [5],
            [{"command": "tou", "enabled": True}],
            [{"id": "", "command": "tou", "enabled": True}],
        ],
    )
    def test_the_list_shape_and_ids_reject_the_document(self, commands):
        doc = _doc()
        doc["commands"] = commands
        with pytest.raises(IntentRejected) as err:
            parse_plan(doc)
        assert err.value.reason == REASON_INVALID_DOCUMENT

    def test_an_id_shared_with_a_segment_is_a_duplicate(self):
        with pytest.raises(IntentRejected) as err:
            parse_plan(_doc({"id": "s", "command": "tou", "enabled": True}))
        assert err.value.reason == REASON_DUPLICATE_ID

    def test_stored_and_parsed_again_unchanged(self):
        raw = {"id": "v", "command": "vacation", "days": 5, "why": "trip"}
        plan = parse_plan(_doc(raw))
        again = parse_plan(plan.as_document())
        assert again.commands == plan.commands
        assert plan.as_document()["commands"] == [raw]


# -- what becomes of each command ----------------------------------------


class TestCommands:
    def test_shadow_sends_nothing(self):
        runner = Commands(WINDOW)
        runner.set_plan(
            _commands({"id": "t", "command": "tou", "enabled": True}),
            writes=False,
        )
        assert runner.to_send() == []
        assert _statuses(runner) == {"t": (STATUS_SHADOW, None)}

    def test_applied_once_the_heater_reports_it(self):
        runner = Commands(WINDOW)
        runner.set_plan(
            _commands({"id": "t", "command": "tou", "enabled": True}),
            writes=True,
        )
        (command,) = runner.to_send()
        runner.sending(command, NOW)
        assert runner.to_send() == []
        assert runner.check(NOW, Observed(tou_on=False)) is False
        assert _statuses(runner) == {"t": (STATUS_PENDING, None)}
        assert runner.check(NOW, Observed(tou_on=True)) is True
        assert _statuses(runner) == {"t": (STATUS_APPLIED, None)}

    def test_a_status_reports_the_application_only(self):
        """A person changing the setting later does not change it."""
        runner = Commands(WINDOW)
        runner.set_plan(
            _commands({"id": "t", "command": "tou", "enabled": True}),
            writes=True,
        )
        runner.sending(runner.to_send()[0], NOW)
        runner.check(NOW, Observed(tou_on=True))
        runner.check(NOW + WINDOW * 2, Observed(tou_on=False))
        assert _statuses(runner) == {"t": (STATUS_APPLIED, None)}

    def test_failed_when_the_heater_does_not_report_it(self):
        runner = Commands(WINDOW)
        runner.set_plan(
            _commands({"id": "v", "command": "vacation", "days": 5}),
            writes=True,
        )
        runner.sending(runner.to_send()[0], NOW)
        assert runner.next_deadline == NOW + WINDOW
        runner.check(NOW + WINDOW, Observed(mode="heat_pump"))
        assert _statuses(runner) == {"v": (STATUS_FAILED, REASON_NOT_APPLIED)}

    def test_demand_response_is_applied_when_sent(self):
        """The heater reports utility events, not whether it takes part."""
        runner = Commands(WINDOW)
        runner.set_plan(
            _commands(
                {"id": "d", "command": "demand_response", "enabled": True}
            ),
            writes=True,
        )
        runner.sending(runner.to_send()[0], NOW)
        runner.check(NOW, Observed())
        assert _statuses(runner) == {"d": (STATUS_APPLIED, None)}

    @pytest.mark.parametrize(
        ("raw", "observed", "applied"),
        [
            (
                {"command": "vacation", "days": 5},
                Observed(mode="vacation", vacation_days=5),
                True,
            ),
            (
                {"command": "vacation", "days": 5},
                Observed(mode="vacation", vacation_days=3),
                False,
            ),
            (
                {"command": "power", "on": False},
                Observed(mode="power_off"),
                True,
            ),
            (
                {"command": "power", "on": True},
                Observed(mode="power_off"),
                False,
            ),
            ({"command": "power", "on": True}, Observed(mode="vacation"), True),
            (
                {
                    "command": "anti_legionella",
                    "enabled": True,
                    "period_days": 7,
                },
                Observed(anti_legionella_on=True, anti_legionella_period=7),
                True,
            ),
            (
                {
                    "command": "anti_legionella",
                    "enabled": True,
                    "period_days": 7,
                },
                Observed(anti_legionella_on=True, anti_legionella_period=14),
                False,
            ),
            (
                {"command": "anti_legionella", "enabled": False},
                Observed(anti_legionella_on=False),
                True,
            ),
            (
                {"command": "anti_legionella", "enabled": False},
                Observed(),
                False,
            ),
        ],
    )
    def test_read_back(self, raw, observed, applied):
        runner = Commands(WINDOW)
        runner.set_plan(_commands({"id": "c", **raw}), writes=True)
        runner.sending(runner.to_send()[0], NOW)
        runner.check(NOW, observed)
        assert _statuses(runner)["c"][0] == (
            STATUS_APPLIED if applied else STATUS_PENDING
        )

    def test_the_same_command_is_not_applied_again(self):
        runner = Commands(WINDOW)
        tou = {"id": "t", "command": "tou", "enabled": True}
        runner.set_plan(_commands(tou), writes=True)
        runner.sending(runner.to_send()[0], NOW)
        runner.check(NOW, Observed(tou_on=True))

        runner.set_plan(_commands({**tou, "note": "new plan"}), writes=True)
        assert runner.to_send() == []
        assert _statuses(runner) == {"t": (STATUS_APPLIED, None)}

    def test_changed_content_is_applied_again(self):
        runner = Commands(WINDOW)
        runner.set_plan(
            _commands({"id": "t", "command": "tou", "enabled": True}),
            writes=True,
        )
        runner.sending(runner.to_send()[0], NOW)
        runner.set_plan(
            _commands({"id": "t", "command": "tou", "enabled": False}),
            writes=True,
        )
        assert [c.id for c in runner.to_send()] == ["t"]

    def test_going_live_applies_what_shadow_evaluated(self):
        runner = Commands(WINDOW)
        commands = _commands({"id": "t", "command": "tou", "enabled": True})
        runner.set_plan(commands, writes=False)
        runner.set_plan(commands, writes=True)
        assert [c.id for c in runner.to_send()] == ["t"]

    def test_a_rejected_command_is_reported_and_never_sent(self):
        runner = Commands(WINDOW)
        runner.set_plan(
            _commands({"id": "r", "command": "recirculation"}), writes=True
        )
        assert runner.to_send() == []
        (item,) = runner.ack()
        assert (item.status, item.reason) == (
            STATUS_REJECTED,
            REASON_UNSUPPORTED_COMMAND,
        )
        assert item.as_attribute()["command"] == "recirculation"
        assert "detail" in item.as_attribute()

    def test_kept_across_a_restart(self):
        runner = Commands(WINDOW)
        commands = _commands({"id": "t", "command": "tou", "enabled": True})
        runner.set_plan(commands, writes=True)
        runner.sending(runner.to_send()[0], NOW)

        restored = Commands(WINDOW)
        restored.load_document(runner.as_document())
        restored.set_plan(commands, writes=True)
        assert restored.to_send() == []
        assert restored.next_deadline == NOW + WINDOW


# -- live, against a simulated heater ------------------------------------


class CommandHeater(FakeHeater):
    """A heater that takes device commands, or refuses them."""

    # The library's error for every command, if set.
    refuse: str | None = None

    def __init__(self, coordinator, schedule) -> None:
        super().__init__(coordinator, schedule)
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def async_send_command(self, command) -> None:
        self.commands.append((command.command, dict(command.params)))
        if self.refuse is not None:
            raise ValueError(self.refuse)
        status = self.coordinator.data[MAC]["status"]
        if command.command == "tou":
            status.tou_status = command.params["enabled"]


@pytest.fixture
def command_heater(monkeypatch):
    """Make `live_factory` build a heater that takes commands."""
    monkeypatch.setattr(test_live, "FakeHeater", CommandHeater)


def _ack_commands(control: DeviceControl) -> list[dict[str, Any]]:
    return control.ack.as_attributes()["commands"]


class TestLive:
    @pytest.mark.asyncio
    async def test_sent_once_and_reported(
        self, hass, command_heater, live_factory, now
    ):
        _publish(
            hass,
            _live_doc(
                now,
                {"id": "t", "command": "tou", "enabled": True, "why": "cheap"},
            ),
        )
        heater, control = await live_factory()

        assert heater.commands == [("tou", {"enabled": True})]
        assert _ack_commands(control) == [
            {
                "why": "cheap",
                "id": "t",
                "status": "applied",
                "reason": None,
                "warnings": [],
                "command": "tou",
            }
        ]
        # The declaration says the feature honours them.
        assert control.capabilities.as_attributes()["protocol_versions"] == [
            "1.3",
            "0",
        ]

        # A new plan with the same command does not send it again.
        _publish(
            hass,
            _doc(
                {"id": "t", "command": "tou", "enabled": True},
                intent_id="i-2",
                issued_at=now + timedelta(minutes=1),
            ),
        )
        await hass.async_block_till_done()
        assert control.plan.intent_id == "i-2"
        assert heater.commands == [("tou", {"enabled": True})]

    @pytest.mark.asyncio
    async def test_a_library_refusal_is_reported_with_its_reason(
        self, hass, command_heater, live_factory, monkeypatch, now
    ):
        monkeypatch.setattr(CommandHeater, "refuse", "days must be 1-30")
        _publish(
            hass, _live_doc(now, {"id": "v", "command": "vacation", "days": 45})
        )
        _, control = await live_factory()

        (item,) = _ack_commands(control)
        assert (item["status"], item["reason"], item["detail"]) == (
            "failed",
            REASON_WRITE_NOT_CONFIRMED,
            "days must be 1-30",
        )
        # The plan's segments are unaffected.
        assert control.plan.segments[0].id == "s"

    @pytest.mark.asyncio
    async def test_failed_when_the_heater_does_not_report_it(
        self, hass, command_heater, live_factory, freezer, now
    ):
        _publish(
            hass, _live_doc(now, {"id": "v", "command": "vacation", "days": 5})
        )
        heater, control = await live_factory()
        heater.coordinator.data[MAC]["status"].vacation_day_setting = 0
        assert _ack_commands(control)[0]["status"] == "pending"

        # The controller wakes a second after the deadline.
        await _tick(
            hass, freezer, control.commands.window + timedelta(seconds=2)
        )

        (item,) = _ack_commands(control)
        assert (item["status"], item["reason"]) == (
            "failed",
            REASON_NOT_APPLIED,
        )
        assert heater.commands == [("vacation", {"days": 5})]

    @pytest.mark.asyncio
    async def test_a_restart_does_not_send_it_again(
        self, hass, command_heater, live_factory, now
    ):
        _publish(
            hass, _live_doc(now, {"id": "t", "command": "tou", "enabled": True})
        )
        heater, control = await live_factory()
        assert len(heater.commands) == 1
        await control.async_stop()

        store = ControlStore(hass, control.entry.entry_id)
        await store.async_load()
        again = DeviceControl(
            hass,
            control.entry,
            heater.coordinator,
            MAC,
            control.device,
            store,
            writer=heater,
        )
        await again.async_start()
        await hass.async_block_till_done()
        try:
            assert len(heater.commands) == 1
            assert _ack_commands(again)[0]["status"] == "applied"
        finally:
            await again.async_stop()

    @pytest.mark.asyncio
    async def test_shadow_sends_nothing(
        self, hass, command_heater, live_factory, now
    ):
        _publish(
            hass, _live_doc(now, {"id": "t", "command": "tou", "enabled": True})
        )
        heater, control = await live_factory(control_mode="shadow")

        assert heater.commands == []
        assert _ack_commands(control)[0]["status"] == "shadow"


# -- the schema -----------------------------------------------------------


def _schema():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    return Draft202012Validator(
        json.loads(
            Path("docs/external-control-protocol-1.schema.json").read_text()
        )
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"id": "v", "command": "vacation", "days": 5},
        {"id": "p", "command": "power", "on": True},
        {
            "id": "a",
            "command": "anti_legionella",
            "enabled": True,
            "period_days": 7,
        },
        {"id": "b", "command": "anti_legionella", "enabled": False},
        {"id": "t", "command": "tou", "enabled": False, "why": "x"},
        {"id": "d", "command": "demand_response", "enabled": True},
        {"id": "x", "command": "vacation"},
        {"id": "x", "command": "vacation", "days": "5"},
        {"id": "x", "command": "power", "on": 1},
        {"id": "x", "command": "anti_legionella", "enabled": True},
        {
            "id": "x",
            "command": "anti_legionella",
            "enabled": False,
            "period_days": 7,
        },
        {"id": "x", "command": "recirculation"},
    ],
)
def test_the_schema_accepts_what_the_feature_applies(raw):
    """Schema-valid exactly when the parser would apply the command."""
    document = _doc(raw)
    (command,) = parse_plan(document).commands
    assert _schema().is_valid(document) is (command.rejection is None)
