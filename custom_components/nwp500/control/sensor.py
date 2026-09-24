"""The feature's sensor entities (spec sections 4.1 and 4.2)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import NWP500ControlEntity
from .evaluate import STATUS_NONE

if TYPE_CHECKING:
    from . import ControlFeature


class ControlCapabilitiesSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """State: the declaration's version. Attributes: the declaration."""

    _attr_icon = "mdi:file-certificate-outline"

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The version hash."""
        return self.control.capabilities.version

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The declaration."""
        return self.control.capabilities.as_attributes()


class ControlIntentSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """State: the intent being worked on, or `none`."""

    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The intent id."""
        intent = self.control.intent
        return intent.intent_id if intent is not None else STATUS_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Its validity and the opaque top-level keys."""
        intent = self.control.intent
        if intent is None:
            return {"issued_at": None, "valid_until": None, "received_at": None}
        received_at = self.control.received_at
        return {
            **intent.extra,
            "issued_at": intent.issued_at.isoformat(),
            "valid_until": intent.valid_until.isoformat(),
            "received_at": received_at.isoformat() if received_at else None,
            "directive_count": len(intent.directives),
        }


class ControlAckSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """State: the most recent document's acknowledgement."""

    _attr_icon = "mdi:check-decagram-outline"

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """applied, partly_applied, rejected, shadow or none."""
        return self.control.ack.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Per-directive statuses and the document-level reason."""
        return self.control.ack.as_attributes()


class ControlHeartbeatSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Updated at least every 15 minutes while the feature is loaded."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """When the feature last reported itself alive."""
        return self.control.heartbeat


def create_control_sensors(feature: ControlFeature) -> list[SensorEntity]:
    """The sensors for every device the feature controls."""
    entities: list[SensorEntity] = []
    for control in feature.devices.values():
        entities.extend(
            (
                ControlCapabilitiesSensor(control, "capabilities"),
                ControlIntentSensor(control, "intent"),
                ControlAckSensor(control, "ack"),
                ControlHeartbeatSensor(control, "heartbeat"),
            )
        )
    return entities
