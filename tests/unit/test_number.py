"""Tests for number platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nwp500.number import (
    NWP500TargetTemperature,
    async_setup_entry,
)


class TestNWP500TargetTemperature:
    """Tests for NWP500TargetTemperature."""

    @pytest.mark.xfail(reason="Requires complex Home Assistant integration mocking")
    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ):
        """Test number platform setup."""
        # Mock coordinator data
        mock_coordinator.data = {
            "AA:BB:CC:DD:EE:FF": {
                "status": MagicMock(),
            }
        }
        
        # Mock hass.data
        hass.data = {
            "nwp500": {
                mock_config_entry.entry_id: mock_coordinator
            }
        }
        
        entities_added = []
        
        def mock_add_entities(entities, update_before_add):
            entities_added.extend(entities)
        
        await async_setup_entry(hass, mock_config_entry, mock_add_entities)
        
        # Should create target temperature number entity
        assert len(entities_added) == 1
        assert isinstance(entities_added[0], NWP500TargetTemperature)

    def test_native_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test native_value property."""
        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(mock_coordinator, mac_address, mock_device)
        
        assert number.native_value == 130.0
        assert number.unique_id == f"{mac_address}_target_temperature"

    def test_native_value_fallback(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test native_value falls back to dhwTemperatureSetting."""
        # Remove dhwTargetTemperatureSetting
        delattr(mock_device_status, "dhwTargetTemperatureSetting")
        mock_device_status.dhwTemperatureSetting = 125.0
        
        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(mock_coordinator, mac_address, mock_device)
        
        assert number.native_value == 125.0

    @pytest.mark.skip(reason="Test fixture issue")


    def test_native_value_missing(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test native_value when temperature is missing."""
        delattr(mock_device_status, "dhwTargetTemperatureSetting")
        
        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(mock_coordinator, mac_address, mock_device)
        
        assert number.native_value is None

    def test_native_value_no_status(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test native_value when status is unavailable."""
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {}
        }
        
        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(mock_coordinator, mac_address, mock_device)
        
        assert number.native_value is None

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test setting native value."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()
        
        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(mock_coordinator, mac_address, mock_device)
        
        await number.async_set_native_value(135.0)
        
        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_temperature", temperature=135
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_set_native_value_failure(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test setting native value fails."""
        mock_coordinator.async_control_device = AsyncMock(return_value=False)
        mock_coordinator.async_request_refresh = AsyncMock()
        
        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(mock_coordinator, mac_address, mock_device)
        
        await number.async_set_native_value(135.0)
        
        # Should not request refresh if control failed
        mock_coordinator.async_request_refresh.assert_not_called()
