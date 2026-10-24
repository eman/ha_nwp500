"""Config flow for Navien NWP500 integration."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Home Assistant 2026.9 and later validate with probatio, and import
    # `voluptuous` as an alias of it, so at runtime this is probatio there;
    # earlier versions still run on voluptuous. Typed as probatio, as Home
    # Assistant now types schemas.
    import probatio as vol
else:
    import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CONF_CONTROL_ENABLED,
    CONF_CONTROL_INTENT_ENTITY,
    CONF_CONTROL_MODE,
    CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
    CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    CONF_SCAN_INTERVAL,
    CONF_SIMULATOR,
    CONF_SIMULATOR_AIR_ENTITY,
    CONF_SIMULATOR_BATH_GAL,
    CONF_SIMULATOR_CIRCULATION_ENTITY,
    CONF_SIMULATOR_DEMAND_ENTITY,
    CONF_SIMULATOR_DRAW_FLOW_ENTITY,
    CONF_SIMULATOR_ENABLED,
    CONF_SIMULATOR_HUMIDITY_ENTITY,
    CONF_SIMULATOR_INLET_ENTITY,
    CONF_SIMULATOR_SHOWER_GAL,
    CONF_SIMULATOR_WEATHER_ENTITY,
    CONTROL_LIVE_AVAILABLE,
    CONTROL_MODE_LIVE,
    CONTROL_MODE_SHADOW,
    CONTROL_MODES_SELECTABLE,
    CONTROL_OBSOLETE_OPTIONS,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CONTROL_RESERVATION_ENTRY_LIMIT,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    control_follows_plan,
)
from .simulator_deps import (
    REASON_INSTALL_FAILED,
    REASON_MISSING,
    SIMULATOR_REQUIREMENTS,
    DepsResult,
    async_ensure,
    async_missing,
    package_names,
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


# The user guide for the simulator, linked from its forms.
SIMULATOR_GUIDE_URL = (
    "https://github.com/eman/ha_nwp500/blob/main/docs/simulator.md"
)


#: Litres in a US gallon: the sizes are stored in gallons.
_L_PER_GAL = 3.785411784
#: The entities a heater's options may name.
_SIMULATOR_ENTITIES = (
    CONF_SIMULATOR_AIR_ENTITY,
    CONF_SIMULATOR_HUMIDITY_ENTITY,
    CONF_SIMULATOR_WEATHER_ENTITY,
    CONF_SIMULATOR_INLET_ENTITY,
    CONF_SIMULATOR_DEMAND_ENTITY,
    CONF_SIMULATOR_CIRCULATION_ENTITY,
    CONF_SIMULATOR_DRAW_FLOW_ENTITY,
)
_SIZES = (CONF_SIMULATOR_BATH_GAL, CONF_SIMULATOR_SHOWER_GAL)


def _size_selector(*, metric: bool) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=1000 if metric else 260,
            step=0.1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="L" if metric else "gal",
        )
    )


def _simulator_device_schema(*, metric: bool = False) -> vol.Schema:
    """The simulator's form for one heater, every field optional.

    The sensors beside it, a weather forecast, the household's hot water
    use, and a bath's and a shower's size.
    """
    return vol.Schema(
        {
            vol.Optional(CONF_SIMULATOR_AIR_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class="temperature"
                )
            ),
            vol.Optional(
                CONF_SIMULATOR_HUMIDITY_ENTITY
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class="humidity"
                )
            ),
            vol.Optional(
                CONF_SIMULATOR_WEATHER_ENTITY
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Optional(CONF_SIMULATOR_INLET_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class="temperature"
                )
            ),
            vol.Optional(CONF_SIMULATOR_DEMAND_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Optional(
                CONF_SIMULATOR_CIRCULATION_ENTITY
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SIMULATOR_DRAW_FLOW_ENTITY
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class="volume_flow_rate"
                )
            ),
            vol.Optional(CONF_SIMULATOR_BATH_GAL): _size_selector(
                metric=metric
            ),
            vol.Optional(CONF_SIMULATOR_SHOWER_GAL): _size_selector(
                metric=metric
            ),
        }
    )


def _simulator_device_options(
    user_input: dict[str, Any], *, metric: bool = False
) -> dict[str, Any]:
    """A heater's stored options: the sensors chosen, nothing for none.

    The sizes in gallons, from the form's unit.
    """
    options = {
        key: user_input[key]
        for key in _SIMULATOR_ENTITIES
        if user_input.get(key)
    }
    for key in _SIZES:
        if user_input.get(key):
            size = float(user_input[key])
            options[key] = round(size / _L_PER_GAL if metric else size, 2)
    return options


class _SimulatorSteps:
    """The simulator's steps, the same in the setup and options flows.

    Its packages are checked first, and installed behind a progress
    step when they are missing; then each heater gets a form for the
    sensors beside it. The flow says what it does with the answers
    (`_async_simulator_done`) and when the packages cannot be had
    (`_async_simulator_without`).
    """

    hass: Any
    # Set by the flow before the first step: (MAC, name) per heater.
    _simulator_devices: list[tuple[str, str]]
    # Each heater's options as they stand; the forms start from them.
    _simulator_existing: dict[str, dict[str, Any]]

    def _simulator_init(self) -> None:
        self._simulator_devices = []
        self._simulator_existing = {}
        self._simulator_answers: dict[str, dict[str, Any]] = {}
        self._simulator_index = 0
        self._deps: DepsResult | None = None
        self._install_task: asyncio.Task[DepsResult] | None = None

    async def _async_simulator_done(
        self, devices: dict[str, dict[str, Any]]
    ) -> config_entries.ConfigFlowResult:
        raise NotImplementedError

    def _simulator_followed(self) -> bool:
        """Whether another page follows the simulator's."""
        return False

    async def async_step_simulator_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Check the simulator's packages, then ask about each heater."""
        if self._deps is None:
            if await async_missing(self.hass):
                return await self.async_step_simulator_install()
            self._deps = DepsResult(ok=True)
        if not self._deps.ok:
            if self._deps.reason == REASON_MISSING:
                return await self.async_step_simulator_packages_missing()
            return await self.async_step_simulator_install_failed()
        self._simulator_index = 0
        self._simulator_answers = {}
        return await self.async_step_simulator_device()

    async def async_step_simulator_install(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Install the simulator's packages, showing progress."""
        task = self._install_task
        if task is None:
            task = self._install_task = self.hass.async_create_task(
                async_ensure(self.hass)
            )
        if not task.done():
            return self.async_show_progress(  # type: ignore[attr-defined,no-any-return]
                step_id="simulator_install",
                progress_action="simulator_install",
                progress_task=task,
            )
        try:
            self._deps = task.result()
        except Exception:  # noqa: BLE001 - Config flow must not crash UI
            _LOGGER.exception("Installing the simulator's packages failed")
            self._deps = DepsResult(
                ok=False,
                reason=REASON_INSTALL_FAILED,
                missing=SIMULATOR_REQUIREMENTS,
            )
        self._install_task = None
        return self.async_show_progress_done(  # type: ignore[attr-defined,no-any-return]
            next_step_id="simulator_setup"
        )

    def _deps_failed_menu(
        self, step_id: str
    ) -> config_entries.ConfigFlowResult:
        deps = self._deps or DepsResult(ok=False)
        return self.async_show_menu(  # type: ignore[attr-defined,no-any-return]
            step_id=step_id,
            menu_options=["simulator_retry", "simulator_without"],
            description_placeholders={
                "packages": package_names(deps.missing),
                "guide_url": SIMULATOR_GUIDE_URL,
            },
        )

    async def async_step_simulator_packages_missing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Missing, and Home Assistant installs nothing: say so."""
        return self._deps_failed_menu("simulator_packages_missing")

    async def async_step_simulator_install_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The install failed: try again, or go on without."""
        return self._deps_failed_menu("simulator_install_failed")

    async def async_step_simulator_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Check the packages again."""
        self._deps = None
        return await self.async_step_simulator_setup()

    async def async_step_simulator_without(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Go on without the simulator."""
        raise NotImplementedError

    def _simulator_metric(self) -> bool:
        """Whether Home Assistant measures volume in litres."""
        return bool(self.hass.config.units.volume_unit != UnitOfVolume.GALLONS)

    def _simulator_shown(self, mac_address: str) -> dict[str, Any]:
        """A heater's options as its form shows them: sizes in the unit."""
        shown = dict(self._simulator_existing.get(mac_address, {}))
        if self._simulator_metric():
            for key in _SIZES:
                if shown.get(key):
                    shown[key] = round(float(shown[key]) * _L_PER_GAL, 1)
        return shown

    async def async_step_simulator_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The sensors beside one heater, each optional."""
        mac_address, name = self._simulator_devices[self._simulator_index]
        metric = self._simulator_metric()
        if user_input is not None:
            self._simulator_answers[mac_address] = _simulator_device_options(
                user_input, metric=metric
            )
            self._simulator_index += 1
            if self._simulator_index < len(self._simulator_devices):
                return await self.async_step_simulator_device()
            return await self._async_simulator_done(self._simulator_answers)
        return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
            step_id="simulator_device",
            data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                _simulator_device_schema(metric=metric),
                self._simulator_shown(mac_address),
            ),
            description_placeholders={
                "device": name,
                "guide_url": SIMULATOR_GUIDE_URL,
            },
            last_step=self._simulator_index + 1 >= len(self._simulator_devices)
            and not self._simulator_followed(),
        )


class ConfigFlow(_SimulatorSteps, config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg,unused-ignore]
    """Handle a config flow for NWP500."""

    VERSION = 1
    # Bumped to 2 in 0.19.0: entries created before then may still hold
    # energy sensors whose backing library fields were removed in
    # nwp500-python 9.3.0. See async_migrate_entry.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None
        # The sign-in, held while the simulator is offered.
        self._user_input: dict[str, Any] = {}
        self._title = ""
        self._simulator_init()

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
                self._user_input = user_input
                self._title = info["title"]
                self._simulator_devices = info["devices"]
                return await self.async_step_simulator_menu()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_simulator_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Offer the hot water simulator, which can be skipped."""
        return self.async_show_menu(
            step_id="simulator_menu",
            menu_options=["simulator_setup", "simulator_skip"],
            description_placeholders={"guide_url": SIMULATOR_GUIDE_URL},
        )

    async def async_step_simulator_skip(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Without the simulator: the entry as before it existed."""
        return self.async_create_entry(title=self._title, data=self._user_input)

    async def async_step_simulator_without(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The packages could not be had: set up without the simulator."""
        return await self.async_step_simulator_skip()

    async def _async_simulator_done(
        self, devices: dict[str, dict[str, Any]]
    ) -> config_entries.ConfigFlowResult:
        return self.async_create_entry(
            title=self._title,
            data=self._user_input,
            options={CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: devices},
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


# The user guide, linked from the form: translations may not hold URLs.
CONTROL_GUIDE_URL = (
    "https://github.com/eman/ha_nwp500/blob/main/docs/external-control.md"
)


def _form_mode(options: dict[str, Any]) -> str:
    """The Mode choice that shows these stored options.

    A `live` an earlier version stored with its segments switch off writes
    nothing and behaves as shadow, so it shows as Preview.
    """
    mode = options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
    if mode == CONTROL_MODE_LIVE and not control_follows_plan(options):
        return CONTROL_MODE_SHADOW
    return str(mode)


def _control_schema() -> vol.Schema:
    """The external control options form."""
    modes: list[str] = list(CONTROL_MODES_SELECTABLE)
    if CONTROL_LIVE_AVAILABLE:
        # Live writes the heater's reservation list; it is offered only
        # while the gate in the constants is on (issue #158, step 5).
        modes.insert(1, CONTROL_MODE_LIVE)
    return vol.Schema(
        {
            vol.Required(CONF_CONTROL_INTENT_ENTITY): selector.EntitySelector(),
            vol.Required(
                CONF_CONTROL_MODE, default=DEFAULT_CONTROL_MODE
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=modes,
                    translation_key="control_mode",
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


def _control_suggested_values(options: dict[str, Any]) -> dict[str, Any]:
    """What the form shows for an entry that already has the options."""
    suggested = {
        key: value
        for key, value in options.items()
        if key.startswith("control_")
    }
    for key in CONTROL_OBSOLETE_OPTIONS:
        suggested.pop(key, None)
    suggested[CONF_CONTROL_MODE] = _form_mode(options)
    return suggested


def _validate_control_input(user_input: dict[str, Any]) -> dict[str, str]:
    """Cross-field checks the schema cannot express."""
    errors: dict[str, str] = {}
    if (
        user_input[CONF_CONTROL_RESERVATION_ENTRY_RESERVE]
        >= (user_input[CONF_CONTROL_RESERVATION_ENTRY_LIMIT])
    ):
        errors[CONF_CONTROL_RESERVATION_ENTRY_RESERVE] = "reserve_exceeds_limit"
    return errors


def _normalise_control_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn form values into stored options."""
    stored: dict[str, Any] = dict(user_input)
    for key in (
        CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
        CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    ):
        stored[key] = int(stored[key])
    return stored


def _entry_devices(entry: config_entries.ConfigEntry) -> list[tuple[str, str]]:
    """(MAC, name) for each heater of a loaded entry."""
    coordinator = getattr(entry, "runtime_data", None)
    data = getattr(coordinator, "data", None) or {}
    devices: list[tuple[str, str]] = []
    for mac_address, device_data in data.items():
        device = (
            device_data.get("device") if isinstance(device_data, dict) else None
        )
        name = getattr(
            getattr(device, "device_info", None), "device_name", None
        )
        devices.append((mac_address, name or mac_address))
    return devices


class OptionsFlowHandler(_SimulatorSteps, config_entries.OptionsFlow):
    """Handle options flow for NWP500 integration.

    The first page's answers, and each feature's after it, are held in
    `_pending` while the next page is shown; the entry's options are
    written once, at the end.
    """

    def __init__(self) -> None:
        """Hold the answers while the next page is shown."""
        self._pending: dict[str, Any] = {}
        self._simulator_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            options = self.config_entry.options
            self._pending = dict(user_input)
            for key in (CONF_CONTROL_ENABLED, CONF_SIMULATOR_ENABLED):
                if key not in options and not user_input.get(key):
                    # Never switched on: store what the release before the
                    # feature stored (spec section 1.1.4).
                    self._pending.pop(key, None)
            if self._pending.get(CONF_SIMULATOR_ENABLED):
                already_on = options.get(CONF_SIMULATOR_ENABLED) is True
                self._simulator_devices = _entry_devices(self.config_entry)
                self._simulator_existing = dict(
                    options.get(CONF_SIMULATOR) or {}
                )
                if not self._simulator_devices and not already_on:
                    # Its heaters are known only once the integration is
                    # loaded; switched on now, the simulator would start
                    # without the sensors beside them, and would install
                    # its packages with nothing on screen.
                    return self._init_form(
                        errors={CONF_SIMULATOR_ENABLED: "simulator_not_loaded"}
                    )
                if already_on:
                    # Already running, or reported in Repairs: changing
                    # another option does not check its packages again.
                    self._deps = DepsResult(ok=True)
                if self._simulator_devices:
                    return await self.async_step_simulator_setup()
            return await self._async_after_simulator()

        return self._init_form()

    def _simulator_followed(self) -> bool:
        return bool(self._pending.get(CONF_CONTROL_ENABLED))

    def _init_form(
        self, errors: dict[str, str] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_form(
            errors=errors,
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
                    vol.Optional(
                        CONF_SIMULATOR_ENABLED,
                        default=self.config_entry.options.get(
                            CONF_SIMULATOR_ENABLED, False
                        ),
                    ): selector.BooleanSelector(),
                }
            ),
        )

    async def _async_simulator_done(
        self, devices: dict[str, dict[str, Any]]
    ) -> config_entries.ConfigFlowResult:
        self._pending[CONF_SIMULATOR] = devices
        return await self._async_after_simulator()

    async def async_step_simulator_without(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The packages could not be had: leave the simulator off."""
        if CONF_SIMULATOR_ENABLED in self.config_entry.options:
            self._pending[CONF_SIMULATOR_ENABLED] = False
        else:
            self._pending.pop(CONF_SIMULATOR_ENABLED, None)
        return await self._async_after_simulator()

    async def _async_after_simulator(
        self,
    ) -> config_entries.ConfigFlowResult:
        if self._pending.get(CONF_CONTROL_ENABLED):
            return await self.async_step_external_control()
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **self._pending}
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
                for key in CONTROL_OBSOLETE_OPTIONS:
                    options.pop(key, None)
                data = {
                    **options,
                    **self._pending,
                    **_normalise_control_input(user_input),
                }
                return self.async_create_entry(title="", data=data)

        suggested = _control_suggested_values(options)
        if user_input is not None:
            suggested.update(user_input)
        return self.async_show_form(
            step_id="external_control",
            data_schema=self.add_suggested_values_to_schema(
                _control_schema(), suggested
            ),
            errors=errors,
            description_placeholders={"guide_url": CONTROL_GUIDE_URL},
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
            'uv pip install "nwp500-python==9.12.1" "awsiotsdk==1.31.0"'
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
            found = [
                (
                    d.device_info.mac_address,
                    d.device_info.device_name or d.device_info.mac_address,
                )
                for d in devices
            ]

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

    return {"title": f"Navien {device_name}", "devices": found}


class CannotConnect(HomeAssistantError):  # noqa: N818
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):  # noqa: N818
    """Error to indicate there is invalid auth."""
