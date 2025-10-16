"""Config flow for Navien NWP500 integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Import at module level to avoid blocking calls in event loop
try:
    from nwp500 import NavienAuthClient, NavienAPIClient
    NWP500_AVAILABLE = True
except ImportError:
    NWP500_AVAILABLE = False

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NWP500."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


async def validate_input(hass, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    if not NWP500_AVAILABLE:
        _LOGGER.error(
            "nwp500-python library not installed. Please install with: "
            "pip install nwp500-python==1.2.0 awsiotsdk>=1.20.0"
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
                raise InvalidAuth("No devices found for this account")
                
            # Get first device for title
            device = devices[0]
            device_name = device.device_info.device_name or "NWP500"
            
    except Exception as err:
        _LOGGER.error("Failed to authenticate with Navien: %s", err)
        if "401" in str(err) or "unauthorized" in str(err).lower():
            raise InvalidAuth from err
        raise CannotConnect from err
    
    return {"title": f"Navien {device_name}"}


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""