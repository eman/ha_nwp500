"""Water heater platform for Navien NWP500 integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    STATE_ELECTRIC,
)
from homeassistant.const import STATE_OFF
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MAX_TEMPERATURE,
    MIN_TEMPERATURE,
    DHW_OPERATION_SETTING_TO_HA,
    HA_TO_DHW_MODE,
    CURRENT_OPERATION_MODE_TO_HA,
    get_enum_value,
)
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up water heater entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = []
    for mac_address, device_data in coordinator.data.items():
        entities.append(
            NWP500WaterHeater(coordinator, mac_address, device_data["device"])
        )

    async_add_entities(entities, True)


class NWP500WaterHeater(NWP500Entity, WaterHeaterEntity):
    """Navien NWP500 water heater entity."""

    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_min_temp = MIN_TEMPERATURE
    _attr_max_temp = MAX_TEMPERATURE
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.AWAY_MODE
    )

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Any,
    ) -> None:
        """Initialize the water heater."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_water_heater"
        self._attr_name = f"{self.device_name} Water Heater"
        self._attr_operation_list = [
            STATE_ECO,
            STATE_HEAT_PUMP,
            STATE_HIGH_DEMAND,
            STATE_ELECTRIC,
        ]

    @property
    def current_temperature(self) -> float | None:
        """Return the current DHW output temperature."""
        if not (status := self._status):
            return None
        try:
            dhw_temp = getattr(status, "dhwTemperature", None)
            return float(dhw_temp) if dhw_temp is not None else None
        except (AttributeError, TypeError):
            return None

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if not (status := self._status):
            return None
        try:
            target_temp = getattr(status, "dhwTargetTemperatureSetting", None)
            if target_temp is None:
                target_temp = getattr(status, "dhwTemperatureSetting", None)
            return target_temp
        except (AttributeError, TypeError):
            return None

    @property
    def current_operation(self) -> str | None:
        """Return current operation mode based on dhwOperationSetting."""
        if not (status := self._status):
            return None
        try:
            operation_setting = getattr(status, "dhwOperationSetting", None)
            if operation_setting is not None:
                mode_value = get_enum_value(operation_setting)
                if mode_value == 5:
                    return STATE_ECO
                elif mode_value == 6:
                    return STATE_OFF
                else:
                    return DHW_OPERATION_SETTING_TO_HA.get(mode_value, "unknown")
        except (AttributeError, TypeError):
            pass
        return "unknown"

    @property
    def is_on(self) -> bool | None:
        """Return True if the water heater is on."""
        if not (status := self._status):
            return None
        try:
            operation_setting = getattr(status, "dhwOperationSetting", None)
            if operation_setting is not None:
                mode_value = get_enum_value(operation_setting)
                return mode_value not in [0, 6]
            dhw_use = getattr(status, "dhwUse", None)
            comp_use = getattr(status, "compUse", None)
            heat_upper = getattr(status, "heatUpperUse", None)
            heat_lower = getattr(status, "heatLowerUse", None)
            if any([dhw_use, comp_use, heat_upper, heat_lower]):
                return True
            operation_mode = getattr(status, "operationMode", None)
            if operation_mode is not None:
                return get_enum_value(operation_mode) not in [0, 6]
        except (AttributeError, TypeError):
            pass
        return None

    @property
    def is_away_mode_on(self) -> bool | None:
        """Return true if away mode (vacation mode) is on."""
        if not (status := self._status):
            return None
        try:
            operation_setting = getattr(status, "dhwOperationSetting", None)
            if operation_setting is not None:
                return bool(get_enum_value(operation_setting) == 5)
        except (AttributeError, TypeError):
            pass
        return False

    def _build_extra_state_attributes(self) -> dict[str, Any]:
        """Build additional state attributes for water heater."""
        attrs = super()._build_extra_state_attributes()

        if not (status := self._status):
            return attrs

        # Add useful status information
        try:
            # Get the operation modes for display
            operation_mode = getattr(status, "operationMode", None)
            dhw_operation_setting = getattr(
                status, "dhwOperationSetting", None
            )

            # Convert enum operation modes to friendly names using
            # get_enum_value
            current_operation_name = "unknown"
            dhw_setting_name = "unknown"

            # Use CURRENT_OPERATION_MODE_TO_HA for operationMode
            # (current actual state)
            if operation_mode is not None:
                current_operation_name = CURRENT_OPERATION_MODE_TO_HA.get(
                    get_enum_value(operation_mode),
                    f"mode_{get_enum_value(operation_mode)}",
                )

            # Use DHW_OPERATION_SETTING_TO_HA for dhwOperationSetting
            # (user configured mode)
            if dhw_operation_setting is not None:
                dhw_value = get_enum_value(dhw_operation_setting)
                dhw_setting_name = DHW_OPERATION_SETTING_TO_HA.get(
                    dhw_value, f"mode_{dhw_value}"
                )

            # Performance optimization: Batch fetch multiple status
            # attributes at once. Single pass through device status
            # dict vs 13 individual getattr() calls. Reduces attribute
            # access overhead and improves extra_state_attributes perf
            status_attrs = self._get_status_attrs(
                "outsideTemperature",
                "operationBusy",
                "errorCode",
                "subErrorCode",
                "dischargeTemperature",
                "suctionTemperature",
                "freezeProtectionUse",
                "currentInstPower",
                "dhwChargePer",
                "wifiRssi",
                "compUse",
                "heatUpperUse",
                "heatLowerUse",
            )

            attrs.update(
                {
                    # User-friendly operation mode display
                    # What user has configured
                    "dhw_mode_setting": dhw_setting_name,
                    # What device is doing
                    "current_operation_state": current_operation_name,
                    "mode_description": (
                        f"Set: {dhw_setting_name.replace('_', ' ').title()}"
                        f", Running: "
                        f"{current_operation_name.replace('_', ' ').title()}"
                    ),
                    # Temperature and status info
                    "outside_temperature": status_attrs["outsideTemperature"],
                    "operation_busy": status_attrs["operationBusy"],
                    "error_code": status_attrs["errorCode"],
                    "sub_error_code": status_attrs["subErrorCode"],
                    "discharge_temperature": status_attrs[
                        "dischargeTemperature"
                    ],
                    "suction_temperature": status_attrs["suctionTemperature"],
                    "freeze_protection_active": status_attrs[
                        "freezeProtectionUse"
                    ],
                    "current_power": status_attrs["currentInstPower"],
                    "dhw_charge_percentage": status_attrs["dhwChargePer"],
                    "wifi_rssi": status_attrs["wifiRssi"],
                    # Component status (from efficient batch get)
                    "compressor_running": status_attrs["compUse"],
                    "upper_element_on": status_attrs["heatUpperUse"],
                    "lower_element_on": status_attrs["heatLowerUse"],
                    # Raw values for diagnostics
                    "operation_mode_raw": operation_mode,
                    "dhw_operation_setting_raw": dhw_operation_setting,
                }
            )
        except (AttributeError, TypeError):
            pass

        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature.

        Uses set_dhw_temperature_display() which takes the display temperature
        directly (what users see on device/app) without requiring conversion.
        """
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # Validate temperature range
        if not (self.min_temp <= temperature <= self.max_temp):
            _LOGGER.error(
                "Temperature %s out of range (%s-%s)",
                temperature,
                self.min_temp,
                self.max_temp,
            )
            return

        success = await self.coordinator.async_control_device(
            self.mac_address, "set_temperature", temperature=int(temperature)
        )

        if success:
            # Trigger immediate data refresh
            await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set new operation mode using DHW mode control."""
        # Map Home Assistant operation mode to DHW mode value
        dhw_mode_value = HA_TO_DHW_MODE.get(operation_mode)

        if dhw_mode_value is None:
            _LOGGER.error("Invalid operation mode: %s", operation_mode)
            return

        _LOGGER.debug(
            "Setting DHW mode to %s (value: %d)", operation_mode, dhw_mode_value
        )

        success = await self.coordinator.async_control_device(
            self.mac_address, "set_dhw_mode", mode=dhw_mode_value
        )

        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to set operation mode to %s", operation_mode)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the water heater on by setting it to energy saver mode."""
        # When turning "on", set to energy saver (eco) mode as default
        await self.async_set_operation_mode(STATE_ECO)

    async def async_turn_away_mode_on(self) -> None:
        """Turn away mode on by setting to vacation mode."""
        # Vacation mode is handled separately from operation modes
        # since it's not in operation_list. This follows HA design
        # where away_mode is a dedicated feature, not an op mode
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_dhw_mode", mode=5  # VACATION mode
        )

        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to set vacation mode")

    async def async_turn_away_mode_off(self) -> None:
        """Turn away mode off by returning to eco mode."""
        await self.async_set_operation_mode(STATE_ECO)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the water heater off by setting to power off mode."""
        # Use DHW mode 6 (POWER_OFF) instead of the uncertain set_power method
        # This maps to the "off" operation mode in our DHW_MODE_TO_HA mapping
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_dhw_mode", mode=6  # POWER_OFF mode
        )

        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to set water heater to power off mode")
