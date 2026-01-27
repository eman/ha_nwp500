"""Tests for water_heater.py module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_ELECTRIC,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
)
from homeassistant.const import STATE_OFF

from custom_components.nwp500.const import MAX_TEMPERATURE, MIN_TEMPERATURE
from custom_components.nwp500.water_heater import NWP500WaterHeater


class TestNWP500WaterHeater:
    """Tests for NWP500WaterHeater entity."""

    def test_initialization(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test water heater initialization."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.coordinator == mock_coordinator
        assert heater.mac_address == mac_address
        assert heater.min_temp == MIN_TEMPERATURE
        assert heater.max_temp == MAX_TEMPERATURE

    def test_current_temperature(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test current_temperature returns dhwTemperature."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.current_temperature == 120.0

    def test_current_temperature_missing(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test current_temperature returns None when unavailable."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        # Remove dhw_temperature to simulate unavailable sensor
        delattr(mock_device_status, "dhw_temperature")

        assert heater.current_temperature is None

    def test_target_temperature(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test target_temperature property."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.target_temperature == 130.0

    def test_current_operation_heat_pump(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test current_operation with heat pump mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 1  # HEAT_PUMP

        assert heater.current_operation == STATE_HEAT_PUMP

    def test_current_operation_eco(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test current_operation with eco mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 3  # ENERGY_SAVER

        assert heater.current_operation == STATE_ECO

    def test_current_operation_vacation_returns_eco(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test vacation mode returns eco as operation."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 5  # VACATION

        assert heater.current_operation == STATE_ECO

    def test_current_operation_power_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test power off mode returns off state."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 6  # POWER_OFF

        assert heater.current_operation == STATE_OFF

    def test_operation_list(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test operation_list returns available modes."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        operations = heater.operation_list

        assert STATE_ECO in operations
        assert STATE_HEAT_PUMP in operations
        assert STATE_HIGH_DEMAND in operations
        assert STATE_ELECTRIC in operations
        assert len(operations) == 4
        # Vacation and power_off should not be in operation list
        assert "vacation" not in operations
        assert STATE_OFF not in operations

    def test_is_on_when_powered_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test is_on returns True when device is on."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 1  # HEAT_PUMP

        assert heater.is_on is True

    def test_is_on_when_powered_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test is_on returns False when device is off."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 6  # POWER_OFF

        assert heater.is_on is False

    def test_is_away_mode_on_when_vacation(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test is_away_mode_on returns True in vacation."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 5  # VACATION

        assert heater.is_away_mode_on is True

    def test_is_away_mode_on_when_not_vacation(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test is_away_mode_on returns False when not vacation."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_device_status.dhw_operation_setting.value = 1  # HEAT_PUMP

        assert heater.is_away_mode_on is False

    @pytest.mark.asyncio
    async def test_async_set_temperature(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test setting temperature."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        await heater.async_set_temperature(temperature=125)

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_temperature", temperature=125.0
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_set_operation_mode(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test setting operation mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        await heater.async_set_operation_mode(STATE_ECO)

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address,
            "set_dhw_mode",
            mode=3,  # ECO mode value
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    def test_extra_state_attributes(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test extra_state_attributes property."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        attrs = heater.extra_state_attributes

        assert attrs is not None
        assert "dhw_mode_setting" in attrs
        assert "current_operation_state" in attrs
        assert "outside_temperature" in attrs

    def test_extra_state_attributes_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test extra_state_attributes when status unavailable."""
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
            }
        }

        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        attrs = heater.extra_state_attributes

        # Should return base attributes even without status
        assert attrs is not None

    @pytest.mark.asyncio
    async def test_async_set_temperature_none(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test setting temperature with None value."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock()

        await heater.async_set_temperature()

        # Should not call control_device when temperature is None
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_set_operation_mode_invalid(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test setting invalid operation mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock()

        await heater.async_set_operation_mode("invalid_mode")

        # Should not call control_device with invalid mode
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test turning water heater on."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        await heater.async_turn_on()

        # Turn on sets to ECO mode (mode=3), not power_on
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_dhw_mode", mode=3
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test turning water heater off."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        await heater.async_turn_off()

        # Turn off sets to POWER_OFF mode (mode=6)
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_dhw_mode", mode=6
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_away_mode_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test turning away mode on."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        await heater.async_turn_away_mode_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address,
            "set_dhw_mode",
            mode=5,  # VACATION mode value
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_away_mode_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test turning away mode off."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        await heater.async_turn_away_mode_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address,
            "set_dhw_mode",
            mode=3,  # ECO mode value
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    def test_current_operation_unknown(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test current_operation returns unknown for unmapped value."""
        # Set to unmapped operation setting value
        mock_device_status.dhw_operation_setting.value = 99

        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.current_operation == "unknown"

    def test_is_on_fallback_to_component_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test is_on falls back to checking component status."""
        # Remove dhw_operation_setting to trigger fallback
        delattr(mock_device_status, "dhw_operation_setting")

        # Set component statuses
        mock_device_status.dhw_use = True

        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.is_on is True

    def test_is_on_fallback_to_operation_mode(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test is_on falls back to operationMode."""
        # Remove dhw_operation_setting to trigger fallback
        delattr(mock_device_status, "dhw_operation_setting")

        # Set all component statuses to False
        mock_device_status.dhw_use = False
        mock_device_status.comp_use = False
        mock_device_status.heat_upper_use = False
        mock_device_status.heat_lower_use = False

        # Keep operation_mode
        mock_device_status.operation_mode.value = 32

        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(mock_coordinator, mac_address, mock_device)
        heater.hass = mock_hass

        assert heater.is_on is True
