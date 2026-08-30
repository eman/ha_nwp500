"""Sensor platform for Navien NWP500 integration."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import schedule_state
from .const import SENSOR_CONFIGS
from .coordinator import (
    NWP500ConfigEntry,
    NWP500DataUpdateCoordinator,
)
from .entity import NWP500Entity

if TYPE_CHECKING:
    from nwp500 import Device  # type: ignore[attr-defined]

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class NWP500SensorEntityDescription(SensorEntityDescription):
    """Describes NWP500 sensor entity."""

    attr_name: str | None = None
    value_fn: Callable[[Any], Any] | None = None


def create_sensor_descriptions() -> tuple[NWP500SensorEntityDescription, ...]:
    """Create sensor descriptions from configuration data.

    This function creates all sensor entity descriptions from the SENSOR_CONFIGS
    data structure, eliminating ~400 lines of repetitive code.
    """
    descriptions: list[NWP500SensorEntityDescription] = []

    # Unit mapping for string-based units to HA constants
    unit_map: dict[str, str] = {
        "W": UnitOfPower.WATT,
        "Wh": UnitOfEnergy.WATT_HOUR,
        "%": PERCENTAGE,
    }

    # Device class mapping for string-based device classes
    device_class_map: dict[str, SensorDeviceClass] = {
        "temperature": SensorDeviceClass.TEMPERATURE,
        "power": SensorDeviceClass.POWER,
        "energy": SensorDeviceClass.ENERGY,
        "energy_storage": SensorDeviceClass.ENERGY_STORAGE,
        "signal_strength": SensorDeviceClass.SIGNAL_STRENGTH,
        "water": SensorDeviceClass.WATER,
        "duration": SensorDeviceClass.DURATION,
    }

    # Entity category mapping
    entity_category_map: dict[str, EntityCategory] = {
        "diagnostic": EntityCategory.DIAGNOSTIC,
        "config": EntityCategory.CONFIG,
    }

    # State class mapping
    state_class_map: dict[str, SensorStateClass] = {
        "measurement": SensorStateClass.MEASUREMENT,
        "total_increasing": SensorStateClass.TOTAL_INCREASING,
        "total": SensorStateClass.TOTAL,
    }

    for key, config in SENSOR_CONFIGS.items():
        if not isinstance(config, dict):
            continue
        attr_name: str = config["attr"]

        # Check if this is a text/enum sensor (no numeric value)
        is_enum_sensor = config.get("special") == "enum_name"
        is_boolean_sensor = config.get("special") == "boolean"

        # Determine the value function based on special handling needs
        if is_enum_sensor:
            # Special handling for enum types that need .name extraction
            def _make_enum_value_fn(attr: str) -> Callable[[Any], str | None]:
                def value_fn(status: Any) -> str | None:
                    val = getattr(status, attr, None)
                    if val is not None and hasattr(val, "name"):
                        return val.name  # type: ignore[no-any-return]
                    elif val is not None:
                        return str(val)
                    return None

                return value_fn  # noqa: B023

            value_fn = _make_enum_value_fn(attr_name)
        elif is_boolean_sensor:
            # Special handling for boolean sensors - return "On"/"Off"
            def _make_boolean_value_fn(
                attr: str,
            ) -> Callable[[Any], str | None]:
                def value_fn(status: Any) -> str | None:
                    val = getattr(status, attr, None)
                    if val is None:
                        return None
                    return "On" if val else "Off"

                return value_fn  # noqa: B023

            value_fn = _make_boolean_value_fn(attr_name)
        else:
            # Standard attribute getter
            def _make_standard_value_fn(attr: str) -> Callable[[Any], Any]:
                def value_fn(status: Any) -> Any:
                    return getattr(status, attr, None)

                return value_fn  # noqa: B023

            value_fn = _make_standard_value_fn(attr_name)

        # Get unit - None for enum/boolean sensors to prevent numeric interpretation
        unit = config.get("unit")
        if is_enum_sensor or is_boolean_sensor or not unit:
            native_unit = None
        else:
            native_unit = unit_map.get(str(unit), str(unit))

        # Default precision by device class if not explicitly set
        default_precision: dict[str, int] = {
            "temperature": 1,
            "power": 0,
            "energy": 0,
            "energy_storage": 0,
        }
        precision: int | None = config.get(
            "precision",
            default_precision.get(str(config.get("device_class", ""))),
        )

        descriptions.append(
            NWP500SensorEntityDescription(
                key=key,
                attr_name=attr_name,
                translation_key=key,
                device_class=device_class_map.get(
                    str(config.get("device_class", ""))
                ),
                state_class=state_class_map.get(
                    str(config.get("state_class", ""))
                ),
                native_unit_of_measurement=native_unit,
                entity_registry_enabled_default=bool(
                    config.get("enabled", False)
                ),
                suggested_display_precision=precision,
                entity_category=entity_category_map.get(
                    str(config.get("entity_category", ""))
                ),
                value_fn=value_fn,
            )
        )

    return tuple(descriptions)


# Create sensor descriptions once at module load time
SENSOR_DESCRIPTIONS = create_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NWP500ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator = config_entry.runtime_data

    entities: list[SensorEntity] = []

    # Add device-specific sensors
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        for description in SENSOR_DESCRIPTIONS:
            entities.append(
                NWP500Sensor(coordinator, mac_address, device, description)
            )
        # Programmed schedules, readable per device
        entities.append(
            NWP500ReservationScheduleSensor(coordinator, mac_address, device)
        )
        entities.append(
            NWP500TOUScheduleSensor(coordinator, mac_address, device)
        )
        # Cloud-recorded metadata, readable while the device is offline
        entities.append(
            NWP500CloudErrorSensor(coordinator, mac_address, device)
        )
        entities.extend(
            NWP500DescalingSensor(coordinator, mac_address, device, field)
            for field in ("descaling_start_time", "descaling_end_time")
        )

    # Add diagnostic telemetry sensors (one per integration, not per device)
    if coordinator.data:
        # Use first device's mac_address for device association
        first_mac = next(iter(coordinator.data.keys()))
        first_device = coordinator.data[first_mac]["device"]

        entities.extend(
            [
                NWP500LastResponseTimeSensor(
                    coordinator, first_mac, first_device
                ),
                NWP500MQTTRequestCountSensor(
                    coordinator, first_mac, first_device
                ),
                NWP500MQTTResponseCountSensor(
                    coordinator, first_mac, first_device
                ),
                NWP500MQTTConnectedSensor(coordinator, first_mac, first_device),
                NWP500ConsecutiveTimeoutsSensor(
                    coordinator, first_mac, first_device
                ),
            ]
        )

    async_add_entities(entities, True)


class NWP500Sensor(NWP500Entity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 sensor entity."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        description: NWP500SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device)
        self.entity_description = description
        self._attr_unique_id = f"{mac_address}_{description.key}"

    @property
    def native_unit_of_measurement(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the native unit for this field.

        Prioritize the unit reported by the device status to ensure the value matches the unit.
        Fallback to the entity description default if status is unavailable.
        """
        status = self._status

        # 1. Try to get the actual unit from the device status
        if status:
            # Use the actual attribute name for unit lookup in the library,
            # not the entity key which might be different.
            field_name = (
                self.entity_description.attr_name
                if isinstance(
                    self.entity_description, NWP500SensorEntityDescription
                )
                and self.entity_description.attr_name
                else self.entity_description.key
            )
            unit = self.coordinator.get_field_unit_safe(status, field_name)
            if unit:
                return unit

        # 2. For temperature sensors, if we have a status but no specific unit field,
        # we might want to check the coordinator's unit system setting, but it's safer
        # to fall back to the static definition if the device doesn't explicitly tell us.

        # 3. Fallback to entity description unit
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the state of the sensor."""
        if not (status := self._status):
            return None
        description = self.entity_description
        if (
            isinstance(description, NWP500SensorEntityDescription)
            and description.value_fn
        ):
            try:
                return description.value_fn(status)
            except AttributeError, TypeError:
                return None
        return None


class NWP500DiagnosticSensor(NWP500Entity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Base class for diagnostic sensors that report coordinator telemetry."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        key: str,
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_diagnostic_{key}"
        self._attr_translation_key = key


class NWP500LastResponseTimeSensor(NWP500DiagnosticSensor):
    """Sensor showing timestamp of last MQTT response."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "last_response",
        )

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the timestamp of the last response."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        if telemetry["last_response_time"]:
            return datetime.fromtimestamp(
                telemetry["last_response_time"], tz=UTC
            )
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return additional attributes."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        attrs = {
            "last_request_id": telemetry["last_request_id"],
            "last_response_id": telemetry["last_response_id"],
        }
        if telemetry["last_request_time"] and telemetry["last_response_time"]:
            attrs["response_latency"] = (
                telemetry["last_response_time"] - telemetry["last_request_time"]
            )
        return attrs


class NWP500MQTTRequestCountSensor(NWP500DiagnosticSensor):
    """Sensor showing total MQTT requests sent."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "request_count",
        )

    @property
    def native_value(self) -> int:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the total number of requests sent."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        value = telemetry.get("total_requests_sent")
        return int(value) if value is not None else 0


class NWP500MQTTResponseCountSensor(NWP500DiagnosticSensor):
    """Sensor showing total MQTT responses received."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "response_count",
        )

    @property
    def native_value(self) -> int:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the total number of responses received."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        value = telemetry.get("total_responses_received")
        return int(value) if value is not None else 0


class NWP500MQTTConnectedSensor(NWP500DiagnosticSensor):
    """Sensor showing MQTT connection status and duration."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "mqtt_status",
        )

    @property
    def native_value(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the connection status."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        return "connected" if telemetry["mqtt_connected"] else "disconnected"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return additional attributes."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        attrs = {}
        if telemetry["mqtt_connected_since"]:
            attrs["connected_since"] = datetime.fromtimestamp(
                telemetry["mqtt_connected_since"], tz=UTC
            )
            attrs["connected_duration_seconds"] = (
                time.time() - telemetry["mqtt_connected_since"]
            )
        return attrs


class NWP500ConsecutiveTimeoutsSensor(NWP500DiagnosticSensor):
    """Sensor showing consecutive MQTT timeouts."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "consecutive_timeouts",
        )

    @property
    def native_value(self) -> int:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the number of consecutive timeouts."""
        telemetry = self.coordinator.get_mqtt_telemetry()
        return int(telemetry.get("consecutive_timeouts", 0))


class NWP500ScheduleSensor(NWP500DiagnosticSensor):
    """Exposes a programmed schedule as readable entity state.

    The reservation and TOU schedules were previously reachable only as
    coordinator dicts and one-shot bus events, so an external scheduler had
    to run a WebSocket request/event dance and could not simply poll. The
    state is the entry count and the program itself is in the attributes,
    including a canonical hash for cheap desired-vs-programmed comparison.

    No state_class: the entry count is a unitless configuration value, not a
    measurement, and long-term statistics over it would be meaningless.
    """

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        key: str,
        store: str,
        canonical_fn: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Initialize the schedule sensor."""
        super().__init__(coordinator, mac_address, device, key)
        self._store = store
        self._canonical_fn = canonical_fn

    def _schedule(self) -> dict[str, Any] | None:
        """Return the stored schedule, or None if never fetched."""
        store: dict[str, dict[str, Any]] = getattr(
            self.coordinator, self._store
        )
        return store.get(self.mac_address)

    @property
    def native_value(self) -> int | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the number of programmed entries.

        None until the schedule has been read, so "not fetched yet" stays
        distinguishable from "device has no entries".
        """
        schedule = self._schedule()
        if schedule is None:
            return None
        return len(schedule_state.reservation_entries(schedule))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the programmed entries and a hash of the program."""
        schedule = self._schedule()
        if schedule is None:
            return {"entries": [], "enabled": None, "schedule_hash": None}

        return {
            "entries": schedule_state.reservation_entries(schedule),
            "enabled": schedule_state.is_enabled(schedule),
            "schedule_hash": schedule_state.schedule_hash(
                self._canonical_fn(schedule)
            ),
        }


class NWP500ReservationScheduleSensor(NWP500ScheduleSensor):
    """The programmed reservation schedule."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "reservation_schedule",
            "reservation_schedules",
            schedule_state.reservation_canonical,
        )


class NWP500TOUScheduleSensor(NWP500ScheduleSensor):
    """The programmed Time of Use schedule."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            mac_address,
            device,
            "tou_schedule",
            "tou_schedules",
            schedule_state.tou_canonical,
        )


class NWP500CloudMetadataSensor(NWP500DiagnosticSensor):
    """Base for sensors fed by the REST device list rather than by MQTT.

    `NWP500Entity.available` goes False once the device stops answering
    over MQTT, which is the right rule for a sensor reading device status
    and the wrong one here: these values come from the cloud, which keeps
    answering while the device is offline. That is exactly when the
    recorded fault is worth reading, so availability follows the
    coordinator alone.
    """

    @property
    def available(self) -> bool:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return True while the coordinator itself is healthy."""
        return self.coordinator.last_update_success


class NWP500CloudErrorSensor(NWP500CloudMetadataSensor):
    """The device's last recorded fault, as the cloud reports it.

    Distinct from the `error_code` sensor, which reflects the live MQTT
    status and so goes unavailable with the device: the cloud keeps this
    independently and still answers while the device is offline, reporting
    the fault as of the last time the device was heard from.
    """

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device, "cloud_error_code")

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the recorded error code by name, or None if not reported."""
        error = self.coordinator.get_device_error(self.mac_address)
        code = getattr(error, "error_code", None) if error else None
        if code is None:
            return None
        # An unknown code stays a plain int rather than an ErrorCode member.
        return str(getattr(code, "name", code))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return when the recorded fault occurred."""
        error = self.coordinator.get_device_error(self.mac_address)
        return {
            "occurred_at": getattr(error, "error_occurred_time", None)
            if error
            else None
        }


class NWP500DescalingSensor(NWP500CloudMetadataSensor):
    """One end of the descaling window the cloud records for the device.

    Reported as the cloud sends it. Both ends are unset on a device with no
    descaling scheduled or recorded, which is the common case, so these are
    disabled by default.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        field: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device, field)
        self._field = field

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the recorded timestamp, or None if none is recorded."""
        descaling = self.coordinator.get_device_descaling(self.mac_address)
        if not descaling:
            return None
        value = getattr(descaling, self._field, None)
        return str(value) if value is not None else None
