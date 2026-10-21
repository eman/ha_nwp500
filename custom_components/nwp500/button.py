"""Button platform for Navien NWP500 integration.

Only the optional features have buttons, external control and the hot
water simulator, and the platform is forwarded only when one of them is
on, so this module is not imported while both are off.
"""

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    control_feature,
    simulator_enabled,
    simulator_feature,
    simulator_started_signal,
)
from .coordinator import NWP500ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NWP500ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    if (feature := control_feature(hass, config_entry)) is not None:
        from .control.button import create_control_buttons

        async_add_entities(create_control_buttons(feature))

    # The simulator's, once it runs: it starts after the platforms, in the
    # background (see sensor.py).
    if simulator_enabled(config_entry):

        @callback
        def _add_simulator_buttons() -> None:
            simulator = simulator_feature(hass, config_entry)
            if simulator is None:
                return
            from .simulator.calibration_entities import (
                create_calibration_buttons,
            )

            async_add_entities(create_calibration_buttons(simulator))

        config_entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                simulator_started_signal(config_entry.entry_id),
                _add_simulator_buttons,
            )
        )
