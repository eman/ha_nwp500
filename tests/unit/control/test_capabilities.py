"""The capability declaration (spec section 4.1, issue #158)."""

from __future__ import annotations

from dataclasses import replace

from custom_components.nwp500.control.capabilities import build_capabilities

from .conftest import FakeFeatures, capabilities


class TestDeclaration:
    def test_defaults(self):
        attrs = build_capabilities(
            {}, features=FakeFeatures(), feature_version="v", telemetry={}
        ).as_attributes()

        assert attrs["protocols"] == ["0"]
        assert attrs["mode"] == "shadow"
        assert attrs["live"] == {"segments": False, "grants": False}
        assert attrs["allowed_modes"] == ["energy_saver"]
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

    def test_options_tighten_the_bounds(self):
        declaration = capabilities(
            control_setpoint_min_f=120, control_setpoint_max_f=145
        )
        assert declaration.setpoint_min_raw == 98
        assert declaration.as_attributes()["setpoint_min_f"] == 120.2

    def test_grants_need_a_surplus_entity(self):
        assert capabilities(
            control_surplus_entity="binary_sensor.s"
        ).grants_supported

    def test_live_switches_are_booleans(self):
        declaration = capabilities(
            control_live_segments=True, control_live_grants="yes"
        )
        assert declaration.live_segments is True
        assert declaration.live_grants is False

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


class TestBoundsOnlyTighten:
    """Options narrow the device's range; they never widen it (#162)."""

    def test_a_wider_option_keeps_the_device_limit(self):
        declaration = capabilities(
            control_setpoint_min_f=90, control_setpoint_max_f=160
        )
        assert declaration.setpoint_min_raw == 81
        assert declaration.setpoint_max_raw == 131

    def test_a_narrower_option_applies(self):
        declaration = capabilities(
            control_setpoint_min_f=120, control_setpoint_max_f=140
        )
        assert declaration.setpoint_min_raw == 98
        assert declaration.setpoint_max_raw == 120

    def test_options_that_leave_no_range_are_ignored(self):
        declaration = capabilities(control_setpoint_min_f=155)
        assert declaration.setpoint_min_raw == 81
        assert declaration.setpoint_max_raw == 131

    def test_options_apply_before_the_device_range_is_known(self):
        declaration = build_capabilities(
            {"control_setpoint_min_f": 120},
            features=None,
            feature_version="v",
            telemetry={},
        )
        assert declaration.setpoint_min_raw == 98
        assert declaration.setpoint_max_raw is None
