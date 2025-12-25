"""The Navien NWP500 integration.

Requires Home Assistant 2025.1+ (Python 3.13-3.14).
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .coordinator import NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.WATER_HEATER,
    Platform.SWITCH,
    Platform.NUMBER,
]

# Service names
SERVICE_SET_RESERVATION = "set_reservation"
SERVICE_UPDATE_RESERVATIONS = "update_reservations"
SERVICE_CLEAR_RESERVATIONS = "clear_reservations"
SERVICE_REQUEST_RESERVATIONS = "request_reservations"
SERVICE_ENABLE_DEMAND_RESPONSE = "enable_demand_response"
SERVICE_DISABLE_DEMAND_RESPONSE = "disable_demand_response"
SERVICE_RESET_AIR_FILTER = "reset_air_filter"
SERVICE_SET_VACATION_DAYS = "set_vacation_days"
SERVICE_SET_RECIRCULATION_MODE = "set_recirculation_mode"
SERVICE_TRIGGER_RECIRCULATION = "trigger_recirculation"

# Service attributes
ATTR_ENABLED = "enabled"
ATTR_DAYS = "days"
ATTR_HOUR = "hour"
ATTR_MINUTE = "minute"
ATTR_OP_MODE = "mode"  # Renamed to avoid conflict with HA's ATTR_MODE
ATTR_TEMPERATURE = "temperature"
ATTR_RESERVATIONS = "reservations"
ATTR_RECIRCULATION_MODE = "mode"

# Valid days of the week
VALID_DAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]

# Valid operation modes for reservations
VALID_MODES = [
    "heat_pump",
    "electric",
    "energy_saver",
    "high_demand",
    "vacation",
    "power_off",
]

# Mode mapping for reservations (friendly name -> DHW mode ID)
MODE_TO_DHW_ID: dict[str, int] = {
    "heat_pump": 1,
    "electric": 2,
    "energy_saver": 3,
    "high_demand": 4,
    "vacation": 5,
    "power_off": 6,
}

# Service schemas
SERVICE_SET_RESERVATION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_ENABLED): cv.boolean,
        vol.Required(ATTR_DAYS): vol.All(cv.ensure_list, [vol.In(VALID_DAYS)]),
        vol.Required(ATTR_HOUR): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=23)
        ),
        vol.Optional(ATTR_MINUTE, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=59)
        ),
        vol.Required(ATTR_OP_MODE): vol.In(VALID_MODES),
        vol.Optional(ATTR_TEMPERATURE): vol.All(
            vol.Coerce(float), vol.Range(min=80, max=150)
        ),
    }
)

SERVICE_UPDATE_RESERVATIONS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_RESERVATIONS): vol.All(cv.ensure_list),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
    }
)

SERVICE_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
    }
)

SERVICE_SET_VACATION_DAYS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=365)
        ),
    }
)

SERVICE_SET_RECIRCULATION_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_RECIRCULATION_MODE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=4)
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NWP500 from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = NWP500DataUpdateCoordinator(hass, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        # Coordinator raises UpdateFailed for all setup errors
        _LOGGER.error("Failed to connect to NWP500: %s", err)
        raise ConfigEntryNotReady from err

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once)
    await _async_setup_services(hass)

    # Set up diagnostics export (periodic JSON exports)
    # The diagnostics module handles test environment detection internally
    try:
        from .diagnostics import async_setup_diagnostics_export

        await async_setup_diagnostics_export(hass, entry)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Failed to setup diagnostics export: %s", err, exc_info=True
        )

    return True


async def _async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for the NWP500 integration."""
    # Only register once
    if hass.services.has_service(DOMAIN, SERVICE_SET_RESERVATION):
        return

    async def _get_coordinator_and_mac(
        call: ServiceCall,
    ) -> tuple[NWP500DataUpdateCoordinator, str]:
        """Get coordinator and MAC address from service call."""
        device_id = call.data[ATTR_DEVICE_ID]
        device_registry = dr.async_get(hass)
        device_entry = device_registry.async_get(device_id)

        if not device_entry:
            raise HomeAssistantError(f"Device {device_id} not found")

        # Find the coordinator for this device
        for _entry_id, coordinator in hass.data[DOMAIN].items():
            if not isinstance(coordinator, NWP500DataUpdateCoordinator):
                continue
            for mac_address in coordinator.data.keys():
                # Check if device identifiers match
                for identifier in device_entry.identifiers:
                    if identifier[0] == DOMAIN and identifier[1] == mac_address:
                        return coordinator, mac_address

        raise HomeAssistantError(
            f"Could not find NWP500 coordinator for device {device_id}"
        )

    async def async_set_reservation(call: ServiceCall) -> None:
        """Handle set_reservation service call."""
        try:
            from nwp500.encoding import build_reservation_entry
        except ImportError as err:
            raise HomeAssistantError(
                "nwp500-python library not available"
            ) from err

        coordinator, mac_address = await _get_coordinator_and_mac(call)

        enabled = call.data[ATTR_ENABLED]
        days = call.data[ATTR_DAYS]
        hour = call.data[ATTR_HOUR]
        minute = call.data.get(ATTR_MINUTE, 0)
        mode = call.data[ATTR_OP_MODE]
        temperature = call.data.get(ATTR_TEMPERATURE)

        # Convert mode string to DHW mode ID
        mode_id = MODE_TO_DHW_ID.get(mode)
        if mode_id is None:
            raise HomeAssistantError(f"Invalid mode: {mode}")

        # For vacation and power_off modes, temperature is optional
        # Use match/case for cleaner logic
        if temperature is None:
            match mode:
                case "vacation" | "power_off":
                    # Use a default value for non-temperature modes
                    temperature = 120.0
                case _:
                    raise HomeAssistantError(
                        f"Temperature is required for mode '{mode}'"
                    )

        # Build the reservation entry using library function
        # Library handles Fahrenheit to half-degrees Celsius conversion
        reservation = build_reservation_entry(
            enabled=enabled,
            days=days,
            hour=hour,
            minute=minute,
            mode_id=mode_id,
            temperature_f=float(temperature),
        )

        _LOGGER.info(
            "Setting reservation for %s: days=%s, time=%02d:%02d, "
            "mode=%s, temp=%s°F",
            mac_address,
            days,
            hour,
            minute,
            mode,
            temperature,
        )

        success = await coordinator.async_update_reservations(
            mac_address, [reservation], enabled=True
        )

        if not success:
            raise HomeAssistantError("Failed to set reservation")

    async def async_update_reservations(call: ServiceCall) -> None:
        """Handle update_reservations service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)

        reservations = call.data[ATTR_RESERVATIONS]
        enabled = call.data.get(ATTR_ENABLED, True)

        if not isinstance(reservations, list):
            raise HomeAssistantError("Reservations must be a list")

        _LOGGER.info(
            "Updating %d reservations for %s (enabled=%s)",
            len(reservations),
            mac_address,
            enabled,
        )

        success = await coordinator.async_update_reservations(
            mac_address, reservations, enabled=enabled
        )

        if not success:
            raise HomeAssistantError("Failed to update reservations")

    async def async_clear_reservations(call: ServiceCall) -> None:
        """Handle clear_reservations service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)

        _LOGGER.info("Clearing all reservations for %s", mac_address)

        # Send empty list to clear all reservations
        success = await coordinator.async_update_reservations(
            mac_address, [], enabled=False
        )

        if not success:
            raise HomeAssistantError("Failed to clear reservations")

    async def async_request_reservations(call: ServiceCall) -> None:
        """Handle request_reservations service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)

        _LOGGER.info("Requesting reservations for %s", mac_address)

        success = await coordinator.async_request_reservations(mac_address)

        if not success:
            raise HomeAssistantError("Failed to request reservations")

    # Register all services with schemas
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_RESERVATION,
        async_set_reservation,
        schema=SERVICE_SET_RESERVATION_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_RESERVATIONS,
        async_update_reservations,
        schema=SERVICE_UPDATE_RESERVATIONS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_RESERVATIONS,
        async_clear_reservations,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_RESERVATIONS,
        async_request_reservations,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    async def async_enable_demand_response(call: ServiceCall) -> None:
        """Handle enable_demand_response service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)
        _LOGGER.info("Enabling demand response for %s", mac_address)
        success = await coordinator.async_send_command(
            mac_address, "enable_demand_response"
        )
        if not success:
            raise HomeAssistantError("Failed to enable demand response")

    async def async_disable_demand_response(call: ServiceCall) -> None:
        """Handle disable_demand_response service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)
        _LOGGER.info("Disabling demand response for %s", mac_address)
        success = await coordinator.async_send_command(
            mac_address, "disable_demand_response"
        )
        if not success:
            raise HomeAssistantError("Failed to disable demand response")

    async def async_reset_air_filter(call: ServiceCall) -> None:
        """Handle reset_air_filter service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)
        _LOGGER.info("Resetting air filter timer for %s", mac_address)
        success = await coordinator.async_send_command(
            mac_address, "reset_air_filter"
        )
        if not success:
            raise HomeAssistantError("Failed to reset air filter timer")

    async def async_set_vacation_days(call: ServiceCall) -> None:
        """Handle set_vacation_days service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)
        days = call.data[ATTR_DAYS]
        _LOGGER.info("Setting vacation mode for %s days on %s", days, mac_address)
        success = await coordinator.async_send_command(
            mac_address, "set_vacation_days", days=days
        )
        if not success:
            raise HomeAssistantError("Failed to set vacation days")

    async def async_set_recirculation_mode(call: ServiceCall) -> None:
        """Handle set_recirculation_mode service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)
        mode = call.data[ATTR_RECIRCULATION_MODE]
        _LOGGER.info(
            "Setting recirculation mode to %d for %s", mode, mac_address
        )
        success = await coordinator.async_send_command(
            mac_address, "set_recirculation_mode", mode=mode
        )
        if not success:
            raise HomeAssistantError("Failed to set recirculation mode")

    async def async_trigger_recirculation(call: ServiceCall) -> None:
        """Handle trigger_recirculation service call."""
        coordinator, mac_address = await _get_coordinator_and_mac(call)
        _LOGGER.info("Triggering recirculation pump for %s", mac_address)
        success = await coordinator.async_send_command(
            mac_address, "trigger_recirculation"
        )
        if not success:
            raise HomeAssistantError("Failed to trigger recirculation")

    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_DEMAND_RESPONSE,
        async_enable_demand_response,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_DEMAND_RESPONSE,
        async_disable_demand_response,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_AIR_FILTER,
        async_reset_air_filter,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VACATION_DAYS,
        async_set_vacation_days,
        schema=SERVICE_SET_VACATION_DAYS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_RECIRCULATION_MODE,
        async_set_recirculation_mode,
        schema=SERVICE_SET_RECIRCULATION_MODE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_RECIRCULATION,
        async_trigger_recirculation,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    _LOGGER.debug("Registered NWP500 reservation services")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

        # Unregister services if no more entries
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_RESERVATION)
            hass.services.async_remove(DOMAIN, SERVICE_UPDATE_RESERVATIONS)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_RESERVATIONS)
            hass.services.async_remove(DOMAIN, SERVICE_REQUEST_RESERVATIONS)

    return unload_ok
