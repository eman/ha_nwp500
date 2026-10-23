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
        return None if readiness is None else readiness.bath.ready

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """A bath's size, the margin, and when the heat pump has it ready."""
        answer = self.answer
        readiness = None if answer is None else answer.readiness
        attributes = self._readiness_attributes()
        if readiness is not None:
            bath = readiness.bath
            # Ready now, the library's `ready_at` is each walk's start:
            # news only while the bath is still to come.
            at = None if bath.ready else bath.ready_at
            attributes["bath_gal"] = bath.volume_gal
            # Whole gallons: a walk a minute moves it by fractions.
            attributes["margin_gal"] = round(bath.margin_gal)
            attributes["ready_at"] = (
                None
                if at is None
                else at.replace(second=0, microsecond=0).isoformat()
            )
        return attributes


def create_simulator_binary_sensors(
    feature: SimulatorFeature,
) -> list[BinarySensorEntity]:
    """A bath sensor for each heater the simulator runs for."""
    return [
        SimulatorBathReadyBinarySensor(runner, "bath_ready")
        for runner in feature.runners.values()
    ]
