"""Tests for dynamic temperature limits."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import ServiceValidationError

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

    def test_water_heater_limits_fahrenheit(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Before feature data arrives, the platform constants apply (F)."""
        mac_address = mock_device.device_info.mac_address

        # Set HA to Fahrenheit
        mock_hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT

        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        # No feature data yet, so the platform constants apply
        assert heater.min_temp == float(MIN_TEMPERATURE_F)
        assert heater.max_temp == float(MAX_TEMPERATURE_F)

    def test_water_heater_limits_celsius(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Before feature data arrives, the platform constants apply (C)."""
        mac_address = mock_device.device_info.mac_address

        # Set HA to Celsius
        mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS

        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        # No feature data yet, so the platform constants apply
        assert heater.min_temp == float(MIN_TEMPERATURE_C)
        assert heater.max_temp == float(MAX_TEMPERATURE_C)

    @staticmethod
    def _heater_with_device_range(
        mock_coordinator, mock_device, mock_hass, unit
    ) -> NWP500WaterHeater:
        """A heater whose device reports the usual NWP500 setpoint range.

        The raw values are what the device sends, in half-degrees Celsius:
        81 is 40.5 degC / 104.9 degF and 131 is 65.5 degC / 149.9 degF.
        """
        mock_hass.config.units.temperature_unit = unit
        features = MagicMock()
        features.dhw_temperature_min_raw = 81
        features.dhw_temperature_max_raw = 131
        mock_coordinator.device_features.get.return_value = features
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass
        return heater

    @pytest.mark.parametrize(
        ("unit", "low", "high"),
        [
            (UnitOfTemperature.FAHRENHEIT, 104.9, 149.9),
            (UnitOfTemperature.CELSIUS, 40.5, 65.5),
        ],
    )
    def test_water_heater_limits_from_features(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
        unit: str,
        low: float,
        high: float,
    ):
        """Once known, the device's own range replaces the constants.

        The constants are wider than the device accepts, so offering them
        let the UI propose setpoints the library then rejected. The range is
        converted to Home Assistant's unit directly, so it is right even
        before the library's own unit setting catches up with a change.
        """
        heater = self._heater_with_device_range(
            mock_coordinator, mock_device, mock_hass, unit
        )

        assert heater.min_temp == pytest.approx(low)
        assert heater.max_temp == pytest.approx(high)

    @pytest.mark.asyncio
    async def test_water_heater_rejects_setpoint_below_device_minimum(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """A setpoint inside the constants but below the device's minimum.

        It used to pass the entity's check, be rejected by the library, and
        leave the service call reporting success.
        """
        heater = self._heater_with_device_range(
            mock_coordinator,
            mock_device,
            mock_hass,
            UnitOfTemperature.FAHRENHEIT,
        )

        assert MIN_TEMPERATURE_F < 100 < heater.min_temp
        with pytest.raises(ServiceValidationError):
            await heater.async_set_temperature(temperature=100)

        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("unit", "requested", "sent"),
        [
            # Shown as 150 at whole-degree precision, so it must be accepted.
            (UnitOfTemperature.FAHRENHEIT, 150, 149.9),
            # Shown as 105; the device stores the same half-degree step.
            (UnitOfTemperature.FAHRENHEIT, 105, 105.0),
            (UnitOfTemperature.FAHRENHEIT, 104.5, 104.9),
            (UnitOfTemperature.CELSIUS, 65.5, 65.5),
        ],
    )
    async def test_water_heater_accepts_the_limits_the_ui_shows(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
        unit: str,
        requested: float,
        sent: float,
    ):
        """The UI rounds the limits, and a rounded limit must still work.

        HA shows `max_temp` at the display precision, so a device maximum of
        149.9 degF is offered as 150. Rejecting 150 would refuse a value the
        UI offers. The value is clamped into the device's exact range, which
        the library checks, before it is sent.
        """
        heater = self._heater_with_device_range(
            mock_coordinator, mock_device, mock_hass, unit
        )

        await heater.async_set_temperature(temperature=requested)

        sent_value = mock_coordinator.async_control_device.call_args.kwargs[
            "temperature"
        ]
        assert sent_value == pytest.approx(sent)
        assert heater.min_temp <= sent_value <= heater.max_temp

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("unit", "requested"),
        [
            (UnitOfTemperature.FAHRENHEIT, 151),
            (UnitOfTemperature.FAHRENHEIT, 104),
            # Celsius is shown to a tenth, so the allowance is 0.05 degC.
            (UnitOfTemperature.CELSIUS, 65.6),
            (UnitOfTemperature.CELSIUS, 40.4),
        ],
    )
    async def test_water_heater_rejects_beyond_the_rounding(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
        unit: str,
        requested: float,
    ):
        """Only rounding is forgiven, not a value past the shown limit."""
        heater = self._heater_with_device_range(
            mock_coordinator, mock_device, mock_hass, unit
        )

        with pytest.raises(ServiceValidationError):
            await heater.async_set_temperature(temperature=requested)

        mock_coordinator.async_control_device.assert_not_called()

    def test_limits_ignore_non_integer_raw_values(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """A raw limit that is not an integer falls back to the constant."""
        mac_address = mock_device.device_info.mac_address
        mock_hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT

        features = MagicMock()
        features.dhw_temperature_min_raw = None
        features.dhw_temperature_max_raw = "131"
        mock_coordinator.device_features.get.return_value = features

        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.min_temp == float(MIN_TEMPERATURE_F)
        assert heater.max_temp == float(MAX_TEMPERATURE_F)

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
