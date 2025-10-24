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

        This helper method reduces repetitive getattr() calls by fetching
        multiple attributes in a single operation.

        Args:
            *attrs: Variable number of attribute names to fetch

        Returns:
            Dictionary mapping attribute names to their values (or None)
        """
        if not (status := self._status):
            return {attr: None for attr in attrs}
        return {attr: getattr(status, attr, None) for attr in attrs}

    def _build_device_info(self) -> DeviceInfo:
        """Build device info with all available information."""
        # Start with base device info
        device_info = DeviceInfo(
            identifiers={(DOMAIN, self.mac_address)},
            name=self.device.device_info.device_name or "Navien NWP500",
            manufacturer="Navien",
            model="NWP500",
            connections={("mac", self.mac_address.lower())},
        )

        # Update name with location information if available
        device_name = device_info["name"]
        if hasattr(self.device, "location") and self.device.location:
            location = self.device.location
            location_parts = []
            if location.city:
                location_parts.append(location.city)
            if location.state:
                location_parts.append(location.state)
            if location_parts:
                # Create new DeviceInfo with updated name
                device_name = f"{device_name} ({', '.join(location_parts)})"

        # Get device feature info for additional details
        serial_number = None
        sw_version = None
        device_feature = self.coordinator.device_features.get(self.mac_address)
        if device_feature:
            _LOGGER.debug("Device feature available for %s", self.mac_address)
            # Get serial number
            serial_number = getattr(
                device_feature, "controllerSerialNumber", None
            )

            # Use controller firmware version as primary sw_version
            # (HA convention) This provides a concise version
            # identifier for the main device firmware
            controller_version = getattr(
                device_feature, "controllerSwVersion", None
            )
            sw_version = (
                controller_version  # Simple, clean version for HA device info
            )

        # Build hardware version based on device type and connection status
        hw_version_parts = [f"Type {self.device.device_info.device_type}"]

        # Check connection status
        if (
            hasattr(self.device.device_info, "connected")
            and self.device.device_info.connected is not None
        ):
            connection_status = (
                "Connected"
                if self.device.device_info.connected
                else "Disconnected"
            )
            hw_version_parts.append(connection_status)

        hw_version = " | ".join(hw_version_parts)

        # Create final DeviceInfo with all attributes
        final_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.mac_address)},
            name=device_name,
            manufacturer="Navien",
            model="NWP500",
            connections={("mac", self.mac_address.lower())},
            serial_number=serial_number,
            sw_version=sw_version,
            hw_version=hw_version,
            suggested_area="Utility Room",
        )

        return final_device_info

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
                if location.city:
                    attrs["city"] = location.city
                if location.state:
                    attrs["state"] = location.state

            # Add device feature info if available (technical details
            # not in device info)
            device_feature = self.coordinator.device_features.get(
                self.mac_address
            )
            if device_feature:
                # Individual firmware versions for technical analysis
                controller_version = getattr(
                    device_feature, "controllerSwVersion", None
                )
                panel_version = getattr(device_feature, "panelSwVersion", None)
                wifi_version = getattr(device_feature, "wifiSwVersion", None)

                attrs.update(
                    {
                        "controller_sw_version": controller_version,
                        "panel_sw_version": panel_version,
                        "wifi_sw_version": wifi_version,
                    }
                )

                # Add composite firmware version string for comprehensive view
                version_parts = []
                if controller_version:
                    version_parts.append(f"Controller: {controller_version}")
                if panel_version:
                    version_parts.append(f"Panel: {panel_version}")
                if wifi_version:
                    version_parts.append(f"WiFi: {wifi_version}")

                if version_parts:
                    attrs["firmware_versions"] = " | ".join(version_parts)

        return attrs
