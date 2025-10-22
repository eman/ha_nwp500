"""Sensor platform for Navien NWP500 integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPower,
    UnitOfEnergy,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_CONFIGS
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity


@dataclass(frozen=True)
class NWP500SensorEntityDescription(SensorEntityDescription):
    """Describes NWP500 sensor entity."""

    value_fn: Callable[[Any], Any] | None = None


def create_sensor_descriptions() -> tuple[NWP500SensorEntityDescription, ...]:
    """Create sensor descriptions from configuration data.

    This function creates all sensor entity descriptions from the SENSOR_CONFIGS
    data structure, eliminating ~400 lines of repetitive code.
    """
    descriptions: list[NWP500SensorEntityDescription] = []

    # Unit mapping for string-based units to HA constants
    unit_map: dict[str, str] = {
        "°F": UnitOfTemperature.FAHRENHEIT,
        "W": UnitOfPower.WATT,
        "Wh": UnitOfEnergy.WATT_HOUR,
        "%": PERCENTAGE,
    }

    # Device class mapping for string-based device classes
    device_class_map: dict[str, SensorDeviceClass] = {
        "temperature": SensorDeviceClass.TEMPERATURE,
        "power": SensorDeviceClass.POWER,
        "energy": SensorDeviceClass.ENERGY,
        "signal_strength": SensorDeviceClass.SIGNAL_STRENGTH,
    }

    # State class mapping
    state_class_map: dict[str, SensorStateClass] = {
        "measurement": SensorStateClass.MEASUREMENT,
        "total_increasing": SensorStateClass.TOTAL_INCREASING,
    }

    for key, config in SENSOR_CONFIGS.items():
        attr_name: str = config["attr"]  # type: ignore[assignment]
        
        # Check if this is a text/enum sensor (no numeric value)
        is_enum_sensor = config.get("special") == "enum_name"

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

                return value_fn

            value_fn = _make_enum_value_fn(attr_name)
        else:
            # Standard attribute getter
            def _make_standard_value_fn(attr: str) -> Callable[[Any], Any]:
                def value_fn(status: Any) -> Any:
                    return getattr(status, attr, None)

                return value_fn

            value_fn = _make_standard_value_fn(attr_name)
        
        # Get unit - None for enum sensors to prevent numeric interpretation
        unit = config.get("unit", "")
        if is_enum_sensor:
            native_unit = None
        elif unit:
            native_unit = unit_map.get(str(unit), str(unit))
        else:
            native_unit = None

        descriptions.append(
            NWP500SensorEntityDescription(
                key=key,
                name=str(config["name"]),
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
                value_fn=value_fn,
            )
        )

    return tuple(descriptions)


# Create sensor descriptions once at module load time
SENSOR_DESCRIPTIONS = create_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        for description in SENSOR_DESCRIPTIONS:
            entities.append(
                NWP500Sensor(coordinator, mac_address, device, description)
            )

    async_add_entities(entities, True)


class NWP500Sensor(NWP500Entity, SensorEntity):
    """Navien NWP500 sensor entity."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Any,
        description: NWP500SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, mac_address, device)
        self.entity_description = description
        self._attr_unique_id = f"{mac_address}_{description.key}"
        self._attr_name = f"{self.device_name} {description.name}"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor using optimized _status."""
        if not (status := self._status):
            return None

        # Access value_fn from our custom description class
        description = self.entity_description
        if (
            isinstance(description, NWP500SensorEntityDescription)
            and description.value_fn
        ):
            try:
                return description.value_fn(status)
            except (AttributeError, TypeError):
                return None

        return None
