"""Number platform for Navien NWP500 integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_TEMPERATURE, MIN_TEMPERATURE
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity

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


class NWP500TargetTemperature(NWP500Entity, NumberEntity):
    """Navien NWP500 target temperature number entity."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_TEMPERATURE
    _attr_native_max_value = MAX_TEMPERATURE
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Any,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_target_temperature"
        self._attr_name = f"{self.device_name} Target Temperature"
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self) -> float | None:
        """Return the current target temperature."""
        if not (status := self._status):
            return None

        try:
            target_temp = getattr(status, "dhwTargetTemperatureSetting", None)
            if target_temp is None:
                target_temp = getattr(status, "dhwTemperatureSetting", None)
            return float(target_temp) if target_temp is not None else None
        except (AttributeError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the target temperature."""
        success = await self.coordinator.async_control_device(
            self.mac_address, "set_temperature", temperature=int(value)
        )

        if success:
            await self.coordinator.async_request_refresh()
