"""Base class for the feature's entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.util import slugify

from ..entity import NWP500Entity

if TYPE_CHECKING:
    from .device import DeviceControl

# The recorder keeps no attributes for a state whose attributes exceed
# 16 KiB (its MAX_STATE_ATTRS_BYTES). The entity's own attributes, such as
# its name and icon, count too, hence the margin.
ATTRIBUTE_BUDGET = 15 * 1024

# The name a heater's device takes without one of its own, as the device
# registry gets it (`NWP500Entity._build_device_info`).
DEFAULT_DEVICE_NAME = "Navien NWP500"


def control_entity_id(domain: str, device_name: str, key: str) -> str:
    """The entity id spec section 4 documents: `<domain>.<device>_control_<key>`.

    Fixed by the key, not by the entity's display name, so a scheduler
    finds the entity whatever it is called.
    """
    return f"{domain}.{slugify(device_name)}_control_{key}"


def _platform_domain(entity: object) -> str:
    for domain, base in (
        ("binary_sensor", BinarySensorEntity),
        ("button", ButtonEntity),
        ("sensor", SensorEntity),
    ):
        if isinstance(entity, base):
            return domain
    raise TypeError(f"{type(entity).__name__} is not a control platform")


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
        # The documented entity id, suggested before the entity is added; a
        # registered entity keeps the one it has.
        device_name = getattr(control.device.device_info, "device_name", None)
        self.entity_id = control_entity_id(
            _platform_domain(self),
            device_name
            if isinstance(device_name, str) and device_name
            else DEFAULT_DEVICE_NAME,
            key,
        )

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

    @override
    def _build_extra_state_attributes(self) -> dict[str, Any]:
        """No device attributes: each entity's attributes are its own."""
        return {}
