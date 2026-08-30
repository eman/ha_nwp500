"""Tests for config_flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from nwp500.exceptions import AuthenticationError, InvalidCredentialsError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500.config_flow import (
    CannotConnect,
    ConfigFlow,
    InvalidAuth,
)
from custom_components.nwp500.const import DOMAIN


class TestConfigFlow:
    """Tests for ConfigFlow."""

    pass


@pytest.mark.asyncio
async def test_validate_input_success():
    """Test input validation succeeds."""
    with (
        patch("custom_components.nwp500.config_flow.nwp500_available", True),
        patch(
            "custom_components.nwp500.config_flow.NavienAuthClient"
        ) as mock_auth_class,
        patch(
            "custom_components.nwp500.config_flow.NavienAPIClient"
        ) as mock_api_class,
    ):
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


@pytest.mark.asyncio
async def test_validate_input_auth_failure():
    """Test input validation fails on auth error."""
    with (
        patch("custom_components.nwp500.config_flow.nwp500_available", True),
        patch(
            "custom_components.nwp500.config_flow.NavienAuthClient"
        ) as mock_auth_class,
    ):
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(
            side_effect=InvalidCredentialsError("Invalid email or password")
        )
        mock_auth_class.return_value = mock_auth

        from custom_components.nwp500.config_flow import validate_input

        with pytest.raises(InvalidAuth):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "wrong_password"},
            )


@pytest.mark.asyncio
async def test_validate_input_no_devices():
    """Test input validation fails when no devices found."""
    with (
        patch("custom_components.nwp500.config_flow.nwp500_available", True),
        patch(
            "custom_components.nwp500.config_flow.NavienAuthClient"
        ) as mock_auth_class,
        patch(
            "custom_components.nwp500.config_flow.NavienAPIClient"
        ) as mock_api_class,
    ):
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(return_value=mock_auth)
        mock_auth.__aexit__ = AsyncMock(return_value=False)
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
        mock_hass.config_entries.async_get_entry = MagicMock(
            return_value=mock_entry
        )
        flow.hass = mock_hass
        flow.context = {"entry_id": "test_entry_id"}

        # Test that async_step_reauth sets up correctly
        result = await flow.async_step_reauth({CONF_EMAIL: "test@example.com"})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert flow._reauth_entry == mock_entry

    # test_reauth_confirm_success lived here. It drove ConfigFlow directly
    # against a MagicMock hass and patched out async_update_reload_and_abort,
    # which meant it mocked away the unique-ID handling that decides whether
    # reauth is even allowed to write. See
    # TestReconfigureFlow.test_reauth_same_account_updates_password, which
    # exercises the same path against real flow machinery.

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
        assert (
            result["description_placeholders"]["account"] == "test@example.com"
        )

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
    with (
        patch("custom_components.nwp500.config_flow.nwp500_available", True),
        patch(
            "custom_components.nwp500.config_flow.NavienAuthClient"
        ) as mock_auth_class,
    ):
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
    with (
        patch("custom_components.nwp500.config_flow.nwp500_available", True),
        patch(
            "custom_components.nwp500.config_flow.NavienAuthClient"
        ) as mock_auth_class,
    ):
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(
            side_effect=RuntimeError("Connection failed")
        )
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
    with (
        patch("custom_components.nwp500.config_flow.nwp500_available", True),
        patch(
            "custom_components.nwp500.config_flow.NavienAuthClient"
        ) as mock_auth_class,
    ):
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(
            side_effect=TimeoutError("Connection timeout")
        )
        mock_auth_class.return_value = mock_auth

        from custom_components.nwp500.config_flow import validate_input

        with pytest.raises(CannotConnect):
            await validate_input(
                MagicMock(),
                {"email": "test@example.com", "password": "test_password"},
            )


async def _validate_input_raising(err: Exception):
    """Run validate_input with the auth client raising `err`."""
    with (
        patch("custom_components.nwp500.config_flow.nwp500_available", True),
        patch(
            "custom_components.nwp500.config_flow.NavienAuthClient"
        ) as mock_auth_class,
    ):
        mock_auth = AsyncMock()
        mock_auth.__aenter__ = AsyncMock(side_effect=err)
        mock_auth_class.return_value = mock_auth

        from custom_components.nwp500.config_flow import validate_input

        return await validate_input(
            MagicMock(),
            {"email": "test@example.com", "password": "wrong_password"},
        )


@pytest.mark.asyncio
async def test_validate_input_invalid_credentials():
    """Rejected credentials are reported as invalid auth, not a connection failure."""
    with pytest.raises(InvalidAuth):
        await _validate_input_raising(
            InvalidCredentialsError("Invalid email or password")
        )


@pytest.mark.asyncio
async def test_validate_input_non_retriable_auth_error():
    """A definitive authentication failure is reported as invalid auth."""
    err = AuthenticationError("Account locked")
    err.retriable = False

    with pytest.raises(InvalidAuth):
        await _validate_input_raising(err)


@pytest.mark.asyncio
async def test_validate_input_retriable_auth_error_is_connection_error():
    """A transient auth failure must not accuse the user's password.

    The library marks network failures during authentication as retriable.
    Reporting one as invalid_auth would send the user to change a password
    that is fine.
    """
    err = AuthenticationError("Temporary network failure")
    err.retriable = True

    with pytest.raises(CannotConnect):
        await _validate_input_raising(err)


@pytest.mark.asyncio
async def test_validate_input_unrecognized_error_is_connection_error():
    """Errors the library does not classify fall back to cannot_connect."""
    with pytest.raises(CannotConnect):
        await _validate_input_raising(RuntimeError("401 Unauthorized"))


class TestReconfigureFlow:
    """Tests driving the real Home Assistant flow machinery.

    These exercise async_step_reconfigure / async_step_reauth_confirm end to
    end rather than mocking async_update_reload_and_abort, because the bug
    they guard against lived in the abort helper that runs *before* it.
    """

    @staticmethod
    def _patch_library():
        """Patch the library so validate_input succeeds."""
        auth = AsyncMock()
        auth.__aenter__ = AsyncMock(return_value=auth)
        auth.__aexit__ = AsyncMock()

        device = MagicMock()
        device.device_info.device_name = "NWP500"
        api = AsyncMock()
        api.list_devices = AsyncMock(return_value=[device])

        return (
            patch(
                "custom_components.nwp500.config_flow.nwp500_available", True
            ),
            patch(
                "custom_components.nwp500.config_flow.NavienAuthClient",
                return_value=auth,
            ),
            patch(
                "custom_components.nwp500.config_flow.NavienAPIClient",
                return_value=api,
            ),
        )

    @staticmethod
    def _entry(hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="test@example.com",
            data={
                CONF_EMAIL: "test@example.com",
                CONF_PASSWORD: "old-password",
            },
            title="Navien NWP500",
        )
        entry.add_to_hass(hass)
        return entry

    @pytest.mark.asyncio
    async def test_reconfigure_same_account_updates_password(
        self, hass, enable_custom_integrations
    ):
        """Reconfiguring with the same email must store the new password.

        Regression test: _abort_if_unique_id_configured matched the entry
        being reconfigured and aborted with "already_configured", so the new
        password was silently discarded.
        """
        entry = self._entry(hass)
        available, auth, api = self._patch_library()

        with available, auth, api:
            result = await entry.start_reconfigure_flow(hass)
            assert result["step_id"] == "reconfigure"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_EMAIL: "test@example.com",
                    CONF_PASSWORD: "new-password",
                },
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_PASSWORD] == "new-password"
        # The old code merged updates={"title": ...} into entry.data.
        assert "title" not in entry.data

    @pytest.mark.asyncio
    async def test_reconfigure_different_account_is_rejected(
        self, hass, enable_custom_integrations
    ):
        """A different account must not be bound to an existing entry."""
        entry = self._entry(hass)
        available, auth, api = self._patch_library()

        with available, auth, api:
            result = await entry.start_reconfigure_flow(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_EMAIL: "someone-else@example.com",
                    CONF_PASSWORD: "their-password",
                },
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_account"
        assert entry.data[CONF_EMAIL] == "test@example.com"
        assert entry.data[CONF_PASSWORD] == "old-password"

    @pytest.mark.asyncio
    async def test_reauth_same_account_updates_password(
        self, hass, enable_custom_integrations
    ):
        """Reauth with the entry's own account stores the new password."""
        entry = self._entry(hass)
        available, auth, api = self._patch_library()

        with available, auth, api:
            result = await entry.start_reauth_flow(hass)
            assert result["step_id"] == "reauth_confirm"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_EMAIL: "test@example.com",
                    CONF_PASSWORD: "new-password",
                },
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "new-password"

    @pytest.mark.asyncio
    async def test_reauth_different_account_is_rejected(
        self, hass, enable_custom_integrations
    ):
        """Reauth must not silently rebind the entry to another account.

        Every device, entity and MQTT subscription is keyed to MACs from the
        original account, so accepting a different login would leave the
        registry describing devices the entry no longer talks to.
        """
        entry = self._entry(hass)
        available, auth, api = self._patch_library()

        with available, auth, api:
            result = await entry.start_reauth_flow(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_EMAIL: "someone-else@example.com",
                    CONF_PASSWORD: "their-password",
                },
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_account"
        assert entry.data[CONF_EMAIL] == "test@example.com"
        assert entry.data[CONF_PASSWORD] == "old-password"
