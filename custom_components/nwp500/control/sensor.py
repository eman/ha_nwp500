"""The feature's sensor entities (spec sections 4.1 and 4.2)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature

from nwp500.temperature import HalfCelsius

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


class ControlWantedModeSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The mode the feature wants now; in shadow, what it would write."""

    _attr_icon = "mdi:state-machine"

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """A mode name, unknown until there is a baseline."""
        return self.control.wanted.mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Why the wanted state is what it is."""
        wanted = self.control.wanted
        baseline = self.control.baseline
        return {
            "suspended_by": wanted.suspended_by,
            "holds": list(wanted.holds),
            "restore_pending": wanted.restore_pending,
            "baseline_version": baseline.version if baseline else None,
            "baseline_provisional": baseline.provisional if baseline else None,
        }


class ControlWantedSetpointSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The setpoint the feature wants now, in Home Assistant's unit."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_icon = "mdi:thermometer-auto"

    @property
    def native_unit_of_measurement(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Home Assistant's configured unit."""
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The wanted setpoint."""
        raw = self.control.wanted.setpoint_raw
        if raw is None:
            return None
        celsius = self.native_unit_of_measurement == UnitOfTemperature.CELSIUS
        return round(HalfCelsius(raw).to_preferred(celsius), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The device-resolution value and whether a surplus raise is on."""
        wanted = self.control.wanted
        return {
            "setpoint_raw": wanted.setpoint_raw,
            "surplus_raised": wanted.surplus_raised,
            "holds": list(wanted.holds),
        }


class ControlWantedReservationHashSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The hash the wanted reservation list would produce on the device."""

    _attr_icon = "mdi:calendar-check"

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Comparable with the Reservation Schedule sensor's hash."""
        return self.control.wanted.schedule_hash

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The entries, and which of them the feature owns."""
        wanted = self.control.wanted
        schedule = wanted.schedule or {}
        return {
            "entry_count": len(schedule.get("reservation", [])),
            "enabled": schedule.get("reservation_use") == 2
            if schedule
            else None,
            "entries": list(schedule.get("reservation", [])),
            "owned": [e.as_document() for e in wanted.entries],
        }


class ControlLastRestoreSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The reason for the last restore to the baseline."""

    _attr_icon = "mdi:restore"

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """A reason from spec section 5.4, or `none`."""
        restore = self.control.last_restore
        return restore.reason if restore is not None else STATUS_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """When, and whether it read back as the baseline."""
        restore = self.control.last_restore
        if restore is None:
            return {"at": None, "matches_baseline": None, "pending": None}
        return {
            "at": restore.at.isoformat(),
            "matches_baseline": restore.matches_baseline,
            "pending": self.control.wanted.restore_pending,
        }


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
                ControlWantedModeSensor(control, "wanted_mode"),
                ControlWantedSetpointSensor(control, "wanted_setpoint"),
                ControlWantedReservationHashSensor(
                    control, "wanted_reservation_hash"
                ),
                ControlLastRestoreSensor(control, "last_restore"),
            )
        )
    return entities
