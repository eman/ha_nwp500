"""The Navien NWP500 integration.

Requires Home Assistant 2026.11+.
"""

import asyncio
import contextlib
import importlib
import logging
import shutil
from functools import partial
from pathlib import Path
from typing import Any, Final

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_CORE_CONFIG_UPDATE,
    Platform,
)
from homeassistant.core import (
    Event,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import simulator_ws
from .const import (
    CONF_CONTROL_ENABLED,
    CONF_SIMULATOR_ENABLED,
    CONTROL_UNIQUE_ID_MARKER,
    DOMAIN,
    ISSUE_CONTROL_PLAN_ENTITY_MISSING,
    ISSUE_CONTROL_START_FAILED,
    ISSUE_SIMULATOR_INSTALL_FAILED,
    ISSUE_SIMULATOR_PACKAGES_MISSING,
    ISSUE_SIMULATOR_START_FAILED,
    SIMULATOR_CALIBRATION_STORAGE_KEY,
    SIMULATOR_CALIBRATION_STORAGE_VERSION,
    SIMULATOR_HEATER_ENTITIES,
    SIMULATOR_RETIRED_ENTITIES,
    SIMULATOR_UNIQUE_ID_MARKER,
    control_enabled,
    control_feature,
    control_storage_key,
    simulator_enabled,
    simulator_feature,
    simulator_heater_record_directory,
    simulator_record_directory,
    simulator_started_signal,
)
from .coordinator import (
    NWP500ConfigEntry,
    NWP500Data,
    NWP500DataUpdateCoordinator,
)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

# Frontend card
CARD_URL = f"/{DOMAIN}/nwp500-schedule-card.js"
CARD_PATH = Path(__file__).parent / "www" / "nwp500-schedule-card.js"

VISUAL_CARD_URL = f"/{DOMAIN}/nwp500-visual-card.js"
VISUAL_CARD_PATH = Path(__file__).parent / "www" / "nwp500-visual-card.js"

TANK_CARD_URL = f"/{DOMAIN}/nwp500-tank-card.js"
TANK_CARD_PATH = Path(__file__).parent / "www" / "nwp500-tank-card.js"

VISUAL_IMAGE_URL = f"/{DOMAIN}/nwp500-visual-card.png"
VISUAL_IMAGE_PATH = Path(__file__).parent / "www" / "nwp500-visual-card.png"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.WATER_HEATER,
    Platform.SWITCH,
    Platform.NUMBER,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the NWP500 integration domain (once, before any config entries)."""
    # The simulator's modelled path for charts: registered whether or not
    # the simulator is on, it answers for each heater it runs for.
    simulator_ws.async_register(hass)
    async_setup_services(hass)

    # Serve the bundled Lovelace card JS from the component's www/ directory.
    # This makes the custom card available without users needing to manually
    # register it as a Lovelace resource or install it separately from the
    # Marketplace.
    from homeassistant.components.http import StaticPathConfig

    # (url, path, serve_as_js). Stat-ing these touches the filesystem, so the
    # whole batch is checked in one executor job rather than blocking the
    # event loop once per asset.
    assets: tuple[tuple[str, Path, bool], ...] = (
        (CARD_URL, CARD_PATH, True),
        (VISUAL_CARD_URL, VISUAL_CARD_PATH, True),
        (TANK_CARD_URL, TANK_CARD_PATH, True),
        (VISUAL_IMAGE_URL, VISUAL_IMAGE_PATH, False),
    )

    def _present() -> list[tuple[str, Path, bool]]:
        return [asset for asset in assets if asset[1].is_file()]

    present = await hass.async_add_executor_job(_present)

    if not any(path == CARD_PATH for _url, path, _js in present):
        _LOGGER.warning(
            "Frontend card not found at %s — schedule card will not be available",
            CARD_PATH,
        )

    if not present:
        return True

    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, str(path), False) for url, path, _js in present]
    )
    for url, _path, serve_as_js in present:
        if serve_as_js:
            add_extra_js_url(hass, url)

    _LOGGER.debug("Registered %d frontend asset(s)", len(present))
    return True


# Sensor keys dropped in nwp500-python 9.3.0. The library removed the
# underlying DeviceStatus fields outright rather than aliasing them, so these
# entities can never produce a value again. Left in place they would sit in the
# registry as permanently unavailable with no indication why — and their
# recorded history is wrong regardless, having been captured at a unit scale
# that was 2.5x too large.
_REMOVED_SENSOR_KEYS: Final = (
    "total_energy_capacity",
    "available_energy_capacity",
)


def _async_remove_stale_energy_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove energy sensors whose backing library fields no longer exist."""
    entity_registry = er.async_get(hass)
    suffixes = tuple(f"_{key}" for key in _REMOVED_SENSOR_KEYS)

    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if entity_entry.unique_id.endswith(suffixes):
            _LOGGER.info(
                "Removing %s: its device field was removed in "
                "nwp500-python 9.3.0",
                entity_entry.entity_id,
            )
            entity_registry.async_remove(entity_entry.entity_id)


async def async_migrate_entry(
    hass: HomeAssistant, entry: NWP500ConfigEntry
) -> bool:
    """Migrate an existing config entry to the current schema version.

    Home Assistant calls this whenever the stored entry's version does not
    match the config flow's, including when it is *newer* (a downgrade), so
    each step is guarded and unknown-but-newer entries are left untouched.
    """
    if entry.version > 1:
        # Written by a newer release; nothing here knows how to touch it.
        return False

    if entry.minor_version < 2:
        # Drop energy sensors whose backing DeviceStatus fields were removed
        # outright in nwp500-python 9.3.0. This used to run on every setup;
        # it only ever needed to happen once per entry.
        _async_remove_stale_energy_entities(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
        _LOGGER.debug("Migrated config entry to version 1.2")

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: NWP500ConfigEntry
) -> bool:
    """Set up NWP500 from a config entry."""
    # Unit system is determined by the coordinator from the HA configuration
    # and passed into NavienAuthClient/NavienAPIClient/MQTT. No unit handling
    # is configured directly in this setup function.

    coordinator = NWP500DataUpdateCoordinator(hass, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        # The first refresh runs _setup_clients(), which may already have
        # opened an auth session, connected MQTT and started periodic request
        # tasks before failing. HA discards this coordinator and retries with
        # a fresh one, so without an explicit shutdown every retry would
        # strand a live MQTT session.
        await coordinator.async_shutdown()
        if isinstance(err, ConfigEntryNotReady):
            _LOGGER.error("Failed to connect to NWP500: %s", err)
        raise

    entry.runtime_data = NWP500Data(coordinator)

    # A change to Home Assistant's unit system takes effect at the next
    # update: refresh now, so the library's unit and the entities' agree.
    @callback
    def _on_core_config_update(_event: Event) -> None:
        if coordinator.unit_system != coordinator.ha_unit_system():
            hass.async_create_task(coordinator.async_request_refresh())

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, _on_core_config_update)
    )

    # The external control feature (issue #158) is optional and additive.
    # This is its set-up hook: when the option is on, the subpackage is
    # imported, started, and adds its own platforms. The platforms actually
    # forwarded are recorded so that unload can undo exactly that set --
    # an options change reloads the entry after the options have already
    # changed, so unload cannot recompute them.
    platforms = list(PLATFORMS)
    start_failed_issue = f"{ISSUE_CONTROL_START_FAILED}_{entry.entry_id}"
    if control_enabled(entry):
        from .control import async_setup_control

        try:
            platforms.extend(
                await async_setup_control(hass, entry, coordinator)
            )
        except Exception:
            # The feature is optional: its failure must not take the rest of
            # the integration down with it.
            _LOGGER.exception(
                "External control could not start; the integration runs "
                "without it"
            )
            ir.async_create_issue(
                hass,
                DOMAIN,
                start_failed_issue,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_CONTROL_START_FAILED,
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, start_failed_issue)
    elif ir.async_get(hass).async_get_issue(DOMAIN, start_failed_issue):
        # Switched off after it failed to start: nothing is left to fix.
        ir.async_delete_issue(hass, DOMAIN, start_failed_issue)

    if simulator_enabled(entry) and Platform.BUTTON not in platforms:
        # Its Calibrate simulator button, added once it runs.
        platforms.append(Platform.BUTTON)

    entry.runtime_data.platforms = platforms

    if simulator_enabled(entry):
        # Before the platforms, so the entities come up enabled.
        _async_enable_simulator_inputs(hass, entry)
        _async_remove_retired_simulator_entities(hass, entry.entry_id)

    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # The hot water simulator is optional too: its sensors join the sensor
    # platform, and its button the button platform, once it runs. It starts after the
    # heater's platforms, in the background: installing its packages can
    # take minutes, and the water heater must not wait for it.
    if simulator_enabled(entry):
        entry.runtime_data.simulator_start = entry.async_create_background_task(
            hass,
            _async_start_simulator_and_report(hass, entry),
            f"{DOMAIN} simulator start",
        )
    elif _simulator_was_on(entry):
        # Switched off while the entry was not loaded, so no reload removed
        # what it left. An entry that never had the simulator touches
        # nothing of it.
        await _async_remove_simulator_leftovers(hass, entry.entry_id)

    return True


async def _async_start_simulator_and_report(
    hass: HomeAssistant, entry: NWP500ConfigEntry
) -> None:
    """Start the simulator, and say in Repairs whether it could."""
    issue_id = f"{ISSUE_SIMULATOR_START_FAILED}_{entry.entry_id}"
    failure = await _async_start_simulator(hass, entry)
    if failure is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        async_dispatcher_send(hass, simulator_started_signal(entry.entry_id))
        return
    translation_key, placeholders = failure
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )


async def _async_start_simulator(
    hass: HomeAssistant, entry: NWP500ConfigEntry
) -> tuple[str, dict[str, str]] | None:
    """Start the hot water simulator.

    Returns None once it runs, or else the Repairs issue's translation key
    and placeholders that say why it does not.

    Its packages are checked (and installed if Home Assistant may) first,
    and the package is imported in the import executor: it imports numpy
    and pandas, which take most of a second.
    """
    from .simulator_deps import REASON_MISSING, async_ensure, package_names

    try:
        deps = await async_ensure(hass)
        if not deps.ok:
            packages = package_names(deps.missing)
            _LOGGER.error(
                "The hot water simulator could not start: %s %s",
                packages,
                "is not installed, and Home Assistant is set not to install "
                "packages"
                if deps.reason == REASON_MISSING
                else "could not be installed (restart Home Assistant to "
                "try again)",
            )
            return (
                ISSUE_SIMULATOR_PACKAGES_MISSING
                if deps.reason == REASON_MISSING
                else ISSUE_SIMULATOR_INSTALL_FAILED,
                {"packages": packages},
            )
        module = await hass.async_add_import_executor_job(
            importlib.import_module, f"{__name__}.simulator"
        )
        await module.async_setup_simulator(
            hass, entry, entry.runtime_data.coordinator
        )
    except Exception as err:
        # Optional, like external control: the rest of the integration
        # runs without it.
        _LOGGER.exception(
            "The hot water simulator could not start; the integration runs "
            "without it"
        )
        return ISSUE_SIMULATOR_START_FAILED, {"reason": type(err).__name__}
    return None


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    # Switching external control off removes what it created -- its
    # entities and its stored data -- before the entry comes back without
    # it. It happens here rather than on the next set-up, which must not
    # touch the feature at all.
    feature = control_feature(entry)
    if feature is not None and not control_enabled(entry):
        await feature.async_remove()
    elif (
        feature is None
        and _control_was_on(entry)
        and not control_enabled(entry)
    ):
        # It failed to start, so nothing ran to remove it (#189).
        await _async_remove_control_leftovers(hass, entry.entry_id)
    if _simulator_was_on(entry) and not simulator_enabled(entry):
        # Switching the simulator off removes its sensors, whether or not
        # it was running. It is stopped and forgotten first: a start still
        # running, or the reload's unload stopping it again, would write
        # its record and calibration back after they are removed.
        await _async_cancel_simulator_start(entry)
        if simulator_feature(entry) is not None:
            from .simulator import async_unload_simulator

            await async_unload_simulator(hass, entry)
        await _async_remove_simulator_leftovers(hass, entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove what external control stored; Home Assistant removes the rest.

    The entities go with the entry. The heater keeps what the plans put on
    it, as when the feature is switched off.
    """
    if _control_was_on(entry):
        await _async_remove_control_leftovers(hass, entry.entry_id)
    if _simulator_was_on(entry):
        await _async_remove_simulator_leftovers(hass, entry.entry_id)


def _simulator_was_on(entry: ConfigEntry) -> bool:
    """Whether the simulator was ever switched on for this entry.

    As for external control, the option is stored only once it has been.
    """
    return CONF_SIMULATOR_ENABLED in entry.options


@callback
def _async_enable_simulator_inputs(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Enable the heater's entities the simulator reads the history of.

    Only those the integration disabled by default: one a person disabled
    stays so, and the simulator does without it. Nothing is enabled while
    the entry's "Enable newly added entities" is off: Home Assistant then
    disables every new entity as the integration's, which is the person's
    choice too. Home Assistant reloads the entry once after an entity is
    enabled, as from its settings.
    """
    if entry.pref_disable_new_entities:
        return
    wanted = set(SIMULATOR_HEATER_ENTITIES)
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(
        registry, entry.entry_id
    ):
        # `<mac>_<key>`: the MAC address has no underscore.
        key = entity_entry.unique_id.partition("_")[2]
        if (
            entity_entry.platform == DOMAIN
            and entity_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            and (entity_entry.domain, key) in wanted
        ):
            _LOGGER.info(
                "Enabling %s: the hot water simulator reads its history",
                entity_entry.entity_id,
            )
            registry.async_update_entity(
                entity_entry.entity_id, disabled_by=None
            )


@callback
def _async_remove_retired_simulator_entities(
    hass: HomeAssistant, entry_id: str
) -> None:
    """Remove the simulator's entities an earlier version made."""
    retired = {
        (domain, f"simulator_{key}")
        for domain, key in SIMULATOR_RETIRED_ENTITIES
    }
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
        # `<mac>_simulator_<key>`: the MAC address has no underscore.
        key = entity_entry.unique_id.partition("_")[2]
        if (
            entity_entry.platform == DOMAIN
            and (entity_entry.domain, key) in retired
        ):
            _LOGGER.info(
                "Removing %s: the simulator no longer makes it",
                entity_entry.entity_id,
            )
            registry.async_remove(entity_entry.entity_id)


async def _async_remove_simulator_leftovers(
    hass: HomeAssistant, entry_id: str
) -> None:
    """The simulator's entities, Repairs issue, calibration and record."""
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
        if (
            entity_entry.platform == DOMAIN
            and SIMULATOR_UNIQUE_ID_MARKER in entity_entry.unique_id
        ):
            registry.async_remove(entity_entry.entity_id)
    ir.async_delete_issue(
        hass, DOMAIN, f"{ISSUE_SIMULATOR_START_FAILED}_{entry_id}"
    )
    # Its calibration: the parameters fitted to the heater's history.
    await Store[dict[str, Any]](
        hass,
        SIMULATOR_CALIBRATION_STORAGE_VERSION,
        f"{SIMULATOR_CALIBRATION_STORAGE_KEY}.{entry_id}",
    ).async_remove()
    # The heaters' own records of their statuses.
    await hass.async_add_executor_job(
        partial(
            shutil.rmtree,
            simulator_record_directory(hass, entry_id),
            ignore_errors=True,
        )
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: NWP500ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Let a heater no longer on the account be deleted.

    With it go the simulator's record and calibration of it. A heater
    still on the account comes back at the next update, so it stays.
    """
    macs = {
        ident for domain, ident in device_entry.identifiers if domain == DOMAIN
    }
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime_data, "coordinator", None)
    on_account = set((coordinator.data or {}) if coordinator else {})
    if macs & on_account:
        return False
    simulator = simulator_feature(entry)
    for mac_address in macs:
        if simulator is not None:
            await simulator.calibration.async_forget(mac_address)
        else:
            await _async_forget_calibration(hass, entry.entry_id, mac_address)
        await hass.async_add_executor_job(
            partial(
                shutil.rmtree,
                simulator_heater_record_directory(
                    hass, entry.entry_id, mac_address
                ),
                ignore_errors=True,
            )
        )
    return True


async def _async_forget_calibration(
    hass: HomeAssistant, entry_id: str, mac_address: str
) -> None:
    """Drop one heater from a stopped simulator's calibration store."""
    store = Store[dict[str, Any]](
        hass,
        SIMULATOR_CALIBRATION_STORAGE_VERSION,
        f"{SIMULATOR_CALIBRATION_STORAGE_KEY}.{entry_id}",
    )
    data = await store.async_load()
    if data is None:
        return
    heaters = data.get("heaters")
    if isinstance(heaters, dict) and heaters.pop(mac_address, None) is not None:
        await store.async_save(data)


def _control_was_on(entry: ConfigEntry) -> bool:
    """Whether external control was ever switched on for this entry.

    The option is stored only once it has been (spec section 1.1.4), so an
    entry that never had the feature touches nothing of it.
    """
    return CONF_CONTROL_ENABLED in entry.options


async def _async_remove_control_leftovers(
    hass: HomeAssistant, entry_id: str
) -> None:
    """External control's entities, stored state and Repairs issue."""
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
        if (
            entity_entry.platform == DOMAIN
            and CONTROL_UNIQUE_ID_MARKER in entity_entry.unique_id
        ):
            registry.async_remove(entity_entry.entity_id)
    await Store[dict[str, Any]](
        hass, 1, control_storage_key(entry_id)
    ).async_remove()
    ir.async_delete_issue(
        hass, DOMAIN, f"{ISSUE_CONTROL_START_FAILED}_{entry_id}"
    )
    ir.async_delete_issue(
        hass, DOMAIN, f"{ISSUE_CONTROL_PLAN_ENTITY_MISSING}_{entry_id}"
    )


async def async_unload_entry(
    hass: HomeAssistant, entry: NWP500ConfigEntry
) -> bool:
    """Unload a config entry."""
    data = entry.runtime_data
    # A simulator still starting stops starting; its packages' install, if
    # one runs, goes on (simulator_deps).
    await _async_cancel_simulator_start(entry)
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, data.platforms or PLATFORMS
    ):
        if data.control is not None:
            from .control import async_unload_control

            await async_unload_control(hass, entry)
        if data.simulator is not None:
            from .simulator import async_unload_simulator

            await async_unload_simulator(hass, entry)
        await data.coordinator.async_shutdown()

    return unload_ok


async def _async_cancel_simulator_start(entry: NWP500ConfigEntry) -> None:
    """Stop a simulator start still running, and forget it."""
    data = getattr(entry, "runtime_data", None)
    if data is None or (start := data.simulator_start) is None:
        return
    data.simulator_start = None
    start.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await start
