"""Checks against the declaration (spec section 3.5)."""

from __future__ import annotations

import pytest

from custom_components.nwp500.control.capabilities import build_capabilities
from custom_components.nwp500.control.evaluate import (
    NO_ACK,
    ItemAck,
    check_plan,
    rejected_ack,
)
from custom_components.nwp500.control.intent import (
    REASON_MODE_NOT_ALLOWED,
    IntentRejected,
)

from .conftest import capabilities, make_document, segment

SURPLUS = {"control_surplus_entity": "binary_sensor.surplus"}


class TestCheckPlan:
    def test_a_fitting_plan_passes(self, now, parse):
        plan = parse(
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", setpoint_f=130)]
            )
        )
        check_plan(plan, capabilities())

    @pytest.mark.parametrize(
        "setpoint", [{"setpoint_f": 100}, {"setpoint_f": 155}]
    )
    def test_setpoints_are_not_checked(self, now, parse, setpoint):
        """The heater clamps what it is given; limits are the library's."""
        plan = parse(
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", **setpoint)]
            )
        )
        check_plan(plan, capabilities())

    def test_min_is_always_in_bounds(self, now, parse):
        plan = parse(
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", setpoint="min")]
            )
        )
        check_plan(plan, capabilities(control_setpoint_min_f=120))

    def test_bounds_unknown_are_not_checked(self, now, parse):
        plan = parse(
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", setpoint_f=90)]
            )
        )
        check_plan(
            plan,
            build_capabilities(
                {"control_allowed_modes": ["heat_pump"]},
                features=None,
                feature_version="v",
            ),
        )

    @pytest.mark.parametrize(
        "mode",
        [
            "heat_pump",
            "energy_saver",
            "high_demand",
            "electric",
            "vacation",
            "power_off",
        ],
    )
    def test_any_mode_the_heater_has_is_accepted(self, now, parse, mode):
        plan = parse(
            make_document(
                now,
                [
                    segment(now, "a", 0, mode="energy_saver", setpoint_f=130),
                    segment(now, "b", 60, mode=mode, setpoint_f=130),
                ],
            )
        )
        check_plan(plan, capabilities())

    def test_a_mode_the_heater_does_not_have_rejects_the_plan(self, now, parse):
        plan = parse(
            make_document(
                now,
                [
                    segment(now, "a", 0, mode="energy_saver", setpoint_f=130),
                    segment(now, "b", 60, mode="eco", setpoint_f=130),
                ],
            )
        )
        with pytest.raises(IntentRejected) as exc_info:
            check_plan(plan, capabilities())
        assert exc_info.value.reason == REASON_MODE_NOT_ALLOWED


class TestAcks:
    def test_rejected_ack(self):
        ack = rejected_ack("i-9", "superseded", "older")
        assert ack.state == "rejected"
        assert ack.as_attributes() == {
            "intent_id": "i-9",
            "reason": "superseded",
            "detail": "older",
            "segments": [],
            "rejected": None,
        }

    def test_item_ack(self):
        item = ItemAck(
            id="s",
            status="shadow",
            warnings=("moved_1_min",),
            extra={"purpose": "charge"},
            detail={"in_force": True},
        )
        assert item.as_attribute() == {
            "purpose": "charge",
            "id": "s",
            "status": "shadow",
            "reason": None,
            "warnings": ["moved_1_min"],
            "in_force": True,
        }

    def test_no_ack(self):
        assert NO_ACK.state == "none"
