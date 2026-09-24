"""Button platform for Navien NWP500 integration.

Only the external control feature has buttons, and the platform is
forwarded only when that feature is on, so this module is not imported
while it is off.
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import control_feature
from .coordinator import NWP500ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NWP500ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    feature = control_feature(hass, config_entry)
    if feature is None:
        return

    from .control.button import create_control_buttons

    async_add_entities(create_control_buttons(feature))
