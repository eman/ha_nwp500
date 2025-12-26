"""Binary sensor platform for Navien NWP500 integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NWP500DataUpdateCoordinator
from .entity import NWP500Entity


@dataclass(frozen=True)
class NWP500BinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes NWP500 binary sensor entity."""

    value_fn: Callable[[Any], bool | None] | None = None


def create_binary_sensor_descriptions() -> tuple[
    NWP500BinarySensorEntityDescription, ...
]:
    """Create binary sensor descriptions from constants."""
    descriptions = []

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="operation_busy",
            name="Operation Busy",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(status, "operation_busy", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="freeze_protection_use",
            name="Freeze Protection Active",
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(
                status, "freeze_protection_use", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="dhw_use",
            name="DHW In Use",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(status, "dhw_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="dhw_use_sustained",
            name="DHW Use Sustained",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "dhw_use_sustained", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="comp_use",
            name="Compressor Running",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(status, "comp_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="eev_use",
            name="EEV Active",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "eev_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="eva_fan_use",
            name="Evaporator Fan Running",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "eva_fan_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="heat_upper_use",
            name="Upper Electric Heating Element",
            device_class=BinarySensorDeviceClass.HEAT,
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(status, "heat_upper_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="heat_lower_use",
            name="Lower Electric Heating Element",
            device_class=BinarySensorDeviceClass.HEAT,
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(status, "heat_lower_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="scald_use",
            name="Scald Protection Warning",
            device_class=BinarySensorDeviceClass.SAFETY,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "scald_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="anti_legionella_use",
            name="Anti-Legionella Enabled",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "anti_legionella_use", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="anti_legionella_operation_busy",
            name="Anti-Legionella Cycle Running",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "anti_legionella_operation_busy", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="air_filter_alarm_use",
            name="Air Filter Alarm Enabled",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "air_filter_alarm_use", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="error_buzzer_use",
            name="Error Buzzer Enabled",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "error_buzzer_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="eco_use",
            name="Overheat Protection Enabled",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "eco_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="program_reservation_use",
            name="Program Reservation Active",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "program_reservation_use", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="shut_off_valve_use",
            name="Shut-Off Valve Status",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "shut_off_valve_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="con_ovr_sensor_use",
            name="Condensate Overflow Sensor Active",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "con_ovr_sensor_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="wtr_ovr_sensor_use",
            name="Water Leak Detected",
            device_class=BinarySensorDeviceClass.SAFETY,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "wtr_ovr_sensor_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="did_reload",
            name="Device Recently Reloaded",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "did_reload", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="recirculation_use",
            name="Recirculation Active",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(status, "recirc_use", None),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="recirculation_pump_operation_status",
            name="Recirculation Pump Running",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "recirc_pump_operation_status", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="recirculation_operation_busy",
            name="Recirculation Operation Busy",
            device_class=BinarySensorDeviceClass.RUNNING,
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "recirc_operation_busy", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="recirculation_hot_button_ready",
            name="Recirculation Hot Button Ready",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "recirc_hot_btn_ready", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="recirculation_reservation_use",
            name="Recirculation Reservation Active",
            entity_registry_enabled_default=False,
            value_fn=lambda status: getattr(
                status, "recirc_reservation_use", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="tou_override_status",
            name="TOU Override Status",
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(
                status, "tou_override_status", None
            ),
        )
    )

    descriptions.append(
        NWP500BinarySensorEntityDescription(
            key="tou_status",
            name="TOU Status",
            entity_registry_enabled_default=True,
            value_fn=lambda status: getattr(status, "tou_status", None),
        )
    )

    return tuple(descriptions)


BINARY_SENSOR_DESCRIPTIONS = create_binary_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities from a config entry."""
    coordinator: NWP500DataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = []
    for mac_address, device_data in coordinator.data.items():
        device = device_data["device"]
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(
                NWP500BinarySensor(
                    coordinator, mac_address, device, description
                )
            )

    async_add_entities(entities, True)


class NWP500BinarySensor(NWP500Entity, BinarySensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 binary sensor entity."""

    entity_description: NWP500BinarySensorEntityDescription  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Any,
        description: NWP500BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, mac_address, device)
        self.entity_description = description  # pyright: ignore[reportIncompatibleVariableOverride]
        self._attr_unique_id = f"{mac_address}_{description.key}"
        self._attr_name = f"{self.device_name} {description.name}"

    @property
    def is_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return true if the binary sensor is on."""
        if not (status := self._status):
            return None
        if self.entity_description.value_fn:
            try:
                return self.entity_description.value_fn(status)
            except (AttributeError, TypeError):
                return None
        return None
