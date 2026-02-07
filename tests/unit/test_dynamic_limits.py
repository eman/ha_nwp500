"""Tests for dynamic temperature limits."""

from unittest.mock import MagicMock

from homeassistant.const import UnitOfTemperature

from custom_components.nwp500.const import (
    MAX_TEMPERATURE_C,
    MAX_TEMPERATURE_F,
    MIN_TEMPERATURE_C,
    MIN_TEMPERATURE_F,
)
from custom_components.nwp500.number import NWP500TargetTemperature
from custom_components.nwp500.water_heater import NWP500WaterHeater


class TestDynamicLimits:
    """Test dynamic limits for water heater and number entities."""

    def test_water_heater_limits_from_features(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test water heater limits from device features."""
        mac_address = mock_device.device_info.mac_address

        # Mock device features
        features = MagicMock()
        features.dhw_temperature_min = 100.0
        features.dhw_temperature_max = 140.0
        mock_coordinator.device_features.get.return_value = features

        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.min_temp == 100.0
        assert heater.max_temp == 140.0

    def test_water_heater_limits_fallback_fahrenheit(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test water heater limits fallback (Fahrenheit)."""
        mac_address = mock_device.device_info.mac_address

        # Ensure device features missing
        mock_coordinator.device_features.get.return_value = None

        # Set HA to Fahrenheit
        mock_hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT

        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.min_temp == float(MIN_TEMPERATURE_F)
        assert heater.max_temp == float(MAX_TEMPERATURE_F)

    def test_water_heater_limits_fallback_celsius(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test water heater limits fallback (Celsius)."""
        mac_address = mock_device.device_info.mac_address

        # Ensure device features missing
        mock_coordinator.device_features.get.return_value = None
        
        # Ensure status is missing so it falls back to HA config
        mock_coordinator.data = {
            mac_address: {
                "device": mock_device,
                "status": None,
            }
        }

        # Set HA to Celsius
        mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS

        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        # Should return Celsius constants when HA is in Celsius
        assert heater.min_temp == float(MIN_TEMPERATURE_C)
        assert heater.max_temp == float(MAX_TEMPERATURE_C)

    def test_number_limits_from_features(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test number entity limits from device features."""
        mac_address = mock_device.device_info.mac_address

        # Mock device features
        features = MagicMock()
        features.dhw_temperature_min = 100.0
        features.dhw_temperature_max = 140.0
        mock_coordinator.device_features.get.return_value = features

        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        assert number.native_min_value == 100.0
        assert number.native_max_value == 140.0

    def test_number_limits_fallback_celsius(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test number entity limits fallback (Celsius)."""
        mac_address = mock_device.device_info.mac_address

        # Ensure device features missing
        mock_coordinator.device_features.get.return_value = None
        
        # Ensure status is missing so it falls back to HA config
        mock_coordinator.data = {
            mac_address: {
                "device": mock_device,
                "status": None,
            }
        }

        # Set HA to Celsius
        mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS

        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        assert number.native_min_value == float(MIN_TEMPERATURE_C)
        assert number.native_max_value == float(MAX_TEMPERATURE_C)
