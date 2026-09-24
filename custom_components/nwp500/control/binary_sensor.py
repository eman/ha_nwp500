"""The feature's binary sensors (spec section 4.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import NWP500ControlEntity

if TYPE_CHECKING:
    from . import ControlFeature


class ControlWantedTouBinarySensor(NWP500ControlEntity, BinarySensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Whether the feature wants TOU on."""

    _attr_icon = "mdi:cash-clock"

    @property
    def is_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The wanted TOU state, unknown until there is a baseline."""
        return self.control.wanted.tou_on


class ControlRestoreMatchedBinarySensor(  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    NWP500ControlEntity, BinarySensorEntity
):
    """Whether the last restore read back as the baseline."""

    _attr_icon = "mdi:backup-restore"

    @property
    def is_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Unknown until a restore has happened."""
        restore = self.control.last_restore
        return restore.matches_baseline if restore is not None else None


class ControlOverrideBinarySensor(NWP500ControlEntity, BinarySensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """On while a person's change is being honoured (spec section 5.7)."""

    _attr_icon = "mdi:account-wrench"

    @property
    def is_on(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Whether any override is active."""
        return bool(self.control.overrides)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The most recent override, and every field overridden."""
        overrides = self.control.overrides
        if not overrides:
            return {
                "field": None,
                "value": None,
                "detected_at": None,
                "expires_at": None,
                "fields": [],
            }
        latest = max(overrides.values(), key=lambda o: o.detected_at)
        return {**latest.as_document(), "fields": sorted(overrides)}


def create_control_binary_sensors(
    feature: ControlFeature,
) -> list[BinarySensorEntity]:
    """The binary sensors for every device the feature controls."""
    entities: list[BinarySensorEntity] = []
    for control in feature.devices.values():
        entities.extend(
            (
                ControlWantedTouBinarySensor(control, "wanted_tou"),
                ControlRestoreMatchedBinarySensor(control, "restore_matched"),
                ControlOverrideBinarySensor(control, "override"),
            )
        )
    return entities
