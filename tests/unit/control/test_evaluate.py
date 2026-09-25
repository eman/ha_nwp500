"""Checks against the declaration (spec section 3.5)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.nwp500.control.capabilities import build_capabilities
from custom_components.nwp500.control.evaluate import (
    NO_ACK,
    REASON_GRANTS_UNSUPPORTED,
    REASON_IN_PAST,
    REASON_INVALID_WINDOW,
    REASON_OVERLAPPING_GRANT,
    ItemAck,
    check_grants,
    check_plan,
    grants_live,
    rejected_ack,
)
from custom_components.nwp500.control.intent import (
    REASON_MODE_NOT_ALLOWED,
    REASON_OUT_OF_BOUNDS,
    IntentRejected,
)

from .conftest import capabilities, grant, make_document, segment

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
    def test_out_of_bounds_rejects_the_plan(self, now, parse, setpoint):
        plan = parse(
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", **setpoint)]
            )
        )
        with pytest.raises(IntentRejected) as exc_info:
            check_plan(plan, capabilities())
        assert exc_info.value.reason == REASON_OUT_OF_BOUNDS

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
                telemetry={},
            ),
        )

    @pytest.mark.parametrize(
        "mode", ["electric", "vacation", "power_off", "eco"]
    )
    def test_mode_not_allowed_rejects_the_plan(self, now, parse, mode):
        plan = parse(
            make_document(
                now,
                [
                    segment(now, "a", 0, mode="energy_saver", setpoint_f=130),
                    segment(now, "b", 60, mode=mode, setpoint_f=130),
                ],
            )
        )
        with pytest.raises(IntentRejected) as exc_info:
            check_plan(
                plan, capabilities(control_allowed_modes=["energy_saver"])
            )
        assert exc_info.value.reason == REASON_MODE_NOT_ALLOWED


class TestCheckGrants:
    def test_unsupported_without_a_surplus_entity(self, now, parse):
        plan = parse(
            make_document(now, grants=[grant(now, "g", 0, 60, max_f=146)])
        )
        assert check_grants(plan, capabilities(), now=now) == {
            "g": REASON_GRANTS_UNSUPPORTED
        }

    def test_each_grant_on_its_own(self, now, parse):
        plan = parse(
            make_document(
                now,
                grants=[
                    grant(now, "ok", 0, 60, max_f=146),
                    grant(now, "overlap", 30, 90, max_f=146),
                    grant(now, "backwards", 200, 100, max_f=146),
                    grant(now, "hot", 300, 400, max_f=160),
                    grant(now, "past", -120, -60, max_f=146),
                ],
            )
        )
        assert check_grants(plan, capabilities(**SURPLUS), now=now) == {
            "overlap": REASON_OVERLAPPING_GRANT,
            "backwards": REASON_INVALID_WINDOW,
            "hot": REASON_OUT_OF_BOUNDS,
            "past": REASON_IN_PAST,
        }

    def test_adjacent_grants_do_not_overlap(self, now, parse):
        plan = parse(
            make_document(
                now,
                grants=[
                    grant(now, "a", 0, 60, max_f=146),
                    grant(now, "b", 60, 90, max_f=146),
                ],
            )
        )
        assert check_grants(plan, capabilities(**SURPLUS), now=now) == {}

    def test_grants_live(self):
        assert grants_live(capabilities()) is False
        assert grants_live(
            capabilities(
                control_mode="live",
                control_live_segments=True,
                control_live_grants=True,
            )
        )
        # Grants are live only with segments (spec section 6.1).
        assert not grants_live(
            capabilities(control_mode="live", control_live_grants=True)
        )


class TestAcks:
    def test_rejected_ack(self):
        ack = rejected_ack("i-9", "superseded", "older")
        assert ack.state == "rejected"
        assert ack.as_attributes() == {
            "intent_id": "i-9",
            "reason": "superseded",
            "detail": "older",
            "segments": [],
            "grants": [],
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


def test_grant_contains(now, parse):
    plan = parse(make_document(now, grants=[grant(now, "g", 0, 60, max_f=146)]))
    g = plan.grants[0]
    assert g.contains(now)
    assert not g.contains(now + timedelta(minutes=60))
