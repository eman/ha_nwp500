"""Number platform for Navien NWP500 integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MAX_TEMPERATURE_C,
    MAX_TEMPERATURE_F,
    MIN_TEMPERATURE_C,
    MIN_TEMPERATURE_F,
)
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity

if TYPE_CHECKING:
    from nwp500 import Device  # type: ignore[attr-defined]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        entities.append(
            NWP500TargetTemperature(coordinator, mac_address, device)
        )

    async_add_entities(entities, True)


class NWP500TargetTemperature(NWP500Entity, NumberEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 target temperature number entity."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_target_temperature"
        self._attr_name = f"{self.device_name} Target Temperature"
        self._attr_icon = "mdi:thermometer"

    @property
    def native_min_value(self) -> float:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the minimum value."""
        if (
            features := self.coordinator.device_features.get(self.mac_address)
        ) and (
            val := getattr(features, "dhw_temperature_min", None)
        ) is not None:
            return float(val)

        return (
            float(MIN_TEMPERATURE_C)
            if self.native_unit_of_measurement == UnitOfTemperature.CELSIUS
            else float(MIN_TEMPERATURE_F)
        )

    @property
    def native_max_value(self) -> float:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the maximum value."""
        if (
            features := self.coordinator.device_features.get(self.mac_address)
        ) and (
            val := getattr(features, "dhw_temperature_max", None)
        ) is not None:
            return float(val)

        return (
            float(MAX_TEMPERATURE_C)
            if self.native_unit_of_measurement == UnitOfTemperature.CELSIUS
            else float(MAX_TEMPERATURE_F)
        )

    @property
    def native_unit_of_measurement(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the unit of measurement.

        Prefer the unit reported by the device status to ensure consistency with values.
        Fallback to Home Assistant's configured temperature unit.
        """
        if status := self._status:
            try:
                # Try to get unit from DHW target temperature field
                unit = status.get_field_unit("dhw_target_temperature_setting")
                if not unit:
                    unit = status.get_field_unit("dhw_temperature_setting")

                if unit:
                    return str(unit.strip())
            except (AttributeError, TypeError, KeyError, ValueError):
                pass

        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the current target temperature.

        The library handles unit conversion based on HA's configured unit
        system, so this value is already in the correct units.
        """
        if not (status := self._status):
            return None
        try:
            target_temp = getattr(
                status, "dhw_target_temperature_setting", None
            )
            if target_temp is None:
                target_temp = getattr(status, "dhw_temperature_setting", None)
            return float(target_temp) if target_temp is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the target temperature.

        The library handles unit conversion, so the value sent should be
        in HA's configured unit (which the library already expects).
        """
        success = await self.coordinator.async_control_device(
            self.mac_address,
            "set_temperature",
            temperature=float(value),
        )

        if success:
            await self.coordinator.async_request_refresh()
