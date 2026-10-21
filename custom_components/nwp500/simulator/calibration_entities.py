"""The simulator's calibration, for each heater.

A diagnostic sensor says when the heater was last fitted and what each
term came to; a button calibrates it now.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from ..entity import NWP500Entity

if TYPE_CHECKING:
    from . import SimulatorFeature

# Long: kept off the recorder.
_UNRECORDED = frozenset({"terms", "notes"})


class NWP500CalibrationEntity(NWP500Entity):
    """An entity about one heater's calibration."""

    def __init__(
        self, feature: SimulatorFeature, mac_address: str, key: str
    ) -> None:
        """Bind to the feature, for one heater."""
        device = feature.coordinator.data[mac_address]["device"]
        super().__init__(feature.coordinator, mac_address, device)
        self.feature = feature
        self._attr_unique_id = f"{mac_address}_simulator_{key}"
        self._attr_translation_key = f"simulator_{key}"

    @property
    def running(self) -> bool:
        """Whether this heater is being calibrated now."""
        return self.feature.calibrating == self.mac_address

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the calibration as well as the coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(self.feature.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()


class SimulatorCalibrationSensor(NWP500CalibrationEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """When the simulator was last fitted to the heater, and to what."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:tune-variant"
    _unrecorded_attributes = _UNRECORDED

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """When it was last fitted; unknown while it runs on the defaults."""
        calibration = self.feature.calibrations.get(self.mac_address) or {}
        fitted_at = calibration.get("fitted_at")
        if not isinstance(fitted_at, str):
            return None
        try:
            return datetime.fromisoformat(fitted_at)
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The last run: what each term came to, and why one was kept."""
        report = self.feature.reports.get(self.mac_address)
        attributes: dict[str, Any] = {"running": self.running}
        if report is None:
            return attributes
        doc = report.as_document()
        return {
            **attributes,
            "last_run": doc["at"],
            "fitted": doc["fitted"],
            "error": doc["error"],
            "statuses": doc["statuses"],
            "gaps": doc["gaps"],
            "air": doc["air"],
            "source": doc["source"],
            "applied": doc["applied"],
            "duration_s": doc["duration_s"],
            "terms": doc["terms"],
            "notes": doc["notes"],
        }


class SimulatorCalibrateButton(NWP500CalibrationEntity, ButtonEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Calibrates the simulator now, from the history recorded so far."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:tune-variant"

    @property
    @override
    def available(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Not while a calibration runs."""
        return self.feature.calibrating is None and super().available

    async def async_press(self) -> None:
        """Calibrate in the background; the sensor reports the result."""
        self.feature.async_request_calibration(self.mac_address)


def create_calibration_sensors(
    feature: SimulatorFeature,
) -> list[SensorEntity]:
    """A calibration sensor for each heater."""
    return [
        SimulatorCalibrationSensor(feature, mac_address, "calibration")
        for mac_address in feature.runners
    ]


def create_calibration_buttons(
    feature: SimulatorFeature,
) -> list[ButtonEntity]:
    """A calibrate button for each heater."""
    return [
        SimulatorCalibrateButton(feature, mac_address, "calibrate")
        for mac_address in feature.runners
    ]
