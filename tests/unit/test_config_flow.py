"""Tests for config_flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
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
async def test_validate_input_service_error_is_not_a_bad_password():
    """A non-401 service failure must not accuse the user's password.

    nwp500-python raises InvalidCredentialsError only for a 401 or an
    "invalid"/"unauthorized" message, and turns every other non-200 from
    that same response into a bare AuthenticationError -- which defaults to
    retriable=False. Mapping that to invalid_auth would tell the user to
    change a working password because Navien returned a 500.
    """
    err = AuthenticationError("Authentication failed: internal server error")
    err.retriable = False

    with pytest.raises(CannotConnect):
        await _validate_input_raising(err)


@pytest.mark.asyncio
async def test_validate_input_malformed_response_is_a_connection_error():
    """The library reports unparseable responses the same way."""
    err = AuthenticationError("Invalid response format: expecting value")
    err.retriable = False

    with pytest.raises(CannotConnect):
        await _validate_input_raising(err)


@pytest.mark.asyncio
async def test_validate_input_retriable_auth_error_is_connection_error():
    """A transient auth failure is a connection problem too."""
    err = AuthenticationError("Network error: connection reset")
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


# --- Options: external control (issue #158) ----------------------------------


class TestExternalControlOptions:
    """The options step that configures the feature."""

    @staticmethod
    def _handler(hass: HomeAssistant, options: dict | None = None):
        from custom_components.nwp500.config_flow import OptionsFlowHandler

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "x"},
            options=options or {},
        )
        entry.add_to_hass(hass)
        handler = OptionsFlowHandler()
        handler.hass = hass
        handler.handler = entry.entry_id
        return handler, entry

    @staticmethod
    def _control_input(**overrides):
        """What the frontend sends for the second page, validated by it."""
        return {
            "control_intent_entity": "sensor.intent",
            "control_mode": "shadow",
            "control_surplus_threshold_kw": 0.45,
            "control_allowed_modes": ["energy_saver", "heat_pump"],
            "control_assisted_mode": "energy_saver",
            "control_min_run_before_lower_min": 120.0,
            "control_reservation_entry_limit": 7.0,
            "control_reservation_entry_reserve": 2.0,
            **overrides,
        }

    @pytest.mark.asyncio
    async def test_init_form_offers_the_toggle(self, hass: HomeAssistant):
        handler, _ = self._handler(hass)

        result = await handler.async_step_init()

        assert result["type"] == FlowResultType.FORM
        keys = {str(key) for key in result["data_schema"].schema}
        assert keys == {"scan_interval", "control_enabled"}

    @pytest.mark.asyncio
    async def test_toggle_off_keeps_the_other_options(
        self, hass: HomeAssistant
    ):
        handler, _ = self._handler(
            hass, {"control_enabled": True, "control_intent_entity": "sensor.i"}
        )

        result = await handler.async_step_init(
            {"scan_interval": 45, "control_enabled": False}
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == {
            "scan_interval": 45,
            "control_enabled": False,
            "control_intent_entity": "sensor.i",
        }

    @pytest.mark.asyncio
    async def test_toggle_on_shows_the_control_form(self, hass: HomeAssistant):
        handler, _ = self._handler(hass)

        result = await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "external_control"

    @pytest.mark.asyncio
    async def test_control_form_is_stored_normalised(self, hass: HomeAssistant):
        hass.config.units = US_CUSTOMARY_SYSTEM
        handler, _ = self._handler(hass)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )
        form = await handler.async_step_external_control()
        user_input = form["data_schema"](
            self._control_input(
                control_setpoint_min=120.0, control_setpoint_max=145.0
            )
        )

        result = await handler.async_step_external_control(user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        data = result["data"]
        assert data["scan_interval"] == 30
        assert data["control_enabled"] is True
        assert data["control_intent_entity"] == "sensor.intent"
        assert data["control_mode"] == "shadow"
        assert data["control_setpoint_min_f"] == 120.0
        assert data["control_setpoint_max_f"] == 145.0
        assert data["control_min_run_before_lower_min"] == 120
        assert isinstance(data["control_reservation_entry_limit"], int)
        assert "control_setpoint_min" not in data
        assert "control_surplus_entity" not in data

    @pytest.mark.asyncio
    async def test_setpoints_are_taken_in_celsius_and_stored_in_fahrenheit(
        self, hass: HomeAssistant
    ):
        hass.config.units = METRIC_SYSTEM
        handler, _ = self._handler(hass, {"control_setpoint_min_f": 122.0})
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        form = await handler.async_step_external_control()
        suggested = {
            str(key): key.description.get("suggested_value")
            for key in form["data_schema"].schema
            if key.description
        }
        assert suggested["control_setpoint_min"] == 50.0

        user_input = form["data_schema"](
            self._control_input(
                control_setpoint_min=48.9, control_setpoint_max=65.0
            )
        )
        result = await handler.async_step_external_control(user_input)

        assert result["data"]["control_setpoint_min_f"] == 120.0
        assert result["data"]["control_setpoint_max_f"] == 149.0

    @pytest.mark.asyncio
    async def test_an_empty_optional_field_clears_the_stored_value(
        self, hass: HomeAssistant
    ):
        handler, _ = self._handler(
            hass,
            {
                "control_setpoint_min_f": 120.0,
                "control_surplus_entity": "binary_sensor.surplus",
            },
        )
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        result = await handler.async_step_external_control(
            self._control_input()
        )

        assert "control_setpoint_min_f" not in result["data"]
        assert "control_surplus_entity" not in result["data"]

    @pytest.mark.asyncio
    async def test_existing_values_are_suggested(self, hass: HomeAssistant):
        handler, _ = self._handler(
            hass,
            {
                "control_intent_entity": "sensor.i",
                "control_allowed_modes": ["electric"],
                "control_daily_revert_time": "04:30",
            },
        )
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        form = await handler.async_step_external_control()

        suggested = {
            str(key): key.description.get("suggested_value")
            for key in form["data_schema"].schema
            if key.description
        }
        assert suggested["control_intent_entity"] == "sensor.i"
        assert suggested["control_allowed_modes"] == ["electric"]
        # A first-draft option has no field any more.
        assert "control_daily_revert_time" not in suggested

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("overrides", "field", "error"),
        [
            (
                {"control_setpoint_min": 140.0, "control_setpoint_max": 130.0},
                "control_setpoint_min",
                "setpoint_range",
            ),
            (
                {"control_reservation_entry_reserve": 7.0},
                "control_reservation_entry_reserve",
                "reserve_exceeds_limit",
            ),
            (
                {"control_allowed_modes": []},
                "control_allowed_modes",
                "allowed_modes_empty",
            ),
            (
                {"control_assisted_mode": "electric"},
                "control_assisted_mode",
                "assisted_mode_not_allowed",
            ),
        ],
    )
    async def test_cross_field_errors(
        self, hass: HomeAssistant, overrides, field, error
    ):
        handler, _ = self._handler(hass)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        result = await handler.async_step_external_control(
            self._control_input(**overrides)
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {field: error}
        # The user's answers come back as suggestions rather than being lost.
        suggested = {
            str(key): key.description.get("suggested_value")
            for key in result["data_schema"].schema
            if key.description
        }
        assert suggested["control_intent_entity"] == "sensor.intent"

    @pytest.mark.asyncio
    async def test_live_is_not_offered_while_the_gate_is_closed(
        self, hass: HomeAssistant, monkeypatch
    ):
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", False
        )
        handler, _ = self._handler(hass)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )
        form = await handler.async_step_external_control()

        with pytest.raises(vol.Invalid):
            form["data_schema"](self._control_input(control_mode="live"))

    @staticmethod
    def _with_heater(entry, schedule):
        status = MagicMock()
        status.dhw_operation_setting = 3
        status.dhw_target_temperature_setting_raw = 119
        coordinator = MagicMock()
        coordinator.data = {"AA:BB": {"status": status}}
        coordinator.reservation_schedules = (
            {"AA:BB": schedule} if schedule is not None else {}
        )
        entry.runtime_data = coordinator

    @pytest.mark.asyncio
    async def test_live_is_offered_once_the_gate_is_open(
        self, hass: HomeAssistant, monkeypatch
    ):
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        handler, _ = self._handler(hass)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )
        form = await handler.async_step_external_control()
        keys = {str(key) for key in form["data_schema"].schema}
        assert {"control_live_segments", "control_live_grants"} <= keys
        form["data_schema"](self._control_input(control_mode="live"))

    @pytest.mark.asyncio
    async def test_going_live_declares_the_owner_program(
        self, hass: HomeAssistant, monkeypatch
    ):
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        hass.config.units = US_CUSTOMARY_SYSTEM
        handler, entry = self._handler(hass)
        entry_ = {
            "enable": 2,
            "week": 124,
            "hour": 6,
            "min": 0,
            "mode": 3,
            "param": 120,
        }
        self._with_heater(
            entry, {"reservation_use": 2, "reservation": [entry_]}
        )
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        result = await handler.async_step_external_control(
            self._control_input(
                control_mode="live",
                control_live_segments=True,
                control_live_grants=False,
            )
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "going_live"
        summary = result["description_placeholders"]["program"]
        assert "Mode: Energy Saver" in summary
        assert "Setpoint: 139.1 °F" in summary
        assert "Mon Tue Wed Thu Fri 06:00" in summary
        assert "switched off while live" in summary

        result = await handler.async_step_going_live({})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["control_mode"] == "live"
        assert result["data"]["control_live_segments"] is True
        assert result["data"]["control_owner_program"] == {
            "AA:BB": {
                "mode": "energy_saver",
                "setpoint_raw": 119,
                "reservations_enabled": True,
                "entries": [entry_],
                "declared": True,
            }
        }

    @pytest.mark.asyncio
    async def test_going_live_waits_for_the_heater(
        self, hass: HomeAssistant, monkeypatch
    ):
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        handler, entry = self._handler(hass)
        self._with_heater(entry, None)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )
        await handler.async_step_external_control(
            self._control_input(control_mode="live")
        )

        result = await handler.async_step_going_live({})

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "owner_snapshot_unavailable"}

    @pytest.mark.asyncio
    async def test_every_save_in_live_confirms_the_owner_program(
        self, hass: HomeAssistant, monkeypatch
    ):
        """The owner may have changed their program since it was declared."""
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        declared = {"AA:BB": {"mode": "heat_pump", "setpoint_raw": 120}}
        handler, entry = self._handler(
            hass,
            {
                "control_mode": "live",
                "control_owner_program": declared,
            },
        )
        entry_ = {
            "enable": 2,
            "week": 2,
            "hour": 7,
            "min": 0,
            "mode": 3,
            "param": 118,
        }
        self._with_heater(
            entry, {"reservation_use": 2, "reservation": [entry_]}
        )
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        result = await handler.async_step_external_control(
            self._control_input(control_mode="live")
        )

        assert result["step_id"] == "going_live"
        result = await handler.async_step_going_live({})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["control_owner_program"]["AA:BB"]["entries"] == [
            entry_
        ]

    @pytest.mark.asyncio
    async def test_a_heater_added_after_going_live_is_declared(
        self, hass: HomeAssistant, monkeypatch
    ):
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        handler, entry = self._handler(
            hass,
            {
                "control_mode": "live",
                "control_owner_program": {"OTHER": {"mode": "heat_pump"}},
            },
        )
        self._with_heater(entry, {"reservation_use": 1, "reservation": []})
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        result = await handler.async_step_external_control(
            self._control_input(control_mode="live")
        )

        assert result["step_id"] == "going_live"

    @pytest.mark.asyncio
    async def test_going_live_before_the_entry_has_loaded(
        self, hass: HomeAssistant, monkeypatch
    ):
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        handler, _ = self._handler(hass)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        result = await handler.async_step_external_control(
            self._control_input(control_mode="live")
        )

        assert result["step_id"] == "going_live"
        assert result["errors"] == {"base": "owner_snapshot_unavailable"}

    @pytest.mark.asyncio
    async def test_a_heater_that_may_hold_the_list_keeps_its_declaration(
        self, hass: HomeAssistant, hass_storage, monkeypatch
    ):
        from custom_components.nwp500.control.store import ControlStore

        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        declared = {
            "mode": "heat_pump",
            "setpoint_raw": 120,
            "reservations_enabled": False,
            "entries": [],
            "declared": True,
        }
        handler, entry = self._handler(
            hass,
            {
                "control_mode": "shadow",
                "control_owner_program": {"AA:BB": declared},
            },
        )
        self._with_heater(entry, {"reservation_use": 2, "reservation": []})
        store = ControlStore(hass, entry.entry_id)
        await store.async_load()
        await store.async_set_took_over("AA:BB", True)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )
        await handler.async_step_external_control(
            self._control_input(control_mode="live")
        )

        result = await handler.async_step_going_live({})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["control_owner_program"] == {"AA:BB": declared}

    @pytest.mark.asyncio
    async def test_grants_need_live_segments(
        self, hass: HomeAssistant, monkeypatch
    ):
        monkeypatch.setattr(
            "custom_components.nwp500.config_flow.CONTROL_LIVE_AVAILABLE", True
        )
        handler, _ = self._handler(hass)
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )
        result = await handler.async_step_external_control(
            self._control_input(
                control_mode="live",
                control_live_segments=False,
                control_live_grants=True,
            )
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {
            "control_live_grants": "grants_need_segments"
        }

    @pytest.mark.asyncio
    async def test_saving_drops_first_draft_options(self, hass: HomeAssistant):
        handler, _ = self._handler(
            hass,
            {
                "control_hold_off_supported": True,
                "control_daily_revert_time": "03:00",
                "control_tou_off_for_mode": False,
                "control_baseline": {"mode": "energy_saver"},
            },
        )
        await handler.async_step_init(
            {"scan_interval": 30, "control_enabled": True}
        )

        result = await handler.async_step_external_control(
            self._control_input()
        )

        for key in (
            "control_hold_off_supported",
            "control_daily_revert_time",
            "control_tou_off_for_mode",
            "control_baseline",
        ):
            assert key not in result["data"]
