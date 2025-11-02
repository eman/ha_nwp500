"""Config flow for Navien NWP500 integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.data_entry_flow import AbortFlow

from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Import at module level to avoid blocking calls in event loop
try:
    from nwp500 import (  # type: ignore[attr-defined]
        NavienAuthClient,
        NavienAPIClient,
    )

    nwp500_available = True
except ImportError:
    nwp500_available = False

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NWP500."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001 - Config flow must not crash UI
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=info["title"], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauth when credentials expire or become invalid."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm reauth and update credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001 - Config flow must not crash UI
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"
            else:
                if self._reauth_entry:
                    # Update existing entry with new credentials
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        data=user_input,
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")
                
                # This should not happen, but handle gracefully
                return self.async_abort(reason="reauth_failed")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "account": self._reauth_entry.data[CONF_EMAIL]
                if self._reauth_entry
                else "unknown"
            },
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for NWP500 integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            ),
        )


async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate the user input allows us to connect.
    
    Raises:
        CannotConnect: If connection to Navien service fails
        InvalidAuth: If credentials are invalid (401/unauthorized)
    """
    if not nwp500_available:
        _LOGGER.error(
            "nwp500-python library not installed. Please install with: "
            "pip install nwp500-python==6.0.0 awsiotsdk>=1.25.0"
        )
        raise CannotConnect("nwp500-python library not available")

    email = data[CONF_EMAIL]
    password = data[CONF_PASSWORD]

    try:
        async with NavienAuthClient(email, password) as auth_client:
            api_client = NavienAPIClient(auth_client=auth_client)

            # Try to get devices to validate credentials
            devices = await api_client.list_devices()

            if not devices:
                _LOGGER.error(
                    "No devices found for account %s. "
                    "Authentication succeeded but device list is empty. "
                    "Please verify device is registered in NaviLink app.",
                    email,
                )
                raise CannotConnect(
                    "No devices found for this account. "
                    "Please check the NaviLink app to verify your device "
                    "is registered and online."
                )

            # Get first device for title
            device = devices[0]
            device_name = device.device_info.device_name or "NWP500"

    except (CannotConnect, InvalidAuth):
        # Re-raise our own exceptions
        raise
    except (RuntimeError, OSError, TimeoutError, AttributeError, KeyError) as err:
        # Network, connection, and data access errors
        _LOGGER.error("Failed to authenticate with Navien: %s", err)
        if "401" in str(err) or "unauthorized" in str(err).lower():
            raise InvalidAuth from err
        raise CannotConnect from err

    return {"title": f"Navien {device_name}"}


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
