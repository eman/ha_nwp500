"""Tests for __init__.py module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.nwp500 import (
    MODE_TO_DHW_ID,
    SERVICE_CLEAR_RESERVATIONS,
    SERVICE_REQUEST_RESERVATIONS,
    SERVICE_SET_RESERVATION,
    SERVICE_UPDATE_RESERVATIONS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.nwp500.const import DOMAIN


class TestInit:
    """Tests for component initialization."""

    pass


class TestModeMapping:
    """Tests for mode mapping constants."""

    def test_mode_to_dhw_id_contains_all_modes(self):
        """Test all expected modes are in the mapping."""
        expected_modes = [
            "heat_pump",
            "electric",
            "energy_saver",
            "high_demand",
            "vacation",
            "power_off",
        ]
        for mode in expected_modes:
            assert mode in MODE_TO_DHW_ID

    def test_mode_values_are_correct(self):
        """Test mode values match DHW operation setting IDs."""
        assert MODE_TO_DHW_ID["heat_pump"] == 1
        assert MODE_TO_DHW_ID["electric"] == 2
        assert MODE_TO_DHW_ID["energy_saver"] == 3
        assert MODE_TO_DHW_ID["high_demand"] == 4
        assert MODE_TO_DHW_ID["vacation"] == 5
        assert MODE_TO_DHW_ID["power_off"] == 6


@pytest.mark.asyncio
async def test_async_setup_entry_update_failed():
    """Test setup entry handles UpdateFailed correctly."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Mock coordinator that raises UpdateFailed
    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=UpdateFailed("Connection failed")
        )
        mock_coordinator_class.return_value = mock_coordinator

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(mock_hass, mock_entry)


@pytest.mark.asyncio
async def test_async_setup_entry_stores_coordinator():
    """Test setup entry stores coordinator in hass.data."""
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Mock coordinator that succeeds
    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True
        assert DOMAIN in mock_hass.data
        assert mock_entry.entry_id in mock_hass.data[DOMAIN]
        assert mock_hass.data[DOMAIN][mock_entry.entry_id] == mock_coordinator


@pytest.mark.asyncio
async def test_async_setup_entry_registers_services():
    """Test setup entry registers reservation services."""
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_entry(mock_hass, mock_entry)

        # Verify all 4 services were registered
        assert mock_hass.services.async_register.call_count == 4

        # Get all service names that were registered
        registered_services = [
            call[0][1]
            for call in mock_hass.services.async_register.call_args_list
        ]
        assert SERVICE_SET_RESERVATION in registered_services
        assert SERVICE_UPDATE_RESERVATIONS in registered_services
        assert SERVICE_CLEAR_RESERVATIONS in registered_services
        assert SERVICE_REQUEST_RESERVATIONS in registered_services


@pytest.mark.asyncio
async def test_async_setup_entry_skips_service_registration_if_exists():
    """Test setup entry skips registration if services already exist."""
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=True)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_entry(mock_hass, mock_entry)

        # Services should not be registered again
        mock_hass.services.async_register.assert_not_called()


@pytest.mark.asyncio
async def test_async_unload_entry_cleanup():
    """Test unload entry performs proper cleanup."""
    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Setup hass.data with mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.async_shutdown = AsyncMock()
    mock_hass.data = {DOMAIN: {mock_entry.entry_id: mock_coordinator}}

    # Mock successful platform unload
    mock_hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=True
    )

    result = await async_unload_entry(mock_hass, mock_entry)

    assert result is True
    # Verify coordinator was shut down
    mock_coordinator.async_shutdown.assert_called_once()
    # Verify coordinator was removed from hass.data
    assert mock_entry.entry_id not in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_unload_entry_removes_services_when_last():
    """Test unload removes services when last entry is unloaded."""
    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Setup hass.data with only one coordinator (last entry)
    mock_coordinator = MagicMock()
    mock_coordinator.async_shutdown = AsyncMock()
    mock_hass.data = {DOMAIN: {mock_entry.entry_id: mock_coordinator}}

    mock_hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=True
    )
    mock_hass.services.async_remove = MagicMock()

    await async_unload_entry(mock_hass, mock_entry)

    # All 4 services should be removed
    assert mock_hass.services.async_remove.call_count == 4
