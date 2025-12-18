"""Tests for diagnostics support."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.nwp500.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_coordinator():
    """Test diagnostics when coordinator is not available."""
    mock_hass = MagicMock()
    mock_hass.data = {}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    result = await async_get_config_entry_diagnostics(mock_hass, mock_entry)

    assert result == {"error": "Integration not initialized"}


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_with_coordinator():
    """Test diagnostics when coordinator is available."""
    from custom_components.nwp500.const import DOMAIN

    mock_hass = MagicMock()
    mock_coordinator = MagicMock()
    mock_coordinator.mqtt_manager = None
    mock_coordinator.get_mqtt_telemetry = MagicMock(
        return_value={"request_id": "test"}
    )
    mock_coordinator.get_performance_stats = MagicMock(
        return_value={"update_count": 10}
    )

    mock_hass.data = {DOMAIN: {"test_entry": mock_coordinator}}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"
    mock_entry.version = 1

    result = await async_get_config_entry_diagnostics(mock_hass, mock_entry)

    assert result["entry_id"] == "test_entry"
    assert result["version"] == 1
    assert result["mqtt_manager_status"] == "MQTT manager not available"
    assert result["coordinator_telemetry"] == {"request_id": "test"}
    assert result["performance_stats"] == {"update_count": 10}
