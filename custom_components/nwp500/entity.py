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
        device,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.mac_address = mac_address
        self.device = device
        self._last_feature_update = None
        
        # Build device info with available information
        self._attr_device_info = self._build_device_info()

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
        device_name = device_info.name
        if hasattr(self.device, 'location') and self.device.location:
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
            serial_number = getattr(device_feature, 'controllerSerialNumber', None)
            
            # Build comprehensive version string showing all firmware versions
            controller_version = getattr(device_feature, 'controllerSwVersion', None)
            panel_version = getattr(device_feature, 'panelSwVersion', None)
            wifi_version = getattr(device_feature, 'wifiSwVersion', None)
            
            version_parts = []
            if controller_version:
                version_parts.append(f"Controller: {controller_version}")
            if panel_version:
                version_parts.append(f"Panel: {panel_version}")
            if wifi_version:
                version_parts.append(f"WiFi: {wifi_version}")
            
            if version_parts:
                sw_version = " | ".join(version_parts)
        
        # Build hardware version based on device type and connection status
        hw_version_parts = [f"Type {self.device.device_info.device_type}"]
        
        # Check connection status
        if hasattr(self.device.device_info, 'connected') and self.device.device_info.connected is not None:
            connection_status = "Connected" if self.device.device_info.connected else "Disconnected"
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
    def device_info(self) -> DeviceInfo:
        """Return device info, updating if new feature data is available."""
        # Check if device features have been updated since last rebuild
        current_feature = self.coordinator.device_features.get(self.mac_address)
        
        # Only rebuild if features have changed or this is the first access
        feature_changed = (
            current_feature != self._last_feature_update or 
            self._attr_device_info is None
        )
        
        if feature_changed:
            # Rebuild device info with current feature data
            self._attr_device_info = self._build_device_info()
            # Update tracking to prevent unnecessary rebuilds
            self._last_feature_update = current_feature
        
        return self._attr_device_info

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

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.device_data is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {}
        
        if self.device_data:
            device_info = self.device.device_info
            attrs.update({
                "home_seq": device_info.home_seq,
                "device_type": device_info.device_type,
                "connected": device_info.connected,
            })
            
            # Add location info if available
            if hasattr(self.device, 'location') and self.device.location:
                location = self.device.location
                if location.city:
                    attrs["city"] = location.city
                if location.state:
                    attrs["state"] = location.state
            
            # Add device feature info if available (technical details not in device info)
            device_feature = self.coordinator.device_features.get(self.mac_address)
            if device_feature:
                # Keep additional technical information that doesn't belong in device info
                attrs.update({
                    "controller_sw_version": getattr(device_feature, 'controllerSwVersion', None),
                    "panel_sw_version": getattr(device_feature, 'panelSwVersion', None), 
                    "wifi_sw_version": getattr(device_feature, 'wifiSwVersion', None),
                })
        
        return attrs