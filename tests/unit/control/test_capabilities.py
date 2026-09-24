"""The capability declaration (spec section 4.1, issue #158)."""

from __future__ import annotations

from custom_components.nwp500.const import (
    CONF_CONTROL_ALLOWED_MODES,
    CONF_CONTROL_HOLD_OFF_SUPPORTED,
    CONF_CONTROL_LIVE_TYPES,
    CONF_CONTROL_MODE,
    CONF_CONTROL_SETPOINT_MAX_F,
    CONF_CONTROL_SETPOINT_MIN_F,
    CONF_CONTROL_SURPLUS_ENTITY,
    CONTROL_MODE_LIVE,
    CONTROL_MODE_SHADOW,
)
from custom_components.nwp500.control.capabilities import build_capabilities

from .conftest import FakeFeatures, capabilities


class TestDeclaration:
    def test_defaults(self):
        attrs = capabilities().as_attributes()

        assert attrs["protocols"] == ["0"]
        assert attrs["feature_version"] == "0.0-test"
        assert attrs["mode"] == CONTROL_MODE_SHADOW
        assert attrs["live_types"] == []
        assert attrs["supported_directives"] == ["charge", "mode"]
        assert attrs["allowed_modes"] == ["energy_saver"]
        assert attrs["assisted_mode"] == "energy_saver"
        assert attrs["tou_off_for_mode"] is False
        assert attrs["min_run_before_stop_min"] == 120
        assert attrs["reservation_entry_limit"] == 7
        assert attrs["reservation_entry_reserve"] == 2
        assert attrs["daily_revert_time"] == "03:00"
        assert attrs["setpoint_resolution_c"] == 0.5
        assert attrs["setpoint_change_mid_cycle"] is True
        assert attrs["hold_off_margin_f"] == 2.0
        assert attrs["baseline"] is None
        assert attrs["version"] == capabilities().version

    def test_bounds_follow_the_device_range(self):
        attrs = capabilities().as_attributes()
        assert attrs["setpoint_min_f"] == 104.9
        assert attrs["setpoint_max_f"] == 149.9
        assert attrs["setpoint_min_c"] == 40.5
        assert attrs["setpoint_max_c"] == 65.5

    def test_bounds_absent_until_features_arrive(self):
        declaration = build_capabilities(
            {}, features=None, feature_version="v", telemetry={}
        )
        attrs = declaration.as_attributes()
        assert declaration.setpoint_min_raw is None
        assert "setpoint_min_f" not in attrs
        assert "setpoint_max_c" not in attrs

    def test_options_tighten_the_bounds(self):
        declaration = capabilities(
            **{
                CONF_CONTROL_SETPOINT_MIN_F: 120,
                CONF_CONTROL_SETPOINT_MAX_F: 145,
            }
        )
        attrs = declaration.as_attributes()
        # 120 degF is 48.9 degC, 98 half-degrees, which reads back as 120.2.
        assert declaration.setpoint_min_raw == 98
        assert attrs["setpoint_min_f"] == 120.2
        assert attrs["setpoint_max_f"] == 145.4

    def test_hold_off_needs_the_option(self):
        declaration = capabilities(**{CONF_CONTROL_HOLD_OFF_SUPPORTED: True})
        assert "hold_off" in declaration.supported_directives

    def test_surplus_grant_needs_a_surplus_entity(self):
        declaration = capabilities(
            **{CONF_CONTROL_SURPLUS_ENTITY: "binary_sensor.surplus"}
        )
        assert "surplus_grant" in declaration.supported_directives

    def test_live_types_only_count_in_live_mode(self):
        shadow = capabilities(**{CONF_CONTROL_LIVE_TYPES: ["charge"]})
        assert shadow.live_types == ()
        live = capabilities(
            **{
                CONF_CONTROL_MODE: CONTROL_MODE_LIVE,
                CONF_CONTROL_LIVE_TYPES: ["charge"],
            }
        )
        assert live.live_types == ("charge",)

    def test_telemetry_carries_the_dip(self):
        telemetry = capabilities().as_attributes()["telemetry"]
        assert telemetry["delivery_temperature"] == "sensor.tank_upper"
        assert telemetry["compressor_running"] == "binary_sensor.comp"
        assert telemetry["power"] == "sensor.power"
        assert telemetry["delivery_temperature_dip_f"] == 3.4
        assert telemetry["delivery_temperature_dip_min"] == 3


class TestVersion:
    def test_is_short_and_stable(self):
        assert len(capabilities().version) == 8
        assert capabilities().version == capabilities().version

    def test_changes_with_an_option(self):
        before = capabilities().version
        after = capabilities(
            **{CONF_CONTROL_ALLOWED_MODES: ["heat_pump"]}
        ).version
        assert before != after

    def test_changes_when_features_arrive(self):
        before = build_capabilities(
            {}, features=None, feature_version="v", telemetry={}
        ).version
        after = build_capabilities(
            {}, features=FakeFeatures(), feature_version="v", telemetry={}
        ).version
        assert before != after

    def test_ignores_entity_id_renames(self):
        """A consumer re-reads on a version change; a rename is not one."""
        before = capabilities().version
        after = build_capabilities(
            {},
            features=FakeFeatures(),
            feature_version="0.0-test",
            telemetry={"delivery_temperature": "sensor.renamed"},
        ).version
        assert before == after
