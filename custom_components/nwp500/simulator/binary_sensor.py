"""The simulator's binary sensors: whether a bath is ready."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity

from .sensor import NWP500HotWaterEntity

if TYPE_CHECKING:
    from . import SimulatorFeature


class SimulatorBathReadyBinarySensor(NWP500HotWaterEntity, BinarySensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Whether the tank holds a bath's hot water now."""

    _attr_icon = "mdi:bathtub-outline"

    @property
    def is_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """On when the tank can fill a bath now."""
        answer = self.answer
        readiness = None if answer is None else answer.readiness
        return None if readiness is None else readiness.bath_ready

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The margin, and when the heat pump has it ready if not now."""
        answer = self.answer
        readiness = None if answer is None else answer.readiness
        attributes = self._water_attributes()
        if readiness is not None:
            at = readiness.bath_ready_at
            attributes["bath_gal"] = readiness.bath_gal
            attributes["margin_gal"] = readiness.bath_margin_gal
            attributes["ready_at"] = (
                None
                if at is None
                else at.replace(second=0, microsecond=0).isoformat()
            )
        return attributes


def create_simulator_binary_sensors(
    feature: SimulatorFeature,
) -> list[BinarySensorEntity]:
    """A bath sensor for each heater whose water is metered."""
    return [
        SimulatorBathReadyBinarySensor(runner, "bath_ready")
        for runner in feature.runners.values()
        if runner.has_meter
    ]
