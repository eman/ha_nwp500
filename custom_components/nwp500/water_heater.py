"""Water heater platform for Navien NWP500 integration."""

import logging
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_ELECTRIC,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_OFF,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from nwp500.enums import DhwOperationSetting
from nwp500.temperature import HalfCelsius

from .const import (
    DOMAIN,
    HA_TO_DHW_MODE,
    MAX_TEMPERATURE_C,
    MAX_TEMPERATURE_F,
    MIN_TEMPERATURE_C,
    MIN_TEMPERATURE_F,
    get_current_operation_mode_state,
    get_dhw_operation_setting_state,
    get_enum_value,
)
from .coordinator import (
    NWP500ConfigEntry,
    NWP500DataUpdateCoordinator,
)
from .entity import NWP500Entity

if TYPE_CHECKING:
    from nwp500 import Device  # type: ignore[attr-defined]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NWP500ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up water heater entities from a config entry."""
    coordinator = config_entry.runtime_data

    entities = []
    for mac_address, device_data in coordinator.data.items():
        entities.append(
            NWP500WaterHeater(coordinator, mac_address, device_data["device"])
        )

    async_add_entities(entities, True)


class NWP500WaterHeater(NWP500Entity, WaterHeaterEntity, RestoreEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Navien NWP500 water heater entity."""

    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.AWAY_MODE
    )

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
    ) -> None:
        """Initialize the water heater."""
        super().__init__(coordinator, mac_address, device)
        self._attr_unique_id = f"{mac_address}_water_heater"
        self._attr_translation_key = "water_heater"
        self._attr_operation_list = [
            STATE_ECO,
            STATE_HEAT_PUMP,
            STATE_HIGH_DEMAND,
            STATE_ELECTRIC,
        ]
        self._pre_vacation_mode: str | None = None

    async def async_added_to_hass(self) -> None:
        """Restore pre-vacation mode from state on HA restart."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            self._pre_vacation_mode = last_state.attributes.get(
                "pre_vacation_mode"
            )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return extra state attributes, including persisted vacation restore mode."""
        attrs: dict[str, Any] = dict(super().extra_state_attributes or {})
        if self._pre_vacation_mode is not None:
            attrs = {**attrs, "pre_vacation_mode": self._pre_vacation_mode}
        return attrs

    def _device_limit(self, raw_field: str) -> float | None:
        """Convert a raw half-degree-Celsius feature limit to this entity's unit.

        Converted here rather than read from the library's converted field,
        which follows the library's global unit system. The coordinator
        updates that only on its next poll, while `temperature_unit` follows
        Home Assistant at once, so after a unit change the converted field
        would be in the other scale for up to a poll interval.
        """
        features = self.coordinator.device_features.get(self.mac_address)
        raw = getattr(features, raw_field, None) if features else None
        if not isinstance(raw, int) or isinstance(raw, bool):
            return None
        return HalfCelsius(raw).to_preferred(
            self.temperature_unit == UnitOfTemperature.CELSIUS
        )

    @property
    @override
    def min_temp(self) -> float:
        """Return the lowest setpoint the device accepts.

        Taken from the device's feature data. The platform constant is wider
        than the device's own range, so offering it would let the UI propose
        a setpoint the library then rejects. It is used only until the
        feature data arrives.
        """
        if (limit := self._device_limit("dhw_temperature_min_raw")) is not None:
            return limit
        return (
            float(MIN_TEMPERATURE_C)
            if self.temperature_unit == UnitOfTemperature.CELSIUS
            else float(MIN_TEMPERATURE_F)
        )

    @property
    @override
    def max_temp(self) -> float:
        """Return the highest setpoint the device accepts.

        Taken from the device's feature data, falling back to the platform
        constant until that arrives, as `min_temp` does.
        """
        if (limit := self._device_limit("dhw_temperature_max_raw")) is not None:
            return limit
        return (
            float(MAX_TEMPERATURE_C)
            if self.temperature_unit == UnitOfTemperature.CELSIUS
            else float(MAX_TEMPERATURE_F)
        )

    @property
    @override
    def temperature_unit(self) -> str:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return Home Assistant's configured temperature unit.

        The library handles unit conversion based on HA's configured unit
        system, so values are already in the correct units.
        """
        return self.hass.config.units.temperature_unit

    @property
    @override
    def current_temperature(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the current DHW output temperature."""
        if not (status := self._status):
            return None
        try:
            temp = getattr(status, "dhw_temperature", None)
            return float(temp) if temp is not None else None
        except AttributeError, TypeError:
            return None

    @property
    @override
    def target_temperature(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return the temperature we try to reach."""
        if not (status := self._status):
            return None
        try:
            target_temp = getattr(
                status, "dhw_target_temperature_setting", None
            )
            if target_temp is None:
                target_temp = getattr(status, "dhw_temperature_setting", None)
            return float(target_temp) if target_temp is not None else None
        except AttributeError, TypeError:
            return None

    @property
    @override
    def current_operation(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return current operation mode based on dhwOperationSetting."""
        if not (status := self._status):
            return None
        try:
            operation_setting = getattr(status, "dhw_operation_setting", None)
            if operation_setting is not None:
                mode_value = get_enum_value(operation_setting)
                if mode_value == DhwOperationSetting.VACATION:
                    # Vacation mode: show the mode that was active before
                    # vacation so the UI reflects what will be restored.
                    return self._pre_vacation_mode or STATE_ECO
                return get_dhw_operation_setting_state(
                    operation_setting, default="unknown"
                )
        except AttributeError, TypeError:
            pass
        return None

    @property
    @override
    def is_away_mode_on(self) -> bool | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Return true if away mode (vacation mode) is on."""
        if not (status := self._status):
            return None
        try:
            operation_setting = getattr(status, "dhw_operation_setting", None)
            if operation_setting is not None:
                return bool(
                    get_enum_value(operation_setting)
                    == DhwOperationSetting.VACATION
                )
        except AttributeError, TypeError:
            pass
        return False

    def _build_extra_state_attributes(self) -> dict[str, Any]:
        """Build additional state attributes for water heater."""
        attrs = super()._build_extra_state_attributes()

        if not (status := self._status):
            return attrs

        # Add useful status information
        try:
            # Get the operation modes for display
            operation_mode = getattr(status, "operation_mode", None)
            dhw_operation_setting = getattr(
                status, "dhw_operation_setting", None
            )

            # Convert enum operation modes to friendly names using
            # get_enum_value
            current_operation_name = "unknown"
            dhw_setting_name = "unknown"

            # Use CURRENT_OPERATION_MODE_TO_HA for operationMode
            # (current actual state)
            if operation_mode is not None:
                current_operation_name = get_current_operation_mode_state(
                    operation_mode
                )

            # Use DHW_OPERATION_SETTING_TO_HA for dhwOperationSetting
            # (user configured mode)
            if dhw_operation_setting is not None:
                dhw_setting_name = get_dhw_operation_setting_state(
                    dhw_operation_setting
                )

            # Performance optimization: Batch fetch multiple status attributes.
            # Python 3.13's improved dict performance makes this even more efficient.
            status_attrs = self._get_status_attrs(
                "outside_temperature",
                "operation_busy",
                "error_code",
                "sub_error_code",
                "discharge_temperature",
                "suction_temperature",
                "freeze_protection_use",
                "current_inst_power",
                "dhw_charge_per",
                "wifi_rssi",
                "comp_use",
                "heat_upper_use",
                "heat_lower_use",
            )

            attrs.update(
                {
                    # User-friendly operation mode display
                    # What user has configured
                    "dhw_mode_setting": dhw_setting_name,
                    # What device is doing
                    "current_operation_state": current_operation_name,
                    "mode_description": (
                        f"Set: {dhw_setting_name.replace('_', ' ').title()}"
                        f", Running: "
                        f"{current_operation_name.replace('_', ' ').title()}"
                    ),
                    # Temperature and status info
                    "outside_temperature": status_attrs["outside_temperature"],
                    "operation_busy": status_attrs["operation_busy"],
                    "error_code": status_attrs["error_code"],
                    "sub_error_code": status_attrs["sub_error_code"],
                    "discharge_temperature": status_attrs[
                        "discharge_temperature"
                    ],
                    "suction_temperature": status_attrs["suction_temperature"],
                    "freeze_protection_active": status_attrs[
                        "freeze_protection_use"
                    ],
                    "current_power": status_attrs["current_inst_power"],
                    "dhw_charge_percentage": status_attrs["dhw_charge_per"],
                    "wifi_rssi": status_attrs["wifi_rssi"],
                    # Component status (from efficient batch get)
                    "compressor_running": status_attrs["comp_use"],
                    "upper_element_on": status_attrs["heat_upper_use"],
                    "lower_element_on": status_attrs["heat_lower_use"],
                    # Raw values for diagnostics
                    "operation_mode_raw": str(operation_mode)
                    if operation_mode is not None
                    else None,
                    "dhw_operation_setting_raw": str(dhw_operation_setting)
                    if dhw_operation_setting is not None
                    else None,
                }
            )
        except AttributeError, TypeError:
            pass

        return attrs

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature.

        The value is the display temperature -- what the user sees on the
        device and in the app -- and is passed through in that scale; the
        library converts it using the unit-system context the coordinator
        keeps in sync.
        """
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # Validation and dispatch are held together against a unit-system
        # transition. Checking a flag first would not be enough: the value is
        # converted to device units inside the library, after the publish
        # below has yielded, so a transition starting in between would encode
        # a setpoint validated in one scale using the other.
        async with self.coordinator.unit_transition_guard(
            "set the temperature"
        ):
            # Validate temperature range. Raising rather than returning:
            # silently dropping the call left the user looking at a setpoint
            # they thought they had changed, with the reason only in the log.
            #
            # The UI shows the limits rounded to the display precision, so a
            # device maximum of 149.9 degF is offered as 150. Values within
            # that rounding are accepted and clamped to the device's range,
            # which the library checks exactly; the clamped value encodes to
            # the same half-degree step the device stores.
            tolerance = self.precision / 2
            if not (
                self.min_temp - tolerance
                <= temperature
                <= self.max_temp + tolerance
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="temperature_out_of_range",
                    translation_placeholders={
                        "temperature": str(temperature),
                        "min_temp": str(self.min_temp),
                        "max_temp": str(self.max_temp),
                    },
                )

            await self._async_dispatch_command(
                "set_temperature",
                temperature=min(
                    max(float(temperature), self.min_temp), self.max_temp
                ),
            )

        # Refreshing is deliberately outside the guard: it needs no unit
        # context of its own, and holding the lock across a coordinator
        # refresh would stall a pending transition for no reason.
        await self.coordinator.async_request_refresh()

    @override
    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set new operation mode using DHW mode control."""
        dhw_mode_value = HA_TO_DHW_MODE.get(operation_mode)

        if dhw_mode_value is None:
            if operation_mode.lower() == STATE_OFF:
                await self.async_turn_off()
                return

            _LOGGER.error("Invalid operation mode: %s", operation_mode)
            return

        _LOGGER.debug(
            "Setting DHW mode to %s (value: %d)", operation_mode, dhw_mode_value
        )

        await self._async_send_command("set_dhw_mode", mode=dhw_mode_value)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the water heater on by setting it to energy saver mode."""
        await self.async_set_operation_mode(STATE_ECO)

    @override
    async def async_turn_away_mode_on(self) -> None:
        """Turn away mode on by setting vacation mode for 1 day.

        For a custom duration use the nwp500.set_vacation_days service instead.
        """
        previous_mode = self._pre_vacation_mode
        self._pre_vacation_mode = self.current_operation
        try:
            await self._async_dispatch_command("set_vacation_days", days=1)
        except HomeAssistantError:
            # The device never entered vacation mode, so the mode saved by
            # an earlier request is still the one to restore.
            self._pre_vacation_mode = previous_mode
            raise
        await self.coordinator.async_request_refresh()

    @override
    async def async_turn_away_mode_off(self) -> None:
        """Turn away mode off by restoring the pre-vacation operation mode."""
        restore_mode = self._pre_vacation_mode or STATE_ECO

        if (
            restore_mode not in HA_TO_DHW_MODE
            and restore_mode.lower() != STATE_OFF
        ):
            _LOGGER.warning(
                "Invalid pre-vacation operation mode '%s'; falling back to %s",
                restore_mode,
                STATE_ECO,
            )
            restore_mode = STATE_ECO

        await self.async_set_operation_mode(restore_mode)
        self._pre_vacation_mode = None

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the water heater off by setting to power off mode."""
        # Use DHW mode 6 (POWER_OFF) instead of the uncertain set_power method
        # This maps to the "off" operation mode in our DHW_MODE_TO_HA mapping
        await self._async_send_command(
            "set_dhw_mode", mode=DhwOperationSetting.POWER_OFF
        )
