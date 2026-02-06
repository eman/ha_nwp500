"""The Navien NWP500 integration.

Requires Home Assistant 2025.1+ (Python 3.13-3.14).
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DEFAULT_TEMPERATURE, DOMAIN, MODE_TO_DHW_ID
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
SERVICE_SET_VACATION_DAYS = "set_vacation_days"

# Service attributes
ATTR_ENABLED = "enabled"
ATTR_DAYS = "days"
ATTR_HOUR = "hour"
ATTR_MINUTE = "minute"
ATTR_OP_MODE = "mode"  # Renamed to avoid conflict with HA's ATTR_MODE
ATTR_TEMPERATURE = "temperature"
ATTR_RESERVATIONS = "reservations"

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


def validate_reservation_temperature(data: dict[str, Any]) -> dict[str, Any]:
    """Validate that temperature is provided for modes that require it."""
    mode = data.get(ATTR_OP_MODE)
    temperature = data.get(ATTR_TEMPERATURE)

    if mode not in ["vacation", "power_off"] and temperature is None:
        raise vol.Invalid(f"Temperature is required for mode '{mode}'")

    # Set default temperature for modes that don't use it but require a value for the library
    if temperature is None and mode in ["vacation", "power_off"]:
        data[ATTR_TEMPERATURE] = DEFAULT_TEMPERATURE

    return data


# Service schemas
SERVICE_SET_RESERVATION_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_ENABLED): cv.boolean,
            vol.Required(ATTR_DAYS): vol.All(
                cv.ensure_list, [vol.In(VALID_DAYS)]
            ),
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
    ),
    validate_reservation_temperature,
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


class NWP500ServiceHandler:
    """Handles all NWP500 service calls with dependency injection.

    This class encapsulates service handler logic, making it testable
    and avoiding closure complexity from nested functions.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the service handler."""
        self.hass = hass

    async def _get_coordinator_and_mac(
        self, call: ServiceCall
    ) -> tuple[NWP500DataUpdateCoordinator, str]:
        """Get coordinator and MAC address from service call."""
        device_id = call.data[ATTR_DEVICE_ID]
        device_registry = dr.async_get(self.hass)
        device_entry = device_registry.async_get(device_id)

        if not device_entry:
            raise HomeAssistantError(f"Device {device_id} not found")

        # Find the coordinator for this device
        for _entry_id, coordinator in self.hass.data[DOMAIN].items():
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

    async def async_set_reservation(self, call: ServiceCall) -> None:
        """Handle set_reservation service call."""
        try:
            from nwp500.encoding import build_reservation_entry
        except ImportError as err:
            raise HomeAssistantError(
                "nwp500-python library not available"
            ) from err

        coordinator, mac_address = await self._get_coordinator_and_mac(call)

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

        # Temperature is guaranteed by schema validation, but we check again for safety
        if temperature is None:
            if mode in ["vacation", "power_off"]:
                temperature = DEFAULT_TEMPERATURE
            else:
                raise HomeAssistantError(
                    f"Temperature is required for mode '{mode}'"
                )

        # Get device features to pass min/max limits if available
        features = coordinator.device_features.get(mac_address)
        temp_min = (
            getattr(features, "dhw_temperature_min", None) if features else None
        )
        temp_max = (
            getattr(features, "dhw_temperature_max", None) if features else None
        )

        # Build the reservation entry using library function
        # Library handles unit conversion based on global context
        reservation = build_reservation_entry(
            enabled=enabled,
            days=days,
            hour=hour,
            minute=minute,
            mode_id=mode_id,
            temperature=float(temperature),
            temperature_min=temp_min,
            temperature_max=temp_max,
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

    async def async_update_reservations(self, call: ServiceCall) -> None:
        """Handle update_reservations service call."""
        coordinator, mac_address = await self._get_coordinator_and_mac(call)

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

    async def async_clear_reservations(self, call: ServiceCall) -> None:
        """Handle clear_reservations service call."""
        coordinator, mac_address = await self._get_coordinator_and_mac(call)

        _LOGGER.info("Clearing all reservations for %s", mac_address)

        # Send empty list to clear all reservations
        success = await coordinator.async_update_reservations(
            mac_address, [], enabled=False
        )

        if not success:
            raise HomeAssistantError("Failed to clear reservations")

    async def async_request_reservations(self, call: ServiceCall) -> None:
        """Handle request_reservations service call."""
        coordinator, mac_address = await self._get_coordinator_and_mac(call)

        _LOGGER.info("Requesting reservations for %s", mac_address)

        success = await coordinator.async_request_reservations(mac_address)

        if not success:
            raise HomeAssistantError("Failed to request reservations")

    async def async_set_vacation_days(self, call: ServiceCall) -> None:
        """Handle set_vacation_days service call."""
        coordinator, mac_address = await self._get_coordinator_and_mac(call)
        days = call.data[ATTR_DAYS]
        _LOGGER.info(
            "Setting vacation mode for %s days on %s", days, mac_address
        )
        success = await coordinator.async_send_command(
            mac_address, "set_vacation_days", days=days
        )
        if not success:
            raise HomeAssistantError("Failed to set vacation days")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NWP500 from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Unit system is determined by the coordinator from the HA configuration
    # and passed into NavienAuthClient/NavienAPIClient/MQTT. No unit handling
    # is configured directly in this setup function.

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

    return True


async def _async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for the NWP500 integration."""
    # Only register once
    if hass.services.has_service(DOMAIN, SERVICE_SET_RESERVATION):
        return

    # Create service handler instance
    handler = NWP500ServiceHandler(hass)

    # Register all services with schemas
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_RESERVATION,
        handler.async_set_reservation,
        schema=SERVICE_SET_RESERVATION_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_RESERVATIONS,
        handler.async_update_reservations,
        schema=SERVICE_UPDATE_RESERVATIONS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_RESERVATIONS,
        handler.async_clear_reservations,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_RESERVATIONS,
        handler.async_request_reservations,
        schema=SERVICE_DEVICE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VACATION_DAYS,
        handler.async_set_vacation_days,
        schema=SERVICE_SET_VACATION_DAYS_SCHEMA,
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
            hass.services.async_remove(DOMAIN, SERVICE_SET_VACATION_DAYS)

    return unload_ok
