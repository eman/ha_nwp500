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
from homeassistant.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
    CONF_SIMULATOR_DRAW_FLOW_VOLUME,
    CONF_SIMULATOR_ENABLED,
    CONF_SIMULATOR_HUMIDITY_ENTITY,
    CONF_SIMULATOR_INLET_ENTITY,
    CONF_SIMULATOR_MIXING_VALVE_F,
    CONF_SIMULATOR_REFILL_GAL,
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
    DRAW_FLOW_DELIVERED,
    DRAW_FLOW_TANK,
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
_SIZES = (
    CONF_SIMULATOR_BATH_GAL,
    CONF_SIMULATOR_REFILL_GAL,
    CONF_SIMULATOR_SHOWER_GAL,
)


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


def _valve_selector(*, celsius: bool) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=35 if celsius else 95,
            max=70 if celsius else 160,
            step=0.5 if celsius else 1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=(
                UnitOfTemperature.CELSIUS
                if celsius
                else UnitOfTemperature.FAHRENHEIT
            ),
        )
    )


def _to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


#: The sections of a heater's simulator form, and the fields in each.
SECTION_SENSORS = "sensors"
SECTION_HOT_WATER_USE = "hot_water_use"
SECTION_HOUSEHOLD = "household"
_SECTIONS: dict[str, tuple[str, ...]] = {
    SECTION_SENSORS: (
        CONF_SIMULATOR_AIR_ENTITY,
        CONF_SIMULATOR_HUMIDITY_ENTITY,
        CONF_SIMULATOR_WEATHER_ENTITY,
    ),
    SECTION_HOT_WATER_USE: (
        CONF_SIMULATOR_DEMAND_ENTITY,
        CONF_SIMULATOR_DRAW_FLOW_ENTITY,
        CONF_SIMULATOR_DRAW_FLOW_VOLUME,
        CONF_SIMULATOR_INLET_ENTITY,
        CONF_SIMULATOR_CIRCULATION_ENTITY,
    ),
    SECTION_HOUSEHOLD: (
        CONF_SIMULATOR_BATH_GAL,
        CONF_SIMULATOR_REFILL_GAL,
        CONF_SIMULATOR_SHOWER_GAL,
        CONF_SIMULATOR_MIXING_VALVE_F,
    ),
}


def _simulator_device_schema(
    *,
    metric: bool = False,
    celsius: bool = False,
    open_sections: frozenset[str] = frozenset(),
) -> vol.Schema:
    """The simulator's form for one heater, every field optional.

    The sensors beside it and a weather forecast; the household's hot
    water use and what its flow counts, folded away; a bath's, a refill's
    and a shower's size and the mixing valve's setting, folded away.
    Any entity may be chosen, the heater's own among them (its DHW in use
    and its inlet temperature can serve). A folded section in
    `open_sections` is shown open: one holding a value, so a field an
    error names is in view.
    """

    def entities(
        domain: str, device_class: str | None = None
    ) -> selector.EntitySelector:
        config = selector.EntitySelectorConfig(domain=domain)
        if device_class is not None:
            config["device_class"] = device_class
        return selector.EntitySelector(config)

    return vol.Schema(
        {
            vol.Optional(SECTION_SENSORS): section(
                vol.Schema(
                    {
                        vol.Optional(CONF_SIMULATOR_AIR_ENTITY): entities(
                            "sensor", "temperature"
                        ),
                        vol.Optional(CONF_SIMULATOR_HUMIDITY_ENTITY): entities(
                            "sensor", "humidity"
                        ),
                        vol.Optional(CONF_SIMULATOR_WEATHER_ENTITY): entities(
                            "weather"
                        ),
                    }
                ),
                {"collapsed": False},
            ),
            vol.Optional(SECTION_HOT_WATER_USE): section(
                vol.Schema(
                    {
                        vol.Optional(CONF_SIMULATOR_DEMAND_ENTITY): entities(
                            "binary_sensor"
                        ),
                        vol.Optional(CONF_SIMULATOR_DRAW_FLOW_ENTITY): entities(
                            "sensor", "volume_flow_rate"
                        ),
                        # Shown as the default it is; stored as nothing.
                        vol.Optional(
                            CONF_SIMULATOR_DRAW_FLOW_VOLUME,
                            default=DRAW_FLOW_DELIVERED,
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[DRAW_FLOW_DELIVERED, DRAW_FLOW_TANK],
                                translation_key=CONF_SIMULATOR_DRAW_FLOW_VOLUME,
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        ),
                        vol.Optional(CONF_SIMULATOR_INLET_ENTITY): entities(
                            "sensor", "temperature"
                        ),
                        vol.Optional(
                            CONF_SIMULATOR_CIRCULATION_ENTITY
                        ): entities("sensor"),
                    }
                ),
                {"collapsed": SECTION_HOT_WATER_USE not in open_sections},
            ),
            vol.Optional(SECTION_HOUSEHOLD): section(
                vol.Schema(
                    {
                        vol.Optional(CONF_SIMULATOR_BATH_GAL): _size_selector(
                            metric=metric
                        ),
                        vol.Optional(CONF_SIMULATOR_REFILL_GAL): _size_selector(
                            metric=metric
                        ),
                        vol.Optional(CONF_SIMULATOR_SHOWER_GAL): _size_selector(
                            metric=metric
                        ),
                        vol.Optional(
                            CONF_SIMULATOR_MIXING_VALVE_F
                        ): _valve_selector(celsius=celsius),
                    }
                ),
                {"collapsed": SECTION_HOUSEHOLD not in open_sections},
            ),
        }
    )


def _filled_sections(shown: dict[str, Any]) -> frozenset[str]:
    """The folded sections holding a value: stored, or just entered.

    What the draw flow counts at its default is no value of the user's.
    """
    return frozenset(
        name
        for name, values in _nest(shown).items()
        if any(
            value not in (None, "")
            and not (
                key == CONF_SIMULATOR_DRAW_FLOW_VOLUME
                and value == DRAW_FLOW_DELIVERED
            )
            for key, value in values.items()
        )
    )


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """The form's sections as one dict, as the options store them."""
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in _SECTIONS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def _nest(flat: dict[str, Any]) -> dict[str, Any]:
    """Stored options as the form's sections."""
    return {
        name: {key: flat[key] for key in keys if key in flat}
        for name, keys in _SECTIONS.items()
    }


def _simulator_device_options(
    user_input: dict[str, Any], *, metric: bool = False, celsius: bool = False
) -> dict[str, Any]:
    """A heater's stored options: the sensors chosen, nothing for none.

    The sizes in gallons and the valve in °F, from the form's units.
    """
    user_input = _flatten(user_input)
    options = {
        key: user_input[key]
        for key in _SIMULATOR_ENTITIES
        if user_input.get(key)
    }
    for key in _SIZES:
        if user_input.get(key):
            size = float(user_input[key])
            options[key] = round(size / _L_PER_GAL if metric else size, 2)
    # Delivered water is the library's default: stored as nothing.
    if user_input.get(CONF_SIMULATOR_DRAW_FLOW_VOLUME) == DRAW_FLOW_TANK:
        options[CONF_SIMULATOR_DRAW_FLOW_VOLUME] = DRAW_FLOW_TANK
    if user_input.get(CONF_SIMULATOR_MIXING_VALVE_F):
        valve = float(user_input[CONF_SIMULATOR_MIXING_VALVE_F])
        options[CONF_SIMULATOR_MIXING_VALVE_F] = round(
            _to_f(valve) if celsius else valve, 1
        )
    return options


def _simulator_device_errors(options: dict[str, Any]) -> dict[str, str]:
    """Inputs the simulator can't use as chosen."""
    if (
        CONF_SIMULATOR_INLET_ENTITY in options
        and CONF_SIMULATOR_DEMAND_ENTITY not in options
    ):
        # The supply probe is read only over long draws, which the hot
        # water in use sensor marks: alone, it is never read.
        return {"base": "inlet_needs_demand"}
    return {}


def _heater_device(
    hass: HomeAssistant, mac_address: str
) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, mac_address)}
    )


def _own_entities(hass: HomeAssistant, mac_address: str) -> list[str]:
    """The heater's own entities: not the sensors beside it."""
    device = _heater_device(hass, mac_address)
    if device is None:
        return []
    return [
        entity.entity_id
        for entity in er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=True
        )
    ]


def _discovered(hass: HomeAssistant, mac_address: str) -> dict[str, Any]:
    """Sensors a heater's form can suggest: each the only one there is.

    The only weather entity; the only temperature, and the only humidity,
    sensor in the heater's area, not the heater's own.
    """
    found: dict[str, Any] = {}
    weather = hass.states.async_entity_ids("weather")
    if len(weather) == 1:
        found[CONF_SIMULATOR_WEATHER_ENTITY] = weather[0]
    device = _heater_device(hass, mac_address)
    if device is None or device.area_id is None:
        return found
    own = set(_own_entities(hass, mac_address))
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    for key, device_class in (
        (CONF_SIMULATOR_AIR_ENTITY, "temperature"),
        (CONF_SIMULATOR_HUMIDITY_ENTITY, "humidity"),
    ):
        matches = []
        for state in hass.states.async_all("sensor"):
            if (
                state.entity_id in own
                or state.attributes.get("device_class") != device_class
            ):
                continue
            entity = registry.async_get(state.entity_id)
            area = None if entity is None else entity.area_id
            if area is None and entity is not None and entity.device_id:
                other = devices.async_get(entity.device_id)
                area = None if other is None else other.area_id
            if area == device.area_id:
                matches.append(state.entity_id)
        if len(matches) == 1:
            found[key] = matches[0]
    return found


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
        self, step_id: str, options: list[str]
    ) -> config_entries.ConfigFlowResult:
        deps = self._deps or DepsResult(ok=False)
        return self.async_show_menu(  # type: ignore[attr-defined,no-any-return]
            step_id=step_id,
            menu_options=options,
            description_placeholders={
                "packages": package_names(deps.missing),
                "guide_url": SIMULATOR_GUIDE_URL,
            },
        )

    async def async_step_simulator_packages_missing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Missing, and Home Assistant installs nothing: say so.

        Installed by hand meanwhile, they are found on trying again.
        """
        return self._deps_failed_menu(
            "simulator_packages_missing",
            ["simulator_retry", "simulator_without"],
        )

    async def async_step_simulator_install_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The install failed: go on without.

        Home Assistant tries an install again only after it restarts, so
        trying again from here could not succeed.
        """
        return self._deps_failed_menu(
            "simulator_install_failed", ["simulator_without"]
        )

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

    def _simulator_celsius(self) -> bool:
        """Whether Home Assistant measures temperature in °C."""
        return bool(
            self.hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS
        )

    def _simulator_shown(self, mac_address: str) -> dict[str, Any]:
        """A heater's options as its form shows them, in its units.

        A heater asked about for the first time is shown the sensors
        found for it (`_discovered`); one asked before, what it has, so a
        sensor cleared stays cleared.
        """
        if mac_address in self._simulator_existing:
            shown = dict(self._simulator_existing[mac_address])
        else:
            shown = _discovered(self.hass, mac_address)
        if self._simulator_metric():
            for key in _SIZES:
                if shown.get(key):
                    shown[key] = round(float(shown[key]) * _L_PER_GAL, 1)
        valve = shown.get(CONF_SIMULATOR_MIXING_VALVE_F)
        if valve and self._simulator_celsius():
            shown[CONF_SIMULATOR_MIXING_VALVE_F] = round(
                (float(valve) - 32) * 5 / 9, 1
            )
        return shown

    async def async_step_simulator_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The sensors beside one heater, each optional."""
        mac_address, name = self._simulator_devices[self._simulator_index]
        metric = self._simulator_metric()
        celsius = self._simulator_celsius()
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _simulator_device_options(
                user_input, metric=metric, celsius=celsius
            )
            errors = _simulator_device_errors(options)
            if not errors:
                self._simulator_answers[mac_address] = options
                self._simulator_index += 1
                if self._simulator_index < len(self._simulator_devices):
                    return await self.async_step_simulator_device()
                return await self._async_simulator_done(self._simulator_answers)
        shown = (
            self._simulator_shown(mac_address)
            if user_input is None
            else _flatten(user_input)
        )
        return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
            step_id="simulator_device",
            data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                _simulator_device_schema(
                    metric=metric,
                    celsius=celsius,
                    open_sections=_filled_sections(shown),
                ),
                _nest(shown),
            ),
            errors=errors,
            description_placeholders={
                "device": name,
                "guide_url": SIMULATOR_GUIDE_URL,
            },
            last_step=self._simulator_index + 1 >= len(self._simulator_devices)
            and not self._simulator_followed(),
        )


# The user guide, linked from the form: translations may not hold URLs.
CONTROL_GUIDE_URL = (
    "https://github.com/eman/ha_nwp500/blob/main/docs/external-control.md"
)
#: The external control form's folded section: the schedule's sizes.
SECTION_SCHEDULE = "schedule"
#: The attributes that mark a plan entity: a plan document's own keys.
_PLAN_KEYS = frozenset({"protocol", "intent_id", "segments"})


def _form_mode(options: dict[str, Any]) -> str:
    """The Mode choice that shows these stored options.

    A `live` an earlier version stored with its segments switch off writes
    nothing and behaves as shadow, so it shows as Preview.
    """
    mode = options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
    if mode == CONTROL_MODE_LIVE and not control_follows_plan(options):
        return CONTROL_MODE_SHADOW
    return str(mode)


def _plan_entities(hass: HomeAssistant) -> list[str]:
    """Entities whose attributes are a plan: a scheduler's plan entity."""
    return sorted(
        state.entity_id
        for state in hass.states.async_all()
        if _PLAN_KEYS <= state.attributes.keys()
    )


def _control_schema(*, toggle: bool, schedule_open: bool = False) -> vol.Schema:
    """The external control form.

    The plan entity picker offers every entity: a plan entity that is
    unavailable carries no plan to be found by. With `toggle`, the form's first field turns the feature on or off,
    and the plan entity is asked for only when it is on.
    """
    modes: list[str] = list(CONTROL_MODES_SELECTABLE)
    if CONTROL_LIVE_AVAILABLE:
        # Live writes the heater's reservation list; it is offered only
        # while the gate in the constants is on (issue #158, step 5).
        modes.insert(1, CONTROL_MODE_LIVE)
    picker = selector.EntitySelector()
    fields: dict[Any, Any] = {}
    if toggle:
        fields[vol.Required(CONF_CONTROL_ENABLED, default=False)] = (
            selector.BooleanSelector()
        )
        fields[vol.Optional(CONF_CONTROL_INTENT_ENTITY)] = picker
    else:
        fields[vol.Required(CONF_CONTROL_INTENT_ENTITY)] = picker
    fields[vol.Required(CONF_CONTROL_MODE, default=DEFAULT_CONTROL_MODE)] = (
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=modes,
                translation_key="control_mode",
            )
        )
    )
    fields[vol.Optional(SECTION_SCHEDULE)] = section(
        vol.Schema(
            {
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
        ),
        {"collapsed": not schedule_open},
    )
    return vol.Schema(fields)


def _control_suggested_values(
    options: dict[str, Any], plans: list[str]
) -> dict[str, Any]:
    """What the form shows: the options as they stand.

    With no plan entity chosen yet, the plan entity found, when it is
    the only one.
    """
    suggested = {
        key: value
        for key, value in options.items()
        if key.startswith("control_")
    }
    for key in CONTROL_OBSOLETE_OPTIONS:
        suggested.pop(key, None)
    suggested[CONF_CONTROL_MODE] = _form_mode(options)
    if not suggested.get(CONF_CONTROL_INTENT_ENTITY) and len(plans) == 1:
        suggested[CONF_CONTROL_INTENT_ENTITY] = plans[0]
    schedule = {
        key: suggested.pop(key)
        for key in (
            CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
            CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
        )
        if key in suggested
    }
    suggested[SECTION_SCHEDULE] = schedule
    return suggested


def _control_flat(user_input: dict[str, Any]) -> dict[str, Any]:
    """The form's answers with its schedule section unfolded."""
    flat = {k: v for k, v in user_input.items() if k != SECTION_SCHEDULE}
    flat.update(user_input.get(SECTION_SCHEDULE) or {})
    return flat


def _validate_control_input(user_input: dict[str, Any]) -> dict[str, str]:
    """Cross-field checks the schema cannot express."""
    errors: dict[str, str] = {}
    if not user_input.get(CONF_CONTROL_INTENT_ENTITY):
        errors[CONF_CONTROL_INTENT_ENTITY] = "intent_entity_required"
    limit = user_input.get(
        CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
        DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT,
    )
    reserve = user_input.get(
        CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
        DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE,
    )
    if reserve >= limit:
        errors["base"] = "reserve_exceeds_limit"
    return errors


def _normalise_control_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn form values into stored options."""
    stored: dict[str, Any] = {
        CONF_CONTROL_RESERVATION_ENTRY_LIMIT: (
            DEFAULT_CONTROL_RESERVATION_ENTRY_LIMIT
        ),
        CONF_CONTROL_RESERVATION_ENTRY_RESERVE: (
            DEFAULT_CONTROL_RESERVATION_ENTRY_RESERVE
        ),
        **user_input,
        CONF_CONTROL_ENABLED: True,
    }
    for key in (
        CONF_CONTROL_RESERVATION_ENTRY_LIMIT,
        CONF_CONTROL_RESERVATION_ENTRY_RESERVE,
    ):
        stored[key] = int(stored[key])
    return stored


class _ControlSteps:
    """External control's page, the same at setup and in the options."""

    hass: Any

    async def _async_control_done(
        self, options: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        raise NotImplementedError

    def _control_options(self) -> dict[str, Any]:
        """The options the form starts from."""
        return {}

    def _control_toggle(self) -> bool:
        """Whether the form turns the feature on or off."""
        return False

    async def async_step_external_control(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """External control's settings (issue #158)."""
        errors: dict[str, str] = {}
        options = self._control_options()
        toggle = self._control_toggle()
        plans = _plan_entities(self.hass)
        if user_input is not None:
            answers = _control_flat(user_input)
            if toggle and not answers.get(CONF_CONTROL_ENABLED):
                return await self._async_control_done(
                    {CONF_CONTROL_ENABLED: False}
                )
            answers.pop(CONF_CONTROL_ENABLED, None)
            errors = _validate_control_input(answers)
            if not errors:
                return await self._async_control_done(
                    _normalise_control_input(answers)
                )
        suggested = _control_suggested_values(options, plans)
        if user_input is not None:
            suggested.update(user_input)
        return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
            step_id="external_control",
            data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                # Its own error is about the schedule's sizes: in view.
                _control_schema(
                    toggle=toggle,
                    schedule_open=errors.get("base") == "reserve_exceeds_limit",
                ),
                suggested,
            ),
            errors=errors,
            description_placeholders={"guide_url": CONTROL_GUIDE_URL},
        )


#: The setup form's folded section: the optional features.
SECTION_FEATURES = "features"


def _user_schema(*, features_open: bool = False) -> vol.Schema:
    """The sign-in, and the optional features folded beneath it."""
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(SECTION_FEATURES): section(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_SIMULATOR_ENABLED, default=False
                        ): selector.BooleanSelector(),
                        vol.Optional(
                            CONF_CONTROL_ENABLED, default=False
                        ): selector.BooleanSelector(),
                    }
                ),
                {"collapsed": not features_open},
            ),
        }
    )


class ConfigFlow(  # type: ignore[call-arg,unused-ignore]
    _SimulatorSteps, _ControlSteps, config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for NWP500.

    The sign-in alone sets up what the integration always was. Its
    folded section turns on either optional feature, each with its own
    pages after it.
    """

    VERSION = 1
    # Bumped to 2 in 0.19.0: entries created before then may still hold
    # energy sensors whose backing library fields were removed in
    # nwp500-python 9.3.0. See async_migrate_entry.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None
        # The sign-in, held while the features are set up.
        self._user_input: dict[str, Any] = {}
        self._title = ""
        # The features' options, as each one's pages give them.
        self._options: dict[str, Any] = {}
        self._want_control = False
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
            sign_in = {
                CONF_EMAIL: user_input[CONF_EMAIL],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            features = user_input.get(SECTION_FEATURES) or {}
            try:
                info = await validate_input(self.hass, sign_in)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001 - Config flow must not crash UI
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                email = sign_in[CONF_EMAIL].lower()
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_configured()
                self._user_input = sign_in
                self._title = info["title"]
                self._simulator_devices = info["devices"]
                self._want_control = bool(features.get(CONF_CONTROL_ENABLED))
                if features.get(CONF_SIMULATOR_ENABLED):
                    return await self.async_step_simulator_setup()
                return await self._async_after_simulator()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                # A feature turned on stays in view after a failed sign-in.
                _user_schema(
                    features_open=any(
                        (
                            (user_input or {}).get(SECTION_FEATURES) or {}
                        ).values()
                    )
                ),
                None
                if user_input is None
                else {
                    k: v for k, v in user_input.items() if k != CONF_PASSWORD
                },
            ),
            errors=errors,
        )

    def _simulator_followed(self) -> bool:
        return self._want_control

    async def async_step_simulator_without(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The packages could not be had: set up without the simulator."""
        return await self._async_after_simulator()

    async def _async_simulator_done(
        self, devices: dict[str, dict[str, Any]]
    ) -> config_entries.ConfigFlowResult:
        self._options[CONF_SIMULATOR_ENABLED] = True
        self._options[CONF_SIMULATOR] = devices
        return await self._async_after_simulator()

    async def _async_after_simulator(
        self,
    ) -> config_entries.ConfigFlowResult:
        if self._want_control:
            return await self.async_step_external_control()
        return self._create()

    async def _async_control_done(
        self, options: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._options.update(options)
        return self._create()

    def _create(self) -> config_entries.ConfigFlowResult:
        """The entry; with no feature on, as before the features existed."""
        if not self._options:
            return self.async_create_entry(
                title=self._title, data=self._user_input
            )
        return self.async_create_entry(
            title=self._title, data=self._user_input, options=self._options
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


class OptionsFlowHandler(
    _SimulatorSteps, _ControlSteps, config_entries.OptionsFlow
):
    """Handle options flow for NWP500 integration.

    A menu: the update interval, the hot water simulator, external
    control. Each saves its own pages' answers and nothing else.
    """

    def __init__(self) -> None:
        """Nothing chosen yet."""
        self._simulator_init()

    def _on(self, key: str) -> str:
        return "on" if self.config_entry.options.get(key) else "off"

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """What to change."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "simulator", "external_control"],
            description_placeholders={
                "simulator": self._on(CONF_SIMULATOR_ENABLED),
                "control": self._on(CONF_CONTROL_ENABLED),
            },
        )

    def _save(self, changes: dict[str, Any]) -> config_entries.ConfigFlowResult:
        """The entry's options with `changes`.

        A feature never switched on and left off stores nothing, as the
        release before the feature stored (spec section 1.1.4).
        """
        options = {**self.config_entry.options, **changes}
        for key in (CONF_CONTROL_ENABLED, CONF_SIMULATOR_ENABLED):
            if key not in self.config_entry.options and not options.get(key):
                options.pop(key, None)
        return self.async_create_entry(title="", data=options)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """How often the heater is asked for its status."""
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(
            step_id="settings",
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

    async def async_step_simulator(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Turn the simulator on or off; on, each heater's sensors follow."""
        options = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_SIMULATOR_ENABLED):
                return self._save({CONF_SIMULATOR_ENABLED: False})
            already_on = options.get(CONF_SIMULATOR_ENABLED) is True
            self._simulator_devices = _entry_devices(self.config_entry)
            self._simulator_existing = dict(options.get(CONF_SIMULATOR) or {})
            if self._simulator_devices:
                if already_on:
                    # Already running, or reported in Repairs: changing
                    # its sensors does not check its packages again.
                    self._deps = DepsResult(ok=True)
                return await self.async_step_simulator_setup()
            if already_on:
                return self._save({})
            # Its heaters are known only once the integration is loaded;
            # switched on now, the simulator would start without the
            # sensors beside them, and would install its packages with
            # nothing on screen.
            errors[CONF_SIMULATOR_ENABLED] = "simulator_not_loaded"
        return self.async_show_form(
            step_id="simulator",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SIMULATOR_ENABLED,
                        default=bool(options.get(CONF_SIMULATOR_ENABLED)),
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={"guide_url": SIMULATOR_GUIDE_URL},
            last_step=False,
        )

    async def _async_simulator_done(
        self, devices: dict[str, dict[str, Any]]
    ) -> config_entries.ConfigFlowResult:
        return self._save(
            {CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: devices}
        )

    async def async_step_simulator_without(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The packages could not be had: leave the simulator off."""
        return self._save({CONF_SIMULATOR_ENABLED: False})

    def _control_options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    def _control_toggle(self) -> bool:
        return True

    async def _async_control_done(
        self, options: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        if options.get(CONF_CONTROL_ENABLED):
            # Options an earlier version stored that mean nothing now.
            kept = {
                k: v
                for k, v in self.config_entry.options.items()
                if k not in CONTROL_OBSOLETE_OPTIONS
            }
            return self.async_create_entry(title="", data={**kept, **options})
        return self._save({CONF_CONTROL_ENABLED: False})


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
            'uv pip install "nwp500-python==9.12.2" "awsiotsdk==1.31.0"'
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
