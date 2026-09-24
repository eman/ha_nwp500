"""The Disable button (spec section 4.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity

from ..const import CONF_CONTROL_MODE, CONTROL_MODE_DISABLED
from .entity import NWP500ControlEntity

if TYPE_CHECKING:
    from . import ControlFeature


class ControlDisableButton(NWP500ControlEntity, ButtonEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Switches the feature to `disabled`.

    The only control on the dashboard. Enabling, going live and changing
    bounds happen in the options flow.
    """

    _attr_icon = "mdi:hand-back-left-off-outline"

    async def async_press(self) -> None:
        """Set the mode option; the entry reloads through its listener."""
        entry = self.control.entry
        self.hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_CONTROL_MODE: CONTROL_MODE_DISABLED},
        )


def create_control_buttons(feature: ControlFeature) -> list[ButtonEntity]:
    """The buttons for every device the feature controls."""
    return [
        ControlDisableButton(control, "disable")
        for control in feature.devices.values()
    ]
