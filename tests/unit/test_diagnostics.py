"""Tests for the diagnostics module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.nwp500.diagnostics import (
    async_get_config_entry_diagnostics,
    async_setup_diagnostics_export,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_coordinator(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test diagnostics when coordinator not initialized."""
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    
    assert result == {"error": "Integration not initialized"}


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_mqtt_manager(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when MQTT manager not available."""
    mock_coordinator.mqtt_manager = None
    
    hass.data = {
        "nwp500": {
            mock_config_entry.entry_id: mock_coordinator
        }
    }
    
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    
    assert "entry_id" in result
    assert "version" in result
    assert result["mqtt_manager_status"] == "MQTT manager not available"


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_with_mqtt_diagnostics(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics with MQTT diagnostics available."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {
        "connected": True,
        "last_connect_time": "2024-01-01T00:00:00Z"
    }
    
    mock_diagnostics = MagicMock()
    mock_diagnostics.export_json.return_value = '{"test": "data"}'
    mock_mqtt_manager.diagnostics = mock_diagnostics
    
    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {
        "messages_sent": 10,
        "messages_received": 5
    }
    mock_coordinator.get_performance_stats.return_value = {
        "update_count": 100,
        "error_count": 2
    }
    
    hass.data = {
        "nwp500": {
            mock_config_entry.entry_id: mock_coordinator
        }
    }
    
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    
    assert "entry_id" in result
    assert "version" in result
    assert "mqtt_connection_state" in result
    assert result["mqtt_connection_state"]["connected"] is True
    assert "mqtt_diagnostics" in result
    assert result["mqtt_diagnostics"]["test"] == "data"
    assert "coordinator_telemetry" in result
    assert "performance_stats" in result


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_invalid_json(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when export returns invalid format."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {}
    
    mock_diagnostics = MagicMock()
    mock_diagnostics.export_json.return_value = 12345  # Not a string
    mock_mqtt_manager.diagnostics = mock_diagnostics
    
    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {}
    mock_coordinator.get_performance_stats.return_value = {}
    
    hass.data = {
        "nwp500": {
            mock_config_entry.entry_id: mock_coordinator
        }
    }
    
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    
    assert "mqtt_diagnostics_error" in result
    assert "Invalid diagnostics format" in result["mqtt_diagnostics_error"]


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_export_exception(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when export raises exception."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {}
    
    mock_diagnostics = MagicMock()
    mock_diagnostics.export_json.side_effect = ValueError("Export failed")
    mock_mqtt_manager.diagnostics = mock_diagnostics
    
    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {}
    mock_coordinator.get_performance_stats.return_value = {}
    
    hass.data = {
        "nwp500": {
            mock_config_entry.entry_id: mock_coordinator
        }
    }
    
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    
    assert "mqtt_diagnostics_error" in result
    assert "Export failed" in result["mqtt_diagnostics_error"]


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_diagnostics_collector(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when diagnostics collector not initialized."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {}
    mock_mqtt_manager.diagnostics = None
    
    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {}
    mock_coordinator.get_performance_stats.return_value = {}
    
    hass.data = {
        "nwp500": {
            mock_config_entry.entry_id: mock_coordinator
        }
    }
    
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    
    assert "mqtt_diagnostics_status" in result
    assert result["mqtt_diagnostics_status"] == "Diagnostics collector not initialized"


@pytest.mark.asyncio
async def test_async_setup_diagnostics_export_in_ci(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test diagnostics export setup skips in CI environment."""
    with patch.dict("os.environ", {"CI": "true"}):
        # Should return early and not raise any errors
        await async_setup_diagnostics_export(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_async_setup_diagnostics_export_in_testing(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test diagnostics export setup skips in testing environment."""
    with patch.dict("os.environ", {"TESTING": "true"}):
        # Should return early and not raise any errors
        await async_setup_diagnostics_export(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_async_setup_diagnostics_export_no_coordinator(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test diagnostics export setup when coordinator not available."""
    # Should return early without errors
    await async_setup_diagnostics_export(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_async_setup_diagnostics_export_no_mqtt_manager(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics export setup when MQTT manager not available."""
    mock_coordinator.mqtt_manager = None
    
    hass.data = {
        "nwp500": {
            mock_config_entry.entry_id: mock_coordinator
        }
    }
    
    # Should return early without errors
    await async_setup_diagnostics_export(hass, mock_config_entry)
