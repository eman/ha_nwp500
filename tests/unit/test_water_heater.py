"""Tests for water_heater.py module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    STATE_ELECTRIC,
)
from homeassistant.const import STATE_OFF

from custom_components.nwp500.water_heater import NWP500WaterHeater
from custom_components.nwp500.const import MIN_TEMPERATURE, MAX_TEMPERATURE


class TestNWP500WaterHeater:
    """Tests for NWP500WaterHeater entity."""

    def test_initialization(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test water heater initialization."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        assert heater.coordinator == mock_coordinator
        assert heater.mac_address == mac_address
        assert heater.min_temp == MIN_TEMPERATURE
        assert heater.max_temp == MAX_TEMPERATURE

    def test_current_temperature(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test current_temperature returns dhwTemperature."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        assert heater.current_temperature == 120.0

    def test_current_temperature_missing(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test current_temperature returns None when unavailable."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        # Remove dhwTemperature to simulate unavailable sensor
        delattr(mock_device_status, "dhwTemperature")
        
        assert heater.current_temperature is None

    def test_target_temperature(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test target_temperature property."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        assert heater.target_temperature == 130.0

    def test_current_operation_heat_pump(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test current_operation with heat pump mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 1  # HEAT_PUMP
        
        assert heater.current_operation == STATE_HEAT_PUMP

    def test_current_operation_eco(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test current_operation with eco mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 3  # ENERGY_SAVER
        
        assert heater.current_operation == STATE_ECO

    def test_current_operation_vacation_returns_eco(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test vacation mode returns eco as operation."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 5  # VACATION
        
        assert heater.current_operation == STATE_ECO

    def test_current_operation_power_off(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test power off mode returns off state."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 6  # POWER_OFF
        
        assert heater.current_operation == STATE_OFF

    def test_operation_list(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test operation_list returns available modes."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
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
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test is_on returns True when device is on."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 1  # HEAT_PUMP
        
        assert heater.is_on is True

    def test_is_on_when_powered_off(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test is_on returns False when device is off."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 6  # POWER_OFF
        
        assert heater.is_on is False

    def test_is_away_mode_on_when_vacation(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test is_away_mode_on returns True in vacation."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 5  # VACATION
        
        assert heater.is_away_mode_on is True

    def test_is_away_mode_on_when_not_vacation(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test is_away_mode_on returns False when not vacation."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_device_status.dhwOperationSetting.value = 1  # HEAT_PUMP
        
        assert heater.is_away_mode_on is False

    @pytest.mark.asyncio
    async def test_async_set_temperature(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test setting temperature."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_coordinator.async_control_device = AsyncMock(
            return_value=True
        )
        mock_coordinator.async_request_refresh = AsyncMock()
        
        await heater.async_set_temperature(temperature=125)
        
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_temperature", temperature=125
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_set_operation_mode(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test setting operation mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_coordinator.async_control_device = AsyncMock(
            return_value=True
        )
        mock_coordinator.async_request_refresh = AsyncMock()
        
        await heater.async_set_operation_mode(STATE_ECO)
        
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_dhw_mode", mode=3  # ECO mode value
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    def test_extra_state_attributes(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test extra_state_attributes property."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        attrs = heater.extra_state_attributes
        
        assert attrs is not None
        assert "dhw_mode_setting" in attrs
        assert "current_operation_state" in attrs
        assert "outside_temperature" in attrs

    def test_extra_state_attributes_no_status(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test extra_state_attributes when status unavailable."""
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {}
        }
        
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        attrs = heater.extra_state_attributes
        
        # Should return base attributes even without status
        assert attrs is not None

    @pytest.mark.asyncio
    async def test_async_set_temperature_none(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test setting temperature with None value."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_coordinator.async_control_device = AsyncMock()
        
        await heater.async_set_temperature()
        
        # Should not call control_device when temperature is None
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_set_operation_mode_invalid(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test setting invalid operation mode."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_coordinator.async_control_device = AsyncMock()
        
        await heater.async_set_operation_mode("invalid_mode")
        
        # Should not call control_device with invalid mode
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_on(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test turning water heater on."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
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
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test turning water heater off."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()
        
        await heater.async_turn_off()
        
        # Turn off sets power_on to False
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_power", power_on=False
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_away_mode_on(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test turning away mode on."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()
        
        await heater.async_turn_away_mode_on()
        
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_dhw_mode", mode=5  # VACATION mode value
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_away_mode_off(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test turning away mode off."""
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()
        
        await heater.async_turn_away_mode_off()
        
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_dhw_mode", mode=3  # ECO mode value  
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    def test_current_operation_unknown(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test current_operation returns unknown for unmapped value."""
        # Set to unmapped operation setting value
        mock_device_status.dhwOperationSetting.value = 99
        
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        assert heater.current_operation == "unknown"

    def test_is_on_fallback_to_component_status(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test is_on falls back to checking component status."""
        # Remove dhwOperationSetting to trigger fallback
        delattr(mock_device_status, "dhwOperationSetting")
        
        # Set component statuses
        mock_device_status.dhwUse = True
        
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        assert heater.is_on is True

    def test_is_on_fallback_to_operation_mode(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test is_on falls back to operationMode."""
        # Remove dhwOperationSetting to trigger fallback
        delattr(mock_device_status, "dhwOperationSetting")
        
        # Set all component statuses to False
        mock_device_status.dhwUse = False
        mock_device_status.compUse = False
        mock_device_status.heatUpperUse = False
        mock_device_status.heatLowerUse = False
        
        # Keep operationMode
        mock_device_status.operationMode.value = 32
        
        mac_address = mock_device.device_info.mac_address
        heater = NWP500WaterHeater(
            mock_coordinator, mac_address, mock_device
        )
        
        assert heater.is_on is True
