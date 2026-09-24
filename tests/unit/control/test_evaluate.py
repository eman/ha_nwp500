"""Per-directive checks and the ack summary (spec section 3.3)."""

from __future__ import annotations

from datetime import timedelta

from custom_components.nwp500.const import (
    CONF_CONTROL_HOLD_OFF_SUPPORTED,
    CONF_CONTROL_LIVE_TYPES,
    CONF_CONTROL_MODE,
    CONTROL_MODE_LIVE,
)
from custom_components.nwp500.control.evaluate import (
    REASON_IN_PAST,
    REASON_MODE_NOT_ALLOWED,
    REASON_OUT_OF_BOUNDS,
    REASON_TOO_SHORT,
    REASON_TYPE_NOT_LIVE,
    REASON_TYPE_UNSUPPORTED,
    STATUS_PARTLY_APPLIED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SHADOW,
    check_directive,
    evaluate_intent,
    rejected_ack,
)

from .conftest import capabilities, directive, make_document


def _one(parse, now, d, **options):
    intent = parse(make_document(now, [d]))
    return check_directive(
        intent.directives[0], capabilities(**options), now=now
    )


class TestCheckDirective:
    def test_a_fitting_charge_passes(self, now, parse):
        assert _one(parse, now, directive(now, "charge", target_f=130)) is None

    def test_type_unsupported(self, now, parse):
        d = directive(now, "hold_off")
        assert _one(parse, now, d) == REASON_TYPE_UNSUPPORTED
        assert (
            _one(parse, now, d, **{CONF_CONTROL_HOLD_OFF_SUPPORTED: True})
            is None
        )

    def test_in_past(self, now, parse):
        d = directive(now, "charge", start=-400, end=-10, target_f=130)
        assert _one(parse, now, d) == REASON_IN_PAST

    def test_a_window_already_under_way_is_not_in_the_past(self, now, parse):
        d = directive(now, "charge", start=-60, end=200, target_f=130)
        assert _one(parse, now, d) is None

    def test_out_of_bounds(self, now, parse):
        below = directive(now, "charge", target_f=100)
        above = directive(now, "surplus_grant", max_f=155)
        options = {"control_surplus_entity": "binary_sensor.s"}
        assert _one(parse, now, below) == REASON_OUT_OF_BOUNDS
        assert _one(parse, now, above, **options) == REASON_OUT_OF_BOUNDS
        at_max = directive(now, "surplus_grant", max_f=149.9)
        assert _one(parse, now, at_max, **options) is None

    def test_bounds_unknown_are_not_checked(self, now, parse):
        from custom_components.nwp500.control.capabilities import (
            build_capabilities,
        )

        intent = parse(
            make_document(now, [directive(now, "charge", target_f=90)])
        )
        declaration = build_capabilities(
            {}, features=None, feature_version="v", telemetry={}
        )
        assert (
            check_directive(intent.directives[0], declaration, now=now) is None
        )

    def test_mode_not_allowed(self, now, parse):
        d = directive(now, "mode", mode="electric")
        assert _one(parse, now, d) == REASON_MODE_NOT_ALLOWED
        assert _one(parse, now, d, control_allowed_modes=["electric"]) is None

    def test_too_short(self, now, parse):
        d = directive(now, "charge", start=0, end=119, target_f=130)
        assert _one(parse, now, d) == REASON_TOO_SHORT
        exact = directive(now, "charge", start=0, end=120, target_f=130)
        assert _one(parse, now, exact) is None

    def test_type_not_live_is_checked_last(self, now, parse):
        live = {
            CONF_CONTROL_MODE: CONTROL_MODE_LIVE,
            CONF_CONTROL_LIVE_TYPES: ["mode"],
        }
        d = directive(now, "charge", target_f=130)
        assert _one(parse, now, d, **live) == REASON_TYPE_NOT_LIVE
        bad = directive(now, "charge", target_f=100)
        assert _one(parse, now, bad, **live) == REASON_OUT_OF_BOUNDS


class TestEvaluateIntent:
    def test_shadow_when_everything_fits(self, now, parse):
        intent = parse(
            make_document(
                now,
                [
                    directive(
                        now, "charge", "c", target_f=130, purpose="demand"
                    ),
                    directive(now, "mode", "m", mode="energy_saver"),
                ],
            )
        )
        ack = evaluate_intent(intent, capabilities(), now=now)

        assert ack.state == STATUS_SHADOW
        assert ack.intent_id == "i-1"
        assert [d.status for d in ack.directives] == [STATUS_SHADOW] * 2
        attrs = ack.as_attributes()
        assert attrs["directives"][0] == {
            "purpose": "demand",
            "id": "c",
            "type": "charge",
            "status": STATUS_SHADOW,
            "reason": None,
        }

    def test_empty_intent_is_shadow(self, now, parse):
        ack = evaluate_intent(
            parse(make_document(now)), capabilities(), now=now
        )
        assert ack.state == STATUS_SHADOW
        assert ack.directives == ()

    def test_partly_applied_when_some_are_rejected(self, now, parse):
        intent = parse(
            make_document(
                now,
                [
                    directive(now, "charge", "ok", target_f=130),
                    directive(now, "hold_off", "no", start=200, end=300),
                ],
            )
        )
        ack = evaluate_intent(intent, capabilities(), now=now)
        assert ack.state == STATUS_PARTLY_APPLIED
        assert ack.directives[1].status == STATUS_REJECTED
        assert ack.directives[1].reason == REASON_TYPE_UNSUPPORTED

    def test_rejected_when_all_are_rejected(self, now, parse):
        intent = parse(make_document(now, [directive(now, "hold_off")]))
        ack = evaluate_intent(intent, capabilities(), now=now)
        assert ack.state == STATUS_REJECTED

    def test_live_types_are_pending_until_execution_exists(self, now, parse):
        live = {
            CONF_CONTROL_MODE: CONTROL_MODE_LIVE,
            CONF_CONTROL_LIVE_TYPES: ["charge"],
        }
        intent = parse(
            make_document(
                now,
                [
                    directive(now, "charge", "c", target_f=130),
                    directive(now, "mode", "m", mode="energy_saver"),
                ],
            )
        )
        ack = evaluate_intent(intent, capabilities(**live), now=now)
        assert ack.state == STATUS_PENDING
        assert ack.directives[0].status == STATUS_PENDING
        assert ack.directives[1].status == STATUS_SHADOW
        assert ack.directives[1].reason == REASON_TYPE_NOT_LIVE

    def test_only_the_document_keys_are_echoed(self, now, parse):
        intent = parse(
            make_document(now, [directive(now, "charge", target_c=55)])
        )
        ack = evaluate_intent(intent, capabilities(), now=now)
        assert set(ack.as_attributes()["directives"][0]) == {
            "id",
            "type",
            "status",
            "reason",
        }

    def test_rejected_ack(self):
        ack = rejected_ack("i-9", "unsupported_protocol", "protocol '2'")
        assert ack.state == STATUS_REJECTED
        assert ack.as_attributes() == {
            "intent_id": "i-9",
            "reason": "unsupported_protocol",
            "detail": "protocol '2'",
            "directives": [],
        }


def test_duration():
    from homeassistant.util import dt as dt_util

    from custom_components.nwp500.control.intent import Directive

    start = dt_util.utcnow()
    d = Directive(
        id="d", type="hold_off", start=start, end=start + timedelta(hours=2)
    )
    assert d.duration == timedelta(hours=2)
