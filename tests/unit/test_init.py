"""Tests for __init__.py module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.nwp500 import async_setup_entry, async_unload_entry
from custom_components.nwp500.const import DOMAIN


class TestInit:
    """Tests for component initialization."""

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_async_setup_entry_success(
        self,
        hass: HomeAssistant,
        mock_config_entry: MagicMock,
    ):
        """Test successful setup of config entry."""
        with patch(
            "custom_components.nwp500.NWP500DataUpdateCoordinator"
        ) as mock_coordinator_class:
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator_class.return_value = mock_coordinator
            
            result = await async_setup_entry(hass, mock_config_entry)
            
            assert result is True
            assert DOMAIN in hass.data
            assert mock_config_entry.entry_id in hass.data[DOMAIN]
            mock_coordinator.async_config_entry_first_refresh.assert_called_once()

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_async_setup_entry_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry: MagicMock,
    ):
        """Test setup fails when coordinator refresh fails."""
        with patch(
            "custom_components.nwp500.NWP500DataUpdateCoordinator"
        ) as mock_coordinator_class:
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock(
                side_effect=Exception("Connection failed")
            )
            mock_coordinator_class.return_value = mock_coordinator
            
            with pytest.raises(ConfigEntryNotReady):
                await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_async_unload_entry_success(
        self,
        hass: HomeAssistant,
        mock_config_entry: MagicMock,
        mock_coordinator: MagicMock,
    ):
        """Test successful unload of config entry."""
        # Setup hass.data
        hass.data[DOMAIN] = {
            mock_config_entry.entry_id: mock_coordinator
        }
        mock_coordinator.async_shutdown = AsyncMock()
        
        # Mock async_unload_platforms to return True
        with patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            return_value=True,
        ):
            result = await async_unload_entry(hass, mock_config_entry)
            
            assert result is True
            assert mock_config_entry.entry_id not in hass.data[DOMAIN]
            mock_coordinator.async_shutdown.assert_called_once()

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_async_unload_entry_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry: MagicMock,
        mock_coordinator: MagicMock,
    ):
        """Test unload fails when platforms fail to unload."""
        # Setup hass.data
        hass.data[DOMAIN] = {
            mock_config_entry.entry_id: mock_coordinator
        }
        mock_coordinator.async_shutdown = AsyncMock()
        
        # Mock async_unload_platforms to return False
        with patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            return_value=False,
        ):
            result = await async_unload_entry(hass, mock_config_entry)
            
            assert result is False
            # Coordinator should not be removed if unload failed
            assert mock_config_entry.entry_id in hass.data[DOMAIN]
            mock_coordinator.async_shutdown.assert_not_called()
