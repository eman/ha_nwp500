"""Tests for config_flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nwp500.config_flow import (
    CannotConnect,
    InvalidAuth,
)
from custom_components.nwp500.const import DOMAIN


class TestConfigFlow:
    """Tests for ConfigFlow."""

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_form_user(self, hass: HomeAssistant):
        """Test we get the user form."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_form_user_success(self, hass: HomeAssistant):
        """Test successful user form submission."""
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            return_value={"title": "Test NWP500"},
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "email": "test@example.com",
                    "password": "test_password",
                },
            )
            
            assert result2["type"] == FlowResultType.CREATE_ENTRY
            assert result2["title"] == "Test NWP500"
            assert result2["data"]["email"] == "test@example.com"

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_form_cannot_connect(self, hass: HomeAssistant):
        """Test we handle cannot connect error."""
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            side_effect=CannotConnect("Connection failed"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "email": "test@example.com",
                    "password": "test_password",
                },
            )
            
            assert result2["type"] == FlowResultType.FORM
            assert result2["errors"] == {"base": "cannot_connect"}

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_form_invalid_auth(self, hass: HomeAssistant):
        """Test we handle invalid auth error."""
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            side_effect=InvalidAuth("Invalid credentials"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "email": "test@example.com",
                    "password": "wrong_password",
                },
            )
            
            assert result2["type"] == FlowResultType.FORM
            assert result2["errors"] == {"base": "invalid_auth"}

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_form_unexpected_exception(self, hass: HomeAssistant):
        """Test we handle unexpected exceptions."""
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            side_effect=Exception("Unexpected error"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "email": "test@example.com",
                    "password": "test_password",
                },
            )
            
            assert result2["type"] == FlowResultType.FORM
            assert result2["errors"] == {"base": "unknown"}

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_options_flow(
        self, hass: HomeAssistant, mock_config_entry: MagicMock
    ):
        """Test options flow."""
        mock_config_entry.add_to_hass(hass)
        
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

    @pytest.mark.skip(reason="Requires complex Home Assistant config_entries mocking")


    @pytest.mark.asyncio
    async def test_options_flow_save(
        self, hass: HomeAssistant, mock_config_entry: MagicMock
    ):
        """Test options flow saves data."""
        mock_config_entry.add_to_hass(hass)
        
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"scan_interval": 45},
        )
        
        assert result2["type"] == FlowResultType.CREATE_ENTRY
        assert result2["data"]["scan_interval"] == 45


@pytest.mark.asyncio
async def test_validate_input_success():
    """Test input validation succeeds."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", True), patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth_class, patch(
        "custom_components.nwp500.config_flow.NavienAPIClient"
    ) as mock_api_class:
        
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(return_value=mock_auth)
        mock_auth.__aexit__ = AsyncMock()
        mock_auth_class.return_value = mock_auth
        
        mock_api = MagicMock()
        mock_device = MagicMock()
        mock_device.device_info.device_name = "Test Water Heater"
        mock_device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
        mock_api.list_devices = AsyncMock(return_value=[mock_device])
        mock_api_class.return_value = mock_api
        
        from custom_components.nwp500.config_flow import validate_input
        
        result = await validate_input(
            MagicMock(),
            {"email": "test@example.com", "password": "test_password"},
        )
        
        assert "title" in result
        assert "Test Water Heater" in result["title"]


@pytest.mark.asyncio
async def test_validate_input_library_unavailable():
    """Test input validation fails when library is unavailable."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", False):
        from custom_components.nwp500.config_flow import validate_input
        
        with pytest.raises(CannotConnect):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "test_password"},
            )


@pytest.mark.skip(reason="Requires specific nwp500 library error message mocking")
@pytest.mark.asyncio
async def test_validate_input_auth_failure():
    """Test input validation fails on auth error."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", True), patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth_class:
        
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(side_effect=Exception("Auth failed"))
        mock_auth_class.return_value = mock_auth
        
        from custom_components.nwp500.config_flow import validate_input
        
        with pytest.raises(InvalidAuth):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "wrong_password"},
            )


@pytest.mark.skip(reason="Requires specific nwp500 library error message mocking")
@pytest.mark.asyncio
async def test_validate_input_no_devices():
    """Test input validation fails when no devices found."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", True), patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth_class, patch(
        "custom_components.nwp500.config_flow.NavienAPIClient"
    ) as mock_api_class:
        
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(return_value=mock_auth)
        mock_auth.__aexit__ = AsyncMock()
        mock_auth_class.return_value = mock_auth
        
        mock_api = MagicMock()
        mock_api.list_devices = AsyncMock(return_value=[])
        mock_api_class.return_value = mock_api
        
        from custom_components.nwp500.config_flow import validate_input
        
        with pytest.raises(CannotConnect):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "test_password"},
            )
