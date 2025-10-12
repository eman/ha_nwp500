"""Base entity class for Navien NWP500 integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NWP500DataUpdateCoordinator


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
        
        # Get device feature info for serial number and firmware version
        # For now, basic device info without additional API calls
        device_feature = coordinator.device_features.get(mac_address)
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac_address)},
            name=device.device_info.device_name or "Navien NWP500",
            manufacturer="Navien",
            model="NWP500",
            # Device info will be populated via MQTT feature updates
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
                "mac_address": device_info.mac_address,
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
            
            # Add device feature info if available
            device_feature = self.coordinator.device_features.get(self.mac_address)
            if device_feature:
                attrs.update({
                    "controller_sw_version": getattr(device_feature, 'controllerSwVersion', None),
                    "panel_sw_version": getattr(device_feature, 'panelSwVersion', None), 
                    "wifi_sw_version": getattr(device_feature, 'wifiSwVersion', None),
                    "controller_serial_number": getattr(device_feature, 'controllerSerialNumber', None),
                })
        
        return attrs