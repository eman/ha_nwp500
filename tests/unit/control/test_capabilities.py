"""The capability declaration (spec section 4.1, issue #158)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.nwp500.control.capabilities import build_capabilities

from .conftest import capabilities


class TestDeclaration:
    def test_what_runs_the_entry_budget_and_what_a_plan_may_say(self):
        """What runs, the budget, and the heater's own limits (#192).

        Facts about the heater a plan is checked against; nothing of
        energy or scheduling.
        """
        attrs = build_capabilities({}, feature_version="v").as_attributes()

        assert set(attrs) == {
            "protocols",
            "protocol_versions",
            "feature_version",
            "mode",
            "setpoint_resolution_c",
            "horizon_h",
            "near_term_lead_min",
            "entry_limit",
            "entry_reserve",
            "entries_available",
            "modes",
            "setpoint",
            "commands",
            "version",
        }
        assert attrs["protocols"] == ["1", "0"]
        # The newest minor of each major: 1.1 adds `reassert`.
        assert attrs["protocol_versions"] == ["1.4", "0"]
        assert attrs["mode"] == "shadow"
        assert attrs["horizon_h"] == 144
        assert attrs["near_term_lead_min"] == 2
        assert attrs["entry_limit"] == 16
        assert attrs["entry_reserve"] == 2
        assert attrs["setpoint_resolution_c"] == 0.5
        assert attrs["entries_available"] is None
        assert attrs["modes"] == [
            "heat_pump",
            "energy_saver",
            "high_demand",
            "electric",
            "power_off",
        ]
        # Unknown until the heater's features are read.
        assert attrs["setpoint"] is None
        assert attrs["commands"]["vacation"] == {
            "days": {"type": "integer", "min": 1, "max": 99}
        }
        assert attrs["commands"]["anti_legionella"] == {
            "enabled": {"type": "boolean"},
            "period_days": {"type": "integer", "min": 1, "max": 30},
        }
        assert attrs["commands"]["power"] == {"on": {"type": "boolean"}}

    def test_the_heaters_setpoint_range_in_both_scales(self):
        attrs = build_capabilities(
            {}, feature_version="v", setpoint_range=(81, 131)
        ).as_attributes()
        assert attrs["setpoint"] == {
            "min_f": 104.9,
            "max_f": 149.9,
            "min_c": 40.5,
            "max_c": 65.5,
        }
        without = build_capabilities({}, feature_version="v").as_attributes()
        assert attrs["version"] != without["version"]


class TestVersion:
    def test_is_short_and_stable(self):
        assert len(capabilities().version) == 8
        assert capabilities().version == capabilities().version

    def test_changes_with_an_option(self):
        before = capabilities().version
        after = capabilities(control_reservation_entry_limit=9).version
        assert before != after

    def test_ignores_the_live_entry_count(self):
        declaration = capabilities()
        assert (
            replace(declaration, entries_available=3).version
            == declaration.version
        )


@pytest.mark.parametrize(
    ("options", "mode"),
    [
        ({"control_mode": "live"}, "live"),
        ({"control_mode": "live", "control_live_segments": True}, "live"),
        # An earlier version's live with its segments switch off wrote
        # nothing, and still writes nothing until the form is saved.
        ({"control_mode": "live", "control_live_segments": False}, "shadow"),
        ({"control_mode": "shadow"}, "shadow"),
        ({"control_mode": "disabled"}, "disabled"),
    ],
)
def test_live_follows_the_plan(options, mode):
    from custom_components.nwp500.const import control_follows_plan

    assert control_follows_plan(options) is (mode == "live")


@pytest.mark.parametrize(
    ("setpoint_f", "ok"),
    [(104.9, True), (150, True), (151, False), (104, False)],
)
def test_a_setpoint_is_checked_after_rounding_to_half_degrees(setpoint_f, ok):
    """150 °F is 65.5 °C on the heater, its 149.9 °F maximum."""
    from datetime import UTC, datetime

    from custom_components.nwp500.control.evaluate import check_plan
    from custom_components.nwp500.control.intent import (
        REASON_SETPOINT_OUT_OF_RANGE,
        IntentRejected,
        parse_plan,
    )

    from .conftest import make_document, segment

    now = datetime(2026, 11, 19, 12, tzinfo=UTC)
    plan = parse_plan(
        make_document(
            now, [segment(now, "s", 0, mode="heat_pump", setpoint_f=setpoint_f)]
        )
    )
    if ok:
        check_plan(plan, (81, 131))
        return
    with pytest.raises(IntentRejected) as raised:
        check_plan(plan, (81, 131))
    assert raised.value.reason == REASON_SETPOINT_OUT_OF_RANGE
