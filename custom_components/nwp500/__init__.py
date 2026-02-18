"""The Navien NWP500 integration.

Requires Home Assistant 2025.1+ (Python 3.13-3.14).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    DEFAULT_TEMPERATURE_C,
    DEFAULT_TEMPERATURE_F,
    DOMAIN,
    MODE_TO_DHW_ID,
)
from .coordinator import NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Frontend card
CARD_URL = f"/{DOMAIN}/nwp500-schedule-card.js"
CARD_PATH = Path(__file__).parent / "www" / "nwp500-schedule-card.js"

VISUAL_CARD_URL = f"/{DOMAIN}/nwp500-visual-card.js"
VISUAL_CARD_PATH = Path(__file__).parent / "www" / "nwp500-visual-card.js"

VISUAL_IMAGE_URL = f"/{DOMAIN}/nwp500-visual-card.png"
VISUAL_IMAGE_PATH = Path(__file__).parent / "www" / "nwp500-visual-card.png"

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
SERVICE_CONFIGURE_TOU = "configure_tou_schedule"
SERVICE_REQUEST_TOU = "request_tou_settings"

# Service attributes
ATTR_ENABLED = "enabled"
ATTR_DAYS = "days"
ATTR_HOUR = "hour"
ATTR_MINUTE = "minute"
ATTR_OP_MODE = "mode"  # Renamed to avoid conflict with HA's ATTR_MODE
ATTR_TEMPERATURE = "temperature"
ATTR_RESERVATIONS = "reservations"
ATTR_PERIODS = "periods"

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

    # Note: Default temperature for modes that don't use it is handled
    # in the service handler to ensure unit-system awareness.
    return data


# Service schemas
SERVICE_SET_RESERVATION_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
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
                vol.Coerce(float), vol.Range(min=0, max=200)
            ),
        }
    ),
    cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_ENTITY_ID),
    validate_reservation_temperature,
)

SERVICE_UPDATE_RESERVATIONS_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
            vol.Required(ATTR_RESERVATIONS): vol.All(
                cv.ensure_list,
                [
                    vol.Schema(
                        {
                            vol.Required("enable"): vol.In(
                                [1, 2], msg="Enable must be 2 (On) or 1 (Off)"
                            ),
                            vol.Required("week"): vol.All(
                                vol.Coerce(int),
                            vol.Range(min=0, max=254),
                            msg="Week must be a bitfield (0-254, Sun=128..Sat=2)",
                        ),
                        vol.Required("hour"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=23),
                            msg="Hour must be 0-23",
                        ),
                        vol.Required("min"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=59),
                            msg="Minute must be 0-59",
                        ),
                        vol.Required("mode"): vol.In(
                            [1, 2, 3, 4, 5, 6],
                            msg="Mode must be 1-6 (HP, ELEC, ECO, BOOST, VAC, OFF)",
                        ),
                        vol.Required("param"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=255),
                            msg="Param must be 0-255 (temperature in half-C)",
                        ),
                    }
                )
            ],
        ),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
    }
)
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

SERVICE_CONFIGURE_TOU_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_PERIODS): vol.All(
            cv.ensure_list,
            vol.Length(max=16),
            [
                vol.Schema(
                    {
                        vol.Required("season"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=4095),
                        ),
                        vol.Required("week"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=127),
                        ),
                        vol.Required("start_hour"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=23),
                        ),
                        vol.Required("start_minute"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=59),
                        ),
                        vol.Required("end_hour"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=23),
                        ),
                        vol.Required("end_minute"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=59),
                        ),
                        vol.Required("price_min"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0),
                        ),
                        vol.Required("price_max"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0),
                        ),
                        vol.Required("decimal_point"): vol.All(
                            vol.Coerce(int),
                            vol.Range(min=0, max=10),
                        ),
                    }
                )
            ],
        ),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
    }
)

SERVICE_REQUEST_TOU_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        }
    ),
    cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_ENTITY_ID),
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
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)

        device_id = call.data.get(ATTR_DEVICE_ID)
        entity_id = call.data.get(ATTR_ENTITY_ID)

        if entity_id:
            entity_entry = entity_registry.async_get(entity_id)
            if not entity_entry:
                raise HomeAssistantError(f"Entity {entity_id} not found")
            device_id = entity_entry.device_id

        if not device_id:
            raise HomeAssistantError("Neither device_id nor entity_id provided")

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

        # SAFETY: Prevent service calls during unit system transitions
        # This prevents temperatures from being set with wrong unit context
        if getattr(coordinator, "_unit_change_in_progress", False):
            raise HomeAssistantError(
                "Cannot set reservation during unit system change. Please try again."
            )

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

        # Temperature is guaranteed by schema validation for most modes.
        # For vacation/power_off, we use a default that matches the unit system.
        if temperature is None:
            if mode in ["vacation", "power_off"]:
                from homeassistant.const import UnitOfTemperature

                temperature = (
                    DEFAULT_TEMPERATURE_C
                    if coordinator.hass.config.units.temperature_unit
                    == UnitOfTemperature.CELSIUS
                    else DEFAULT_TEMPERATURE_F
                )
            else:
                raise HomeAssistantError(
                    f"Temperature is required for mode '{mode}'"
                )

        # Get device features to use device-specific temperature limits
        features = coordinator.device_features.get(mac_address)
        device_temp_min = (
            getattr(features, "dhw_temperature_min", None) if features else None
        )
        device_temp_max = (
            getattr(features, "dhw_temperature_max", None) if features else None
        )

        # Use device-specific limits if available, otherwise fallback to constants
        if device_temp_min is not None and device_temp_max is not None:
            temp_min, temp_max = device_temp_min, device_temp_max
        else:
            # Fallback to hardcoded ranges based on HA unit system
            from homeassistant.const import UnitOfTemperature

            from .const import (
                MAX_TEMPERATURE_C,
                MAX_TEMPERATURE_F,
                MIN_TEMPERATURE_C,
                MIN_TEMPERATURE_F,
            )

            if (
                coordinator.hass.config.units.temperature_unit
                == UnitOfTemperature.CELSIUS
            ):
                temp_min, temp_max = MIN_TEMPERATURE_C, MAX_TEMPERATURE_C
            else:
                temp_min, temp_max = MIN_TEMPERATURE_F, MAX_TEMPERATURE_F

        # Validate temperature range
        if not (temp_min <= temperature <= temp_max):
            # For device-specific limits, don't specify units as they may be in device units
            # For fallback constants, they match HA's unit system
            if device_temp_min is not None and device_temp_max is not None:
                raise HomeAssistantError(
                    f"Temperature {temperature}°{coordinator.hass.config.units.temperature_unit} "
                    f"is outside device valid range ({temp_min}-{temp_max})"
                )
            else:
                raise HomeAssistantError(
                    f"Temperature {temperature}°{coordinator.hass.config.units.temperature_unit} "
                    f"is outside valid range ({temp_min}-{temp_max}°{coordinator.hass.config.units.temperature_unit})"
                )

        # Build the reservation entry using library function
        # Library handles unit conversion based on global context
        # Ensure we never pass None values - use validated temp_min/temp_max as fallbacks
        reservation = build_reservation_entry(
            enabled=enabled,
            days=days,
            hour=hour,
            minute=minute,
            mode_id=mode_id,
            temperature=float(temperature),
            temperature_min=device_temp_min
            if device_temp_min is not None
            else temp_min,
            temperature_max=device_temp_max
            if device_temp_max is not None
            else temp_max,
        )

        _LOGGER.info(
            "Setting reservation for %s: days=%s, time=%02d:%02d, "
            "mode=%s, temp=%s%s",
            mac_address,
            days,
            hour,
            minute,
            mode,
            temperature,
            coordinator.hass.config.units.temperature_unit,
        )

        # Read-modify-write: append to existing schedule instead of replacing
        existing_schedule = coordinator.reservation_schedules.get(
            mac_address, {}
        )
        existing_entries = list(
            existing_schedule.get("reservation", [])
        )
        existing_entries.append(reservation)

        success = await coordinator.async_update_reservations(
            mac_address, existing_entries, enabled=True
        )

        if not success:
            raise HomeAssistantError("Failed to set reservation")

        # Auto-refresh stored reservation state
        await coordinator.async_request_reservations(mac_address)

    async def async_update_reservations(self, call: ServiceCall) -> None:
        """Handle update_reservations service call."""
        coordinator, mac_address = await self._get_coordinator_and_mac(call)

        reservations = call.data[ATTR_RESERVATIONS]
        enabled = call.data[ATTR_ENABLED]

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

        # Auto-refresh stored reservation state
        await coordinator.async_request_reservations(mac_address)

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

        # Auto-refresh stored reservation state
        await coordinator.async_request_reservations(mac_address)

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

    async def async_configure_tou_schedule(self, call: ServiceCall) -> None:
        """Handle configure_tou_schedule service call."""
        coordinator, mac_address = await self._get_coordinator_and_mac(call)

        raw_periods: list[dict[str, Any]] = call.data[ATTR_PERIODS]
        enabled = call.data[ATTR_ENABLED]

        # Convert HA snake_case keys to library camelCase protocol format
        periods = [
            {
                "season": p["season"],
                "week": p["week"],
                "startHour": p["start_hour"],
                "startMinute": p["start_minute"],
                "endHour": p["end_hour"],
                "endMinute": p["end_minute"],
                "priceMin": p["price_min"],
                "priceMax": p["price_max"],
                "decimalPoint": p["decimal_point"],
            }
            for p in raw_periods
        ]

        _LOGGER.info(
            "Configuring TOU schedule with %d periods for %s (enabled=%s)",
            len(periods),
            mac_address,
            enabled,
        )

        success = await coordinator.async_configure_tou_schedule(
            mac_address, periods, enabled=enabled
        )

        if not success:
            raise HomeAssistantError("Failed to configure TOU schedule")

    async def async_request_tou_settings(self, call: ServiceCall) -> None:
        """Handle request_tou_settings service call."""
        coordinator, mac_address = await self._get_coordinator_and_mac(call)

        _LOGGER.info("Requesting TOU settings for %s", mac_address)

        success = await coordinator.async_request_tou_settings(mac_address)

        if not success:
            raise HomeAssistantError("Failed to request TOU settings")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the NWP500 integration domain (once, before any config entries)."""
    # Serve the bundled Lovelace card JS from the component's www/ directory.
    # This makes the custom card available without users needing to manually
    # register it as a Lovelace resource or install it separately via HACS.
    if CARD_PATH.is_file():
        from homeassistant.components.http import StaticPathConfig

        configs = [StaticPathConfig(CARD_URL, str(CARD_PATH), False)]

        if VISUAL_CARD_PATH.is_file():
            configs.append(StaticPathConfig(VISUAL_CARD_URL, str(VISUAL_CARD_PATH), False))
            add_extra_js_url(hass, VISUAL_CARD_URL)

        if VISUAL_IMAGE_PATH.is_file():
            configs.append(StaticPathConfig(VISUAL_IMAGE_URL, str(VISUAL_IMAGE_PATH), False))

        await hass.http.async_register_static_paths(configs)
        add_extra_js_url(hass, CARD_URL)
        _LOGGER.debug("Registered frontend assets")
    else:
        _LOGGER.warning(
            "Frontend card not found at %s — schedule card will not be available",
            CARD_PATH,
        )
    return True


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

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Register services (only once)
    await _async_setup_services(hass)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


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

    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIGURE_TOU,
        handler.async_configure_tou_schedule,
        schema=SERVICE_CONFIGURE_TOU_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_TOU,
        handler.async_request_tou_settings,
        schema=SERVICE_REQUEST_TOU_SCHEMA,
    )

    _LOGGER.debug("Registered NWP500 services")


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
            hass.services.async_remove(DOMAIN, SERVICE_CONFIGURE_TOU)
            hass.services.async_remove(DOMAIN, SERVICE_REQUEST_TOU)

    return unload_ok
