"""Base class for the feature's entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from ..entity import NWP500Entity

if TYPE_CHECKING:
    from .device import DeviceControl


class NWP500ControlEntity(NWP500Entity):
    """An entity that reports the control feature's state for one device.

    Availability follows the feature, not the device: a consumer reads the
    heartbeat to learn the feature is alive even while the heater is
    unreachable.
    """

    def __init__(self, control: DeviceControl, key: str) -> None:
        """Bind to the device's controller."""
        super().__init__(
            control.coordinator, control.mac_address, control.device
        )
        self.control = control
        self._attr_unique_id = f"{control.mac_address}_control_{key}"
        self._attr_translation_key = f"control_{key}"

    async def async_added_to_hass(self) -> None:
        """Follow the controller's updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.control.async_add_listener(self.async_write_ha_state)
        )

    @property
    @override
    def available(self) -> bool:
        """The feature is available while it is loaded."""
        return True
