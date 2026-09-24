"""Config flow for Navien NWP500 integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_CONTROL_ALLOWED_MODES,
    CONF_CONTROL_ASSISTED_MODE,
    CONF_CONTROL_ENABLED,
    CONF_CONTROL_INTENT_ENTITY,
    CONF_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
    CONF_CONTROL_MODE,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    CONF_CONTROL_SETPOINT_MAX_F,
    CONF_CONTROL_SETPOINT_MIN_F,
    CONF_CONTROL_SURPLUS_ENTITY,
    CONF_CONTROL_SURPLUS_THRESHOLD_KW,
    CONF_SCAN_INTERVAL,
    CONTROL_MODE_NAMES,
    CONTROL_MODES_SELECTABLE,
    CONTROL_OBSOLETE_OPTIONS,
    DEFAULT_CONTROL_ALLOWED_MODES,
    DEFAULT_CONTROL_ASSISTED_MODE,
    DEFAULT_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
    DEFAULT_CONTROL_SURPLUS_THRESHOLD_KW,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CONTROL_RESERVATION_ENTRY_LIMIT,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Authentication failures validate_input maps to distinct UI errors. Declared
# before the import so both branches below assign rather than redefine.
_credential_errors: tuple[type[Exception], ...]
_auth_errors: tuple[type[Exception], ...]

# Import at module level to avoid blocking calls in event loop
try:
    from nwp500 import (  # type: ignore[attr-defined]
        NavienAPIClient,
        NavienAuthClient,
    )
    from nwp500.exceptions import (
        AuthenticationError,
        InvalidCredentialsError,
    )

    nwp500_available = True

    # Held as tuples so the handlers in validate_input stay resolvable when
    # the library is missing: an empty tuple is a legal except clause that
    # matches nothing. Nothing can raise these in that state anyway -- the
    # nwp500_available guard returns first.
    _credential_errors = (InvalidCredentialsError,)
    _auth_errors = (AuthenticationError,)
except ImportError:
    nwp500_available = False
    _credential_errors = ()
    _auth_errors = ()

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg,unused-ignore]
    """Handle a config flow for NWP500."""

    VERSION = 1
    # Bumped to 2 in 0.19.0: entries created before then may still hold
    # energy sensors whose backing library fields were removed in
    # nwp500-python 9.3.0. See async_migrate_entry.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

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
                email = user_input[CONF_EMAIL].lower()
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_configured()
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
                    # Reauth must stay on the account the entry was created
                    # for. Every device, entity and MQTT subscription is keyed
                    # to MACs from that account, so accepting a different
                    # login here would silently rebind the entry to devices
                    # its registry entries do not describe.
                    await self.async_set_unique_id(
                        user_input[CONF_EMAIL].lower()
                    )
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data=user_input,
                        reason="reauth_successful",
                    )
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration — allows updating email/password in-place."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001 - Config flow must not crash UI
                _LOGGER.exception("Unexpected exception during reconfigure")
                errors["base"] = "unknown"
            else:
                email = user_input[CONF_EMAIL].lower()
                await self.async_set_unique_id(email)
                # Not _abort_if_unique_id_configured: in a reconfigure flow
                # the entry that matches this unique ID *is* the entry being
                # reconfigured, so that helper aborted with
                # "already_configured" before the new credentials were ever
                # written -- silently discarding the password change this
                # flow exists to make.
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    title=info["title"],
                    data=user_input,
                    reason="reconfigure_successful",
                )

        current_data = self._get_reconfigure_entry().data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL, default=current_data.get(CONF_EMAIL, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )


# The setpoint bounds are stored in degF whatever Home Assistant displays,
# so a later change of unit system cannot misread them. The form shows and
# takes them in the configured unit.
_SETPOINT_FORM_KEYS: dict[str, str] = {
    "control_setpoint_min": CONF_CONTROL_SETPOINT_MIN_F,
    "control_setpoint_max": CONF_CONTROL_SETPOINT_MAX_F,
}

# Options the form may leave empty. An empty field clears the stored value
# rather than keeping it, so "follow the device" can be chosen again.
_OPTIONAL_CONTROL_KEYS = (
    CONF_CONTROL_SURPLUS_ENTITY,
    CONF_CONTROL_SETPOINT_MIN_F,
    CONF_CONTROL_SETPOINT_MAX_F,
)


def _control_schema(hass: HomeAssistant) -> vol.Schema:
    """The external control options form."""
    celsius = hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS
    unit = (
        UnitOfTemperature.CELSIUS if celsius else UnitOfTemperature.FAHRENHEIT
    )
    setpoint_selector = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=20 if celsius else 70,
            max=70 if celsius else 160,
            step=0.1,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    mode_name_options = list(CONTROL_MODE_NAMES)
    return vol.Schema(
        {
            vol.Required(CONF_CONTROL_INTENT_ENTITY): selector.EntitySelector(),
            vol.Required(
                CONF_CONTROL_MODE, default=DEFAULT_CONTROL_MODE
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(CONTROL_MODES_SELECTABLE),
                    translation_key="control_mode",
                )
            ),
            vol.Optional(CONF_CONTROL_SURPLUS_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["binary_sensor", "sensor"]
                )
            ),
            vol.Required(
                CONF_CONTROL_SURPLUS_THRESHOLD_KW,
                default=DEFAULT_CONTROL_SURPLUS_THRESHOLD_KW,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=50,
                    step=0.01,
                    unit_of_measurement="kW",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional("control_setpoint_min"): setpoint_selector,
            vol.Optional("control_setpoint_max"): setpoint_selector,
            vol.Required(
                CONF_CONTROL_ALLOWED_MODES,
                default=list(DEFAULT_CONTROL_ALLOWED_MODES),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=mode_name_options,
                    multiple=True,
                    translation_key="control_mode_name",
                )
            ),
            vol.Required(
                CONF_CONTROL_ASSISTED_MODE,
                default=DEFAULT_CONTROL_ASSISTED_MODE,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=mode_name_options,
                    translation_key="control_mode_name",
                )
            ),
            vol.Required(
                CONF_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
                default=DEFAULT_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=600,
                    step=1,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
                default=DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=MAX_CONTROL_RESERVATION_ENTRY_LIMIT,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
                default=DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=MAX_CONTROL_RESERVATION_ENTRY_LIMIT,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _to_display_unit(hass: HomeAssistant, fahrenheit: Any) -> float | None:
    if fahrenheit is None:
        return None
    if hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS:
        return round(
            TemperatureConverter.convert(
                float(fahrenheit),
                UnitOfTemperature.FAHRENHEIT,
                UnitOfTemperature.CELSIUS,
            ),
            1,
        )
    return float(fahrenheit)


def _to_fahrenheit(hass: HomeAssistant, value: Any) -> float | None:
    if value is None:
        return None
    if hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS:
        return round(
            TemperatureConverter.convert(
                float(value),
                UnitOfTemperature.CELSIUS,
                UnitOfTemperature.FAHRENHEIT,
            ),
            1,
        )
    return float(value)


def _control_suggested_values(
    hass: HomeAssistant, options: dict[str, Any]
) -> dict[str, Any]:
    """What the form shows for an entry that already has the options."""
    suggested = {
        key: value
        for key, value in options.items()
        if key.startswith("control_")
        and key
        not in (CONF_CONTROL_SETPOINT_MIN_F, CONF_CONTROL_SETPOINT_MAX_F)
    }
    for form_key, option_key in _SETPOINT_FORM_KEYS.items():
        if (
            value := _to_display_unit(hass, options.get(option_key))
        ) is not None:
            suggested[form_key] = value
    for key in CONTROL_OBSOLETE_OPTIONS:
        suggested.pop(key, None)
    return suggested


def _validate_control_input(user_input: dict[str, Any]) -> dict[str, str]:
    """Cross-field checks the schema cannot express."""
    errors: dict[str, str] = {}
    minimum = user_input.get("control_setpoint_min")
    maximum = user_input.get("control_setpoint_max")
    if minimum is not None and maximum is not None and minimum >= maximum:
        errors["control_setpoint_min"] = "setpoint_range"
    if (
        user_input[CONF_CONTROL_RESERVATION_ENTRY_RESERVE]
        >= (user_input[CONF_CONTROL_RESERVATION_ENTRY_LIMIT])
    ):
        errors[CONF_CONTROL_RESERVATION_ENTRY_RESERVE] = "reserve_exceeds_limit"
    allowed = user_input.get(CONF_CONTROL_ALLOWED_MODES) or []
    if not allowed:
        errors[CONF_CONTROL_ALLOWED_MODES] = "allowed_modes_empty"
    elif user_input[CONF_CONTROL_ASSISTED_MODE] not in allowed:
        errors[CONF_CONTROL_ASSISTED_MODE] = "assisted_mode_not_allowed"
    return errors


def _normalise_control_input(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Turn form values into stored options."""
    stored: dict[str, Any] = {
        key: value
        for key, value in user_input.items()
        if key not in _SETPOINT_FORM_KEYS
    }
    for form_key, option_key in _SETPOINT_FORM_KEYS.items():
        value = _to_fahrenheit(hass, user_input.get(form_key))
        if value is not None:
            stored[option_key] = value
    for key in (
        CONF_CONTROL_MIN_RUN_BEFORE_LOWER_MIN,
        CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
        CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    ):
        stored[key] = int(stored[key])
    return stored


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for NWP500 integration."""

    def __init__(self) -> None:
        """Hold the first page's answers while the second is shown."""
        self._init_input: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            self._init_input = user_input
            if user_input.get(CONF_CONTROL_ENABLED):
                return await self.async_step_external_control()
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )

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
                    vol.Optional(
                        CONF_CONTROL_ENABLED,
                        default=self.config_entry.options.get(
                            CONF_CONTROL_ENABLED, False
                        ),
                    ): selector.BooleanSelector(),
                }
            ),
        )

    async def async_step_external_control(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure the external control feature (issue #158)."""
        errors: dict[str, str] = {}
        options = dict(self.config_entry.options)

        if user_input is not None:
            errors = _validate_control_input(user_input)
            if not errors:
                for key in (*_OPTIONAL_CONTROL_KEYS, *CONTROL_OBSOLETE_OPTIONS):
                    options.pop(key, None)
                data = {
                    **options,
                    **self._init_input,
                    **_normalise_control_input(self.hass, user_input),
                }
                return self.async_create_entry(title="", data=data)

        suggested = _control_suggested_values(self.hass, options)
        if user_input is not None:
            suggested.update(user_input)
        return self.async_show_form(
            step_id="external_control",
            data_schema=self.add_suggested_values_to_schema(
                _control_schema(self.hass), suggested
            ),
            errors=errors,
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
            'uv pip install "nwp500-python==9.4.2" "awsiotsdk==1.31.0"'
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

    except CannotConnect, InvalidAuth:
        # Re-raise our own exceptions
        raise
    except _credential_errors as err:
        _LOGGER.error("Invalid Navien credentials for %s: %s", email, err)
        raise InvalidAuth from err
    except _auth_errors as err:
        # Reported as a connection failure, not a bad password. Only
        # InvalidCredentialsError above carries the invalid-login contract:
        # nwp500-python raises it for a 401 or an "invalid"/"unauthorized"
        # message, and turns every *other* non-200 from that same response
        # into a bare AuthenticationError -- which also covers unparseable
        # responses and internal state errors, and defaults to
        # retriable=False. Sending those to invalid_auth would tell a user
        # to change a password that is fine because Navien returned a 500.
        _LOGGER.error(
            "Navien authentication service failed (retriable=%s): %s",
            getattr(err, "retriable", False),
            err,
        )
        raise CannotConnect from err
    except Exception as err:  # noqa: BLE001
        # Network, connection, and data access errors
        _LOGGER.error("Failed to authenticate with Navien: %s", err)
        raise CannotConnect from err

    return {"title": f"Navien {device_name}"}


class CannotConnect(HomeAssistantError):  # noqa: N818
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):  # noqa: N818
    """Error to indicate there is invalid auth."""
