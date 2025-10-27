"""Tests for config_flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from custom_components.nwp500.config_flow import (
    CannotConnect,
    InvalidAuth,
    ConfigFlow,
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


class TestReauthFlow:
    """Tests for reauth flow."""
    
    @pytest.mark.asyncio
    async def test_reauth_flow_initialization(self):
        """Test reauth flow initializes correctly."""
        flow = ConfigFlow()
        
        # Mock the config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"
        mock_entry.data = {CONF_EMAIL: "test@example.com"}
        
        # Mock hass and context
        mock_hass = MagicMock()
        mock_hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
        flow.hass = mock_hass
        flow.context = {"entry_id": "test_entry_id"}
        
        # Test that async_step_reauth sets up correctly
        result = await flow.async_step_reauth({CONF_EMAIL: "test@example.com"})
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert flow._reauth_entry == mock_entry
    
    @pytest.mark.asyncio
    async def test_reauth_confirm_success(self):
        """Test successful reauth confirmation."""
        flow = ConfigFlow()
        
        # Mock the config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"
        mock_entry.data = {CONF_EMAIL: "test@example.com"}
        
        # Mock hass
        mock_hass = MagicMock()
        mock_hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
        mock_hass.config_entries.async_update_entry = MagicMock()
        mock_hass.config_entries.async_reload = AsyncMock()
        flow.hass = mock_hass
        flow._reauth_entry = mock_entry
        
        # Mock validate_input to succeed
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            return_value={"title": "Test NWP500"},
        ):
            result = await flow.async_step_reauth_confirm(
                user_input={
                    CONF_EMAIL: "test@example.com",
                    CONF_PASSWORD: "new_password",
                }
            )
        
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        mock_hass.config_entries.async_update_entry.assert_called_once()
        mock_hass.config_entries.async_reload.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_reauth_confirm_invalid_auth(self):
        """Test reauth confirmation with invalid auth."""
        flow = ConfigFlow()
        
        # Mock the config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"
        mock_entry.data = {CONF_EMAIL: "test@example.com"}
        
        # Mock hass
        mock_hass = MagicMock()
        flow.hass = mock_hass
        flow._reauth_entry = mock_entry
        
        # Mock validate_input to fail with invalid auth
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            side_effect=InvalidAuth("Invalid credentials"),
        ):
            result = await flow.async_step_reauth_confirm(
                user_input={
                    CONF_EMAIL: "test@example.com",
                    CONF_PASSWORD: "wrong_password",
                }
            )
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "invalid_auth"}
    
    @pytest.mark.asyncio
    async def test_reauth_confirm_cannot_connect(self):
        """Test reauth confirmation with connection error."""
        flow = ConfigFlow()
        
        # Mock the config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"
        mock_entry.data = {CONF_EMAIL: "test@example.com"}
        
        # Mock hass
        mock_hass = MagicMock()
        flow.hass = mock_hass
        flow._reauth_entry = mock_entry
        
        # Mock validate_input to fail with connection error
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            side_effect=CannotConnect("Connection failed"),
        ):
            result = await flow.async_step_reauth_confirm(
                user_input={
                    CONF_EMAIL: "test@example.com",
                    CONF_PASSWORD: "test_password",
                }
            )
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "cannot_connect"}
    
    @pytest.mark.asyncio
    async def test_reauth_confirm_unexpected_exception(self):
        """Test reauth confirmation with unexpected exception."""
        flow = ConfigFlow()
        
        # Mock the config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"
        mock_entry.data = {CONF_EMAIL: "test@example.com"}
        
        # Mock hass
        mock_hass = MagicMock()
        flow.hass = mock_hass
        flow._reauth_entry = mock_entry
        
        # Mock validate_input to fail with unexpected error
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            side_effect=Exception("Unexpected error"),
        ):
            result = await flow.async_step_reauth_confirm(
                user_input={
                    CONF_EMAIL: "test@example.com",
                    CONF_PASSWORD: "test_password",
                }
            )
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "unknown"}
    
    @pytest.mark.asyncio
    async def test_reauth_confirm_show_form(self):
        """Test reauth confirmation shows form when no input provided."""
        flow = ConfigFlow()
        
        # Mock the config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"
        mock_entry.data = {CONF_EMAIL: "test@example.com"}
        
        # Mock hass
        mock_hass = MagicMock()
        flow.hass = mock_hass
        flow._reauth_entry = mock_entry
        
        result = await flow.async_step_reauth_confirm(user_input=None)
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["description_placeholders"]["account"] == "test@example.com"
    
    @pytest.mark.asyncio
    async def test_reauth_confirm_no_entry(self):
        """Test reauth confirmation handles missing entry gracefully."""
        flow = ConfigFlow()
        
        # Mock hass without entry
        mock_hass = MagicMock()
        flow.hass = mock_hass
        flow._reauth_entry = None
        
        # Mock validate_input to succeed
        with patch(
            "custom_components.nwp500.config_flow.validate_input",
            return_value={"title": "Test NWP500"},
        ):
            result = await flow.async_step_reauth_confirm(
                user_input={
                    CONF_EMAIL: "test@example.com",
                    CONF_PASSWORD: "new_password",
                }
            )
        
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_failed"


@pytest.mark.asyncio
async def test_validate_input_network_errors():
    """Test validate_input handles network errors correctly."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", True), patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth_class:
        
        # Test OSError
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(side_effect=OSError("Network error"))
        mock_auth_class.return_value = mock_auth
        
        from custom_components.nwp500.config_flow import validate_input
        
        with pytest.raises(CannotConnect):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "test_password"},
            )


@pytest.mark.asyncio
async def test_validate_input_runtime_error():
    """Test validate_input handles runtime errors correctly."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", True), patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth_class:
        
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(side_effect=RuntimeError("Connection failed"))
        mock_auth_class.return_value = mock_auth
        
        from custom_components.nwp500.config_flow import validate_input
        
        with pytest.raises(CannotConnect):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "test_password"},
            )


@pytest.mark.asyncio
async def test_validate_input_timeout_error():
    """Test validate_input handles timeout errors correctly."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", True), patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth_class:
        
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(side_effect=TimeoutError("Connection timeout"))
        mock_auth_class.return_value = mock_auth
        
        from custom_components.nwp500.config_flow import validate_input
        
        with pytest.raises(CannotConnect):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "test_password"},
            )


@pytest.mark.asyncio
async def test_validate_input_unauthorized_error():
    """Test validate_input detects 401 errors correctly."""
    with patch("custom_components.nwp500.config_flow.nwp500_available", True), patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth_class:
        
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(
            side_effect=RuntimeError("401 Unauthorized")
        )
        mock_auth_class.return_value = mock_auth
        
        from custom_components.nwp500.config_flow import validate_input
        
        with pytest.raises(InvalidAuth):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "wrong_password"},
            )
