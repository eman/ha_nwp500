"""Tests for const.py module."""

from __future__ import annotations

from custom_components.nwp500.const import (
    CURRENT_OPERATION_MODE_TO_HA,
    DHW_OPERATION_SETTING_TO_HA,
    DOMAIN,
    HA_TO_DHW_MODE,
    MAX_TEMPERATURE_C,
    MAX_TEMPERATURE_F,
    MIN_TEMPERATURE_C,
    MIN_TEMPERATURE_F,
    get_enum_value,
)


def test_domain():
    """Test that domain is correct."""
    assert DOMAIN == "nwp500"


def test_temperature_limits():
    """Test temperature min/max constants."""
    assert MIN_TEMPERATURE_F == 80
    assert MAX_TEMPERATURE_F == 150
    assert MIN_TEMPERATURE_C == 27
    assert MAX_TEMPERATURE_C == 65
    assert MIN_TEMPERATURE_F < MAX_TEMPERATURE_F
    assert MIN_TEMPERATURE_C < MAX_TEMPERATURE_C


def test_get_enum_value_with_value_attribute():
    """Test get_enum_value with object that has .value."""

    class MockEnum:
        value = 42

    assert get_enum_value(MockEnum()) == 42


def test_get_enum_value_without_value_attribute():
    """Test get_enum_value with object that doesn't have .value."""
    assert get_enum_value(42) == 42
    assert get_enum_value("test") == "test"
    assert get_enum_value(None) is None


def test_current_operation_mode_mapping():
    """Test CurrentOperationMode to HA state mapping."""
    assert CURRENT_OPERATION_MODE_TO_HA[0] == "standby"
    assert CURRENT_OPERATION_MODE_TO_HA[32] == "heat_pump"
    assert CURRENT_OPERATION_MODE_TO_HA[64] == "eco"
    assert CURRENT_OPERATION_MODE_TO_HA[96] == "high_demand"
    # Electric mode is in DHW settings, not current operation mode


def test_dhw_operation_setting_mapping():
    """Test DHW operation setting to HA state mapping."""
    assert DHW_OPERATION_SETTING_TO_HA[1] == "heat_pump"
    assert DHW_OPERATION_SETTING_TO_HA[2] == "electric"
    assert DHW_OPERATION_SETTING_TO_HA[3] == "eco"
    assert DHW_OPERATION_SETTING_TO_HA[4] == "high_demand"
    assert DHW_OPERATION_SETTING_TO_HA[5] == "vacation"
    assert DHW_OPERATION_SETTING_TO_HA[6] == "off"


def test_ha_to_dhw_mode_mapping():
    """Test HA state to DHW mode mapping."""
    assert HA_TO_DHW_MODE["eco"] == 3
    assert HA_TO_DHW_MODE["heat_pump"] == 1
    assert HA_TO_DHW_MODE["high_demand"] == 4
    assert HA_TO_DHW_MODE["electric"] == 2
    # Vacation and power_off should not be in this mapping
    assert "vacation" not in HA_TO_DHW_MODE
    assert "off" not in HA_TO_DHW_MODE


def test_mode_mappings_are_bidirectional():
    """Test that mode mappings can convert back and forth."""
    # DHW_OPERATION_SETTING -> HA -> HA_TO_DHW_MODE should match
    # (for normal operation modes only)
    for dhw_value, ha_state in DHW_OPERATION_SETTING_TO_HA.items():
        if ha_state in HA_TO_DHW_MODE:
            assert HA_TO_DHW_MODE[ha_state] == dhw_value
