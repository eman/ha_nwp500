"""The capability declaration (spec section 4.1, issue #158)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.nwp500.control.capabilities import build_capabilities

from .conftest import FakeFeatures, capabilities


class TestDeclaration:
    def test_defaults(self):
        attrs = build_capabilities(
            {}, features=FakeFeatures(), feature_version="v", telemetry={}
        ).as_attributes()

        assert attrs["protocols"] == ["1", "0"]
        # The newest minor of each major: 1.1 adds `reassert`, 1.2 a grant's
        # own timing rules.
        assert attrs["protocol_versions"] == ["1.2", "0"]
        # One per major, in the same order.
        assert [v.split(".")[0] for v in attrs["protocol_versions"]] == (
            attrs["protocols"]
        )
        assert attrs["mode"] == "shadow"
        assert attrs["live"] == {"segments": False, "grants": False}
        # Every mode the heater has: the adapter applies any of them.
        assert attrs["allowed_modes"] == [
            "heat_pump",
            "energy_saver",
            "high_demand",
            "electric",
            "vacation",
            "power_off",
        ]
        assert attrs["assisted_mode"] == "energy_saver"
        assert attrs["horizon_h"] == 144
        assert attrs["near_term_lead_min"] == 2
        assert attrs["entry_limit"] == 16
        assert attrs["entry_reserve"] == 2
        assert attrs["grants_supported"] is False
        assert attrs["grant_rules"] == {
            "surplus_on_before_raise_min": 10,
            "surplus_off_before_lower_min": 15,
            "min_run_before_lower_min": 120,
        }
        # A grant may set each rule itself, within these (protocol 1.2).
        assert attrs["grant_rule_ranges"] == {
            "surplus_on_before_raise_min": [0, 60],
            "surplus_off_before_lower_min": [0, 60],
            "min_run_before_lower_min": [0, 600],
        }
        assert attrs["owner_program"] is None
        assert attrs["lower_trigger_f"] == 104.9
        assert attrs["setpoint_write_starts_recovery"] is True
        assert attrs["setpoint_write_stops_compressor"] is True
        assert attrs["entry_mode_in_tou_window"] == "held"
        # Measured on the unit tested (spec section 8).
        assert attrs["list_write_starts_recovery"] is False
        assert attrs["unchanged_entry_starts_recovery"] is False
        assert attrs["entries_fire_when_powered_off"] is True
        assert attrs["entries_fire_in_vacation"] is False
        assert attrs["setpoint_resolution_c"] == 0.5
        assert attrs["entries_available"] is None

    def test_bounds_follow_the_device_range(self):
        attrs = capabilities().as_attributes()
        assert attrs["setpoint_min_f"] == 104.9
        assert attrs["setpoint_max_f"] == 149.9
        assert attrs["setpoint_min_c"] == 40.5
        assert attrs["setpoint_max_c"] == 65.5

    def test_bounds_absent_until_features_arrive(self):
        attrs = build_capabilities(
            {}, features=None, feature_version="v", telemetry={}
        ).as_attributes()
        assert "setpoint_min_f" not in attrs

    def test_grants_are_never_supported(self):
        """The adapter reads nothing about the home's power (section 5.7).

        Not even with the options an earlier version stored for them.
        """
        attrs = capabilities(
            control_mode="live",
            control_live_segments=True,
            control_live_grants=True,
            control_surplus_entity="binary_sensor.surplus",
            control_min_run_before_lower_min=0,
        ).as_attributes()
        assert attrs["grants_supported"] is False
        assert attrs["live"] == {"segments": True, "grants": False}
        assert attrs["grant_rules"]["min_run_before_lower_min"] == 120

    def test_telemetry_carries_the_dip(self):
        telemetry = capabilities().as_attributes()["telemetry"]
        assert telemetry["delivery_temperature"] == "sensor.tank_upper"
        assert telemetry["delivery_temperature_dip_f"] == 3.4
        assert telemetry["delivery_temperature_dip_min"] == 3

    def test_owner_program_is_declared(self):
        owner = {"declared": False, "mode": "energy_saver", "entries": []}
        declaration = build_capabilities(
            {},
            features=None,
            feature_version="v",
            telemetry={},
            owner_program=owner,
        )
        assert declaration.as_attributes()["owner_program"] == owner


class TestVersion:
    def test_is_short_and_stable(self):
        assert len(capabilities().version) == 8
        assert capabilities().version == capabilities().version

    def test_changes_with_an_option(self):
        before = capabilities().version
        after = capabilities(control_reservation_entry_limit=9).version
        assert before != after

    def test_changes_when_features_arrive(self):
        before = build_capabilities(
            {}, features=None, feature_version="v", telemetry={}
        )
        after = build_capabilities(
            {}, features=FakeFeatures(), feature_version="v", telemetry={}
        )
        assert before.version != after.version

    def test_ignores_renames_and_the_live_entry_count(self):
        declaration = capabilities()
        assert (
            replace(declaration, entries_available=3).version
            == declaration.version
        )
        renamed = replace(
            declaration, telemetry={"delivery_temperature": "sensor.x"}
        )
        assert renamed.version == declaration.version


@pytest.mark.parametrize(
    ("options", "follows"),
    [
        ({"control_mode": "live"}, True),
        ({"control_mode": "live", "control_live_segments": True}, True),
        # An earlier version's live with its segments switch off wrote
        # nothing, and still writes nothing until the form is saved.
        ({"control_mode": "live", "control_live_segments": False}, False),
        ({"control_mode": "shadow"}, False),
        ({"control_mode": "disabled"}, False),
    ],
)
def test_live_follows_the_plan(options, follows):
    from custom_components.nwp500.const import control_follows_plan

    assert control_follows_plan(options) is follows
    assert capabilities(**options).as_attributes()["live"] == {
        "segments": follows,
        "grants": False,
    }
