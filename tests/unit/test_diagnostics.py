"""Tests for the diagnostics module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from custom_components.nwp500.diagnostics import (
    async_get_config_entry_diagnostics,
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
async def test_diagnostics_reports_location_keys_but_redacts_values(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Location appears in diagnostics as structure only, never as values."""
    device = MagicMock()
    device.device_info.device_name = "NWP500"
    device.device_info.device_type = 52
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.connected = True
    device.location.address = "123 Main St"
    device.location.city = "San Rafael"
    device.location.state = "CA"
    device.location.latitude = 37.9735
    device.location.longitude = -122.5311
    device.location.altitude = 12.0

    mock_coordinator.mqtt_manager = None
    mock_coordinator.devices = [device]

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    location = result["devices"][0]["location"]
    # Keys retained so maintainers can see which fields the cloud populated.
    assert set(location) == {
        "address",
        "city",
        "state",
        "latitude",
        "longitude",
        "altitude",
    }
    assert all(value == "**REDACTED**" for value in location.values())

    # No PII leaks anywhere else in the payload.
    for leaked in ("123 Main St", "San Rafael", "37.9735", "-122.5311"):
        assert leaked not in str(result)


@pytest.mark.asyncio
async def test_diagnostics_omits_unpopulated_location_fields(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Unset location fields are omitted, not redacted.

    Redaction replaces values by key, so emitting every key unconditionally
    would render an unset field indistinguishable from a populated one.
    """
    device = MagicMock()
    device.device_info.device_name = "NWP500"
    device.device_info.device_type = 52
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.connected = True
    device.location.address = None
    device.location.city = "San Rafael"
    device.location.state = "CA"
    device.location.latitude = None
    device.location.longitude = None
    device.location.altitude = None

    mock_coordinator.mqtt_manager = None
    mock_coordinator.devices = [device]

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert set(result["devices"][0]["location"]) == {"city", "state"}


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_mqtt_manager(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when MQTT manager not available."""
    mock_coordinator.mqtt_manager = None

    mock_config_entry.runtime_data = mock_coordinator

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
        "last_connect_time": "2024-01-01T00:00:00Z",
    }

    mock_diagnostics = MagicMock()
    mock_diagnostics.export_json.return_value = '{"test": "data"}'
    mock_mqtt_manager.diagnostics = mock_diagnostics

    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {
        "messages_sent": 10,
        "messages_received": 5,
    }
    mock_coordinator.get_performance_stats.return_value = {
        "update_count": 100,
        "error_count": 2,
    }

    mock_config_entry.runtime_data = mock_coordinator

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

    mock_config_entry.runtime_data = mock_coordinator

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

    mock_config_entry.runtime_data = mock_coordinator

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

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "mqtt_diagnostics_status" in result
    assert (
        result["mqtt_diagnostics_status"]
        == "Diagnostics collector not initialized"
    )
