"""Base entity class for Navien NWP500 integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class NWP500Entity(CoordinatorEntity[NWP500DataUpdateCoordinator]):
    """Base class for NWP500 entities."""

    def __init__(
        self,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Any,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.mac_address = mac_address
        self.device = device
        self._last_feature_update = None

        # Build device info with available information
        self._attr_device_info = self._build_device_info()

        # Set initial attribute states
        self._update_attrs()

    def _update_attrs(self) -> None:
        """Update dynamic attributes based on current data."""
        # Update extra state attributes
        self._attr_extra_state_attributes = self._build_extra_state_attributes()

        # Update device info if features changed
        self._update_device_info()

    def _update_device_info(self) -> None:
        """Update device info if feature data has changed."""
        current_feature = self.coordinator.device_features.get(self.mac_address)

        feature_changed = (
            current_feature != self._last_feature_update
            or self._attr_device_info is None
        )

        if feature_changed:
            self._attr_device_info = self._build_device_info()
            self._last_feature_update = current_feature

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attrs()
        super()._handle_coordinator_update()

    @property
    def _status(self) -> Any | None:
        """Get device status with minimal overhead.

        This property provides a cached, efficient way to access device status
        without repeating null checks throughout entity code.

        Returns:
            Device status object or None if unavailable
        """
        if not self.device_data:
            return None
        return self.device_data.get("status")

    def _get_status_attrs(self, *attrs: str) -> dict[str, Any]:
        """Efficiently get multiple status attributes at once.

        This method leverages Python 3.13's optimized dictionary operations
        to reduce overhead from repetitive getattr() calls.

        Args:
            *attrs: Variable number of attribute names to fetch

        Returns:
            Dictionary mapping attribute names to their values (or None)
        """
        if not (status := self._status):
            return dict.fromkeys(attrs)
        return {attr: getattr(status, attr, None) for attr in attrs}

    def _build_device_info(self) -> DeviceInfo:
        """Build device info with all available information."""
        # Use device name from library
        device_name = self.device.device_info.device_name or "Navien NWP500"

        # Get device feature info for detailed information
        serial_number = None
        sw_version = None
        hw_version = None
        model_name = "NWP500"
        configuration_url = None
        suggested_area = "Utility Room"
        
        device_feature = self.coordinator.device_features.get(self.mac_address)
        
        _LOGGER.info(
            "Building device info for %s - feature_available=%s",
            self.mac_address,
            device_feature is not None,
        )
        
        if device_feature:
            # Serial number from controller
            serial_number = getattr(
                device_feature, "controller_serial_number", None
            )
            
            # Firmware versions
            controller_fw = getattr(
                device_feature, "controller_sw_version", None
            )
            wifi_fw = getattr(device_feature, "wifi_sw_version", None)
            
            _LOGGER.info(
                "Device feature data: controller_fw=%s wifi_fw=%s serial=%s",
                controller_fw,
                wifi_fw,
                serial_number,
            )
            
            # Build software version: "Controller.WiFi" format
            if controller_fw and wifi_fw:
                sw_version = f"{controller_fw}.{wifi_fw}"
            elif controller_fw:
                sw_version = controller_fw
            
            # Get volume code
            volume_code = getattr(device_feature, "volume_code", None)
            
            # Hardware version: tank volume from library
            hw_version = None
            if volume_code is not None:
                try:
                    from nwp500.enums import VOLUME_CODE_TEXT
                    hw_version = VOLUME_CODE_TEXT.get(volume_code)

                    # Update model name with capacity if available
                    if hw_version:
                        if "50" in hw_version:
                            model_name = "NWP500-50G"
                        elif "65" in hw_version:
                            model_name = "NWP500-65G"
                        elif "80" in hw_version:
                            model_name = "NWP500-80G"
                except (ImportError, AttributeError, KeyError):
                    pass
            
            _LOGGER.info(
                "Device capacity: volume_code=%s", volume_code
            )
            
            _LOGGER.info(
                "Final device info: model=%s sw_version=%s hw_version=%s serial=%s",
                model_name,
                sw_version,
                hw_version,
                serial_number,
            )

        # Configuration URL for Navien Smart Control app
        configuration_url = f"https://app.naviensmartcontrol.com/device/{self.mac_address}"
        
        # Suggested area from location if available
        if hasattr(self.device, "location") and self.device.location:
            location = self.device.location
            if location.city:
                suggested_area = location.city

        # Create DeviceInfo
        return DeviceInfo(
            identifiers={(DOMAIN, self.mac_address)},
            name=device_name,
            manufacturer="Navien",
            model=model_name,
            serial_number=serial_number,
            hw_version=hw_version,
            sw_version=sw_version,
            configuration_url=configuration_url,
            suggested_area=suggested_area,
        )

    @property
    def device_data(self) -> dict[str, Any] | None:
        """Return the device data."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.mac_address)

    @property
    def device_name(self) -> str:
        """Return the device name."""
        return self.device.device_info.device_name or "NWP500"

    def _build_extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {}

        if self.device_data:
            device_info = self.device.device_info
            attrs.update(
                {
                    "home_seq": device_info.home_seq,
                    "device_type": device_info.device_type,
                    "connected": device_info.connected,
                }
            )

            # Add location info if available
            if hasattr(self.device, "location") and self.device.location:
                location = self.device.location
                if location.address:
                    attrs["address"] = location.address
                if location.city:
                    attrs["city"] = location.city
                if location.state:
                    attrs["state"] = location.state
                if location.latitude:
                    attrs["latitude"] = location.latitude
                if location.longitude:
                    attrs["longitude"] = location.longitude

            # Add device feature info if available (technical details
            # not in device info)
            device_feature = self.coordinator.device_features.get(
                self.mac_address
            )
            if device_feature:
                # Individual firmware versions for technical analysis
                controller_version = getattr(
                    device_feature, "controller_sw_version", None
                )
                controller_code = getattr(
                    device_feature, "controller_sw_code", None
                )
                panel_version = getattr(
                    device_feature, "panel_sw_version", None
                )
                panel_code = getattr(
                    device_feature, "panel_sw_code", None
                )
                wifi_version = getattr(device_feature, "wifi_sw_version", None)
                wifi_code = getattr(device_feature, "wifi_sw_code", None)
                recirc_version = getattr(device_feature, "recirc_sw_version", None)

                attrs.update(
                    {
                        "controller_sw_version": controller_version,
                        "controller_sw_code": controller_code,
                        "panel_sw_version": panel_version,
                        "panel_sw_code": panel_code,
                        "wifi_sw_version": wifi_version,
                        "wifi_sw_code": wifi_code,
                    }
                )

                if recirc_version:
                    attrs["recirc_sw_version"] = recirc_version

                # Capabilities
                attrs.update(
                    {
                        "hpwh_use": getattr(device_feature, "hpwh_use", None),
                        "recirculation_use": getattr(device_feature, "recirculation_use", None),
                        "dr_setting_use": getattr(device_feature, "dr_setting_use", None),
                        "anti_legionella_setting_use": getattr(device_feature, "anti_legionella_setting_use", None),
                        "freeze_protection_use": getattr(device_feature, "freeze_protection_use", None),
                        "smart_diagnostic_use": getattr(device_feature, "smart_diagnostic_use", None),
                    }
                )

                # Operating limits
                attrs.update(
                    {
                        "dhw_temperature_min": getattr(device_feature, "dhw_temperature_min", None),
                        "dhw_temperature_max": getattr(device_feature, "dhw_temperature_max", None),
                        "temperature_type": getattr(device_feature, "temperature_type", None),
                        "dhw_temperature_setting_use": getattr(device_feature, "dhw_temperature_setting_use", None),
                    }
                )

                # Installation info
                volume_code_value = getattr(device_feature, "volume_code", None)
                attrs.update(
                    {
                        "install_type": getattr(device_feature, "install_type", None),
                        "country_code": getattr(device_feature, "country_code", None),
                    }
                )
                
                # Add volume_code text from library
                if volume_code_value is not None:
                    try:
                        from nwp500.enums import VOLUME_CODE_TEXT
                        volume_code_text = VOLUME_CODE_TEXT.get(volume_code_value)
                        if volume_code_text:
                            attrs["volume_code"] = volume_code_text
                    except (ImportError, AttributeError, KeyError):
                        pass

        return attrs
