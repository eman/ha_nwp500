"""The feature's sensor entities (spec sections 4.1 and 4.2)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.util import dt as dt_util

from nwp500.temperature import HalfCelsius

from .entity import NWP500ControlEntity
from .evaluate import STATE_NONE

if TYPE_CHECKING:
    from . import ControlFeature


def _temperatures(raw: int) -> dict[str, float]:
    value = HalfCelsius(raw)
    return {
        "setpoint_f": round(value.to_fahrenheit(), 1),
        "setpoint_c": round(value.to_celsius(), 1),
    }


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
    """State: the plan in force, or `none`."""

    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The plan's intent id."""
        plan = self.control.plan
        return plan.intent_id if plan is not None else STATE_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """When it was issued and received, and the opaque keys."""
        plan = self.control.plan
        if plan is None:
            return {"issued_at": None, "received_at": None}
        received_at = self.control.received_at
        return {
            **plan.extra,
            "issued_at": plan.issued_at.isoformat(),
            "received_at": received_at.isoformat() if received_at else None,
            "segment_count": len(plan.segments),
            "grant_count": len(plan.grants),
        }


class ControlAckSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """State: the acknowledgement of the plan, or of a rejected document."""

    _attr_icon = "mdi:check-decagram-outline"

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """programmed, partly_programmed, pending, rejected, shadow or none."""
        return self.control.ack.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Per-segment and per-grant statuses, and any rejection reason."""
        return self.control.ack.as_attributes()


class ControlProgramHashSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The hash of the list the feature wants on the device."""

    _attr_icon = "mdi:calendar-check"

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Comparable with the Reservation Schedule sensor's hash."""
        return str(self.control.program_details()["hash"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Every entry, and whose it is."""
        details = self.control.program_details()
        return {
            "entry_count": details["entry_count"],
            "entries": details["entries"],
        }


class ControlProgrammedUntilSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """How far the device's copy of the plan reaches."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-end"

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The first segment not programmed, or the last once all are."""
        return self.control.planner.programmed_until

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Whether everything is programmed, and how much waits."""
        planner = self.control.planner
        return {
            "complete": planner.programmed_complete
            if planner.plan is not None
            else None,
            "scheduled": planner.scheduled_count,
        }


class ControlNextEntrySensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """When the next entry the feature owns fires."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-arrow-right"

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The next entry's minute."""
        entry = self.control.planner.next_entry(dt_util.utcnow())
        return entry.fires_at if entry is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """What it sets, and what it serves."""
        entry = self.control.planner.next_entry(dt_util.utcnow())
        if entry is None:
            return {"mode": None, "setpoint_f": None, "setpoint_c": None}
        return {
            "mode": entry.mode,
            **_temperatures(entry.setpoint_raw),
            "kind": entry.kind,
            "serves": entry.serves,
        }


class ControlWantedModeSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The mode the plan puts the heater in now."""

    _attr_icon = "mdi:state-machine"

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """A mode name, unknown without a plan."""
        wanted = self.control.wanted
        return wanted.mode if wanted is not None else None


class ControlWantedSetpointSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The setpoint the plan puts the heater in now, with any surplus raise."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_icon = "mdi:thermometer-auto"

    @property
    def native_unit_of_measurement(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Home Assistant's configured unit."""
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The wanted setpoint."""
        wanted = self.control.wanted
        if wanted is None:
            return None
        celsius = self.native_unit_of_measurement == UnitOfTemperature.CELSIUS
        return round(HalfCelsius(wanted.setpoint_raw).to_preferred(celsius), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The segment it comes from, and the grant raising it, if any."""
        planner = self.control.planner
        plan = planner.plan
        segment = plan.segment_at(dt_util.utcnow()) if plan else None
        raised = planner.raise_state
        return {
            "segment": segment.id if segment else None,
            "grant": raised.grant_id if raised else None,
        }


class ControlLastWriteSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """When the reservation list was last written, sent or simulated."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:content-save-edit-outline"

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The time of the write."""
        write = self.control.last_write
        return write.at if write is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Why, what changed, and whether it was confirmed."""
        write = self.control.last_write
        if write is None:
            return {"reason": None}
        document = write.as_document()
        return {
            "reason": document["reason"],
            "added": document["added"],
            "removed": document["removed"],
            "confirmed": document["confirmed"],
            "simulated": document["simulated"],
            "owner_state": document["owner_state"],
        }


class ControlHeartbeatSensor(NWP500ControlEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Updated at least every 15 minutes while the feature is loaded."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """When the feature last reported itself alive."""
        return self.control.heartbeat


SENSOR_KEYS: tuple[tuple[str, type[NWP500ControlEntity]], ...] = (
    ("capabilities", ControlCapabilitiesSensor),
    ("intent", ControlIntentSensor),
    ("ack", ControlAckSensor),
    ("program_hash", ControlProgramHashSensor),
    ("programmed_until", ControlProgrammedUntilSensor),
    ("next_entry", ControlNextEntrySensor),
    ("wanted_mode", ControlWantedModeSensor),
    ("wanted_setpoint", ControlWantedSetpointSensor),
    ("last_write", ControlLastWriteSensor),
    ("heartbeat", ControlHeartbeatSensor),
)


def create_control_sensors(feature: ControlFeature) -> list[SensorEntity]:
    """The sensors for every device the feature controls."""
    entities: list[SensorEntity] = []
    for control in feature.devices.values():
        for key, cls in SENSOR_KEYS:
            entity = cls(control, key)
            assert isinstance(entity, SensorEntity)  # noqa: S101 - for typing
            entities.append(entity)
    return entities
