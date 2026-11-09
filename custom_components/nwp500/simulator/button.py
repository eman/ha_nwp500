"""The simulator's button: calibrate the heater's parameters now."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from ..const import DOMAIN
from ..entity import NWP500Entity
from .calibration import CalibrationError

if TYPE_CHECKING:
    from . import SimulatorFeature
    from .runner import SimulatorRunner


class SimulatorCalibrateButton(NWP500Entity, ButtonEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Calibrate the heater's parameters to its history, now.

    As the weekly calibration does: the library refines the kept
    parameters, keeping the better of the two.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:tune-variant"

    def __init__(
        self, feature: SimulatorFeature, runner: SimulatorRunner
    ) -> None:
        """Bind to the heater's calibration."""
        device = runner.coordinator.data[runner.mac_address]["device"]
        super().__init__(runner.coordinator, runner.mac_address, device)
        self.feature = feature
        self._attr_unique_id = f"{runner.mac_address}_simulator_calibrate"
        self._attr_translation_key = "simulator_calibrate"

    @property
    @override
    def available(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Always: the calibration reads the recorder, not the heater now."""
        return True

    @override
    async def async_press(self) -> None:
        """Calibrate; say why when it can't."""
        try:
            await self.feature.async_calibrate(self.mac_address)
        except CalibrationError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="simulator_calibration_failed",
                translation_placeholders={"reason": str(err)},
            ) from err


def create_simulator_buttons(feature: SimulatorFeature) -> list[ButtonEntity]:
    """A calibrate button for each heater the simulator runs for."""
    return [
        SimulatorCalibrateButton(feature, runner)
        for runner in feature.runners.values()
    ]
