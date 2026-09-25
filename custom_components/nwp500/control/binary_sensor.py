"""The feature's binary sensors (spec section 4.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity

from nwp500.temperature import HalfCelsius

from .entity import NWP500ControlEntity

if TYPE_CHECKING:
    from . import ControlFeature


class ControlInSyncBinarySensor(  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    NWP500ControlEntity, BinarySensorEntity
):
    """On when the device's list hashes the same as the program.

    In shadow nothing is written, so this is off whenever the program has
    entries of its own. That comparison is the shadow audit.
    """

    _attr_icon = "mdi:sync"

    @property
    def is_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Unknown until the device's list has been read."""
        details = self.control.program_details()
        if details["device_hash"] is None:
            return None
        return bool(details["device_hash"] == details["hash"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The device's hash, and when that list was first seen."""
        details = self.control.program_details()
        read_at = details["read_at"]
        return {
            "device_hash": details["device_hash"],
            "read_at": read_at.isoformat() if read_at else None,
        }


class ControlGrantRaisedBinarySensor(  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    NWP500ControlEntity, BinarySensorEntity
):
    """On while a surplus raise is in force (spec section 5.7)."""

    _attr_icon = "mdi:solar-power-variant"

    @property
    def is_on(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Whether a raise is in force."""
        return self.control.raise_state is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The grant, when it was raised, and to what."""
        raised = self.control.raise_state
        if raised is None:
            return {"grant": None, "raised_at": None, "setpoint_f": None}
        value = HalfCelsius(raised.entry.setpoint_raw)
        return {
            "grant": raised.grant_id,
            "raised_at": raised.raised_at.isoformat(),
            "fires_at": raised.entry.fires_at.isoformat(),
            "setpoint_f": round(value.to_fahrenheit(), 1),
            "setpoint_c": round(value.to_celsius(), 1),
        }


class ControlOverrideBinarySensor(  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    NWP500ControlEntity, BinarySensorEntity
):
    """On while a person's change is being reported (spec section 5.10)."""

    _attr_icon = "mdi:account-wrench"

    @property
    def is_on(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Whether any change is being reported."""
        return bool(self.control.reports)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The most recent change, and every change being reported."""
        reports = sorted(
            self.control.reports.values(), key=lambda r: r.detected_at
        )
        if not reports:
            return {
                "field": None,
                "value": None,
                "detected_at": None,
                "segment": None,
                "reports": [],
            }
        return {
            **reports[-1].as_document(),
            "reports": [r.as_document() for r in reports],
        }


BINARY_SENSOR_KEYS: tuple[tuple[str, type[NWP500ControlEntity]], ...] = (
    ("in_sync", ControlInSyncBinarySensor),
    ("grant_raised", ControlGrantRaisedBinarySensor),
    ("override", ControlOverrideBinarySensor),
)


def create_control_binary_sensors(
    feature: ControlFeature,
) -> list[BinarySensorEntity]:
    """The binary sensors for every device the feature controls."""
    entities: list[BinarySensorEntity] = []
    for control in feature.devices.values():
        for key, cls in BINARY_SENSOR_KEYS:
            entity = cls(control, key)
            assert isinstance(entity, BinarySensorEntity)  # noqa: S101
            entities.append(entity)
    return entities
