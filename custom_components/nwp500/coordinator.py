"""DataUpdateCoordinator for the Navien NWP500 integration."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class NWP500DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the NWP500 API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self.auth_client = None
        self.api_client = None
        self.mqtt_client = None
        self.devices = []
        self.device_features = {}
        self._periodic_task = None
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        if not self.auth_client:
            await self._setup_clients()
        
        try:
            # Initialize device data from existing data or create new entries
            device_data = {}
            for device in self.devices:
                mac_address = device.device_info.mac_address
                
                # Keep existing data if available, or initialize with device info only
                if self.data and mac_address in self.data:
                    device_data[mac_address] = self.data[mac_address].copy()
                else:
                    device_data[mac_address] = {
                        "device": device,
                        "status": None,  # Will be updated via MQTT
                        "last_update": None,
                    }
                
                # Request fresh status via MQTT (async, will update via callback)
                if self.mqtt_client and self.mqtt_client.is_connected:
                    try:
                        await self.mqtt_client.request_device_status(device)
                        _LOGGER.debug("Requested status update for device %s", mac_address)
                    except Exception as err:
                        _LOGGER.warning("Failed to request status for device %s: %s", mac_address, err)
            
            return device_data
            
        except Exception as err:
            _LOGGER.error("Error fetching data: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def _setup_clients(self) -> None:
        """Set up the API and MQTT clients."""
        try:
            from nwp500 import NavienAuthClient, NavienAPIClient, NavienMqttClient
        except ImportError as err:
            _LOGGER.error(
                "nwp500-python library not installed. Please install with: pip install nwp500-python==1.1.2 awsiotsdk>=1.20.0"
            )
            raise UpdateFailed(f"nwp500-python library not available: {err}") from err
        
        email = self.entry.data[CONF_EMAIL]
        password = self.entry.data[CONF_PASSWORD]
        
        try:
            # Setup authentication client
            self.auth_client = NavienAuthClient(email, password)
            await self.auth_client.__aenter__()  # Authenticate
            
            # Setup API client
            self.api_client = NavienAPIClient(auth_client=self.auth_client)
            
            # Get devices
            self.devices = await self.api_client.list_devices()
            if not self.devices:
                raise UpdateFailed("No devices found")
            
            _LOGGER.info("Found %d devices", len(self.devices))
            
            # Setup MQTT client for real-time updates with event emitter
            self.mqtt_client = NavienMqttClient(self.auth_client)
            
            # Set up event listeners using the event emitter functionality
            self.mqtt_client.on('device_status_update', self._on_device_status_event)
            self.mqtt_client.on('device_feature_update', self._on_device_feature_event)
            self.mqtt_client.on('connection_lost', self._on_connection_lost)
            self.mqtt_client.on('connection_restored', self._on_connection_restored)
            
            # Connect to MQTT - this may generate blocking I/O warnings from AWS IoT SDK
            # but it's unavoidable since the underlying library does blocking operations
            try:
                connected = await self.mqtt_client.connect()
            except Exception as err:
                _LOGGER.warning("MQTT connection failed: %s", err)
                connected = False
            
            if not connected:
                _LOGGER.warning("Failed to connect to MQTT, will continue with API-only mode")
            else:
                # Subscribe to device status updates
                for device in self.devices:
                    try:
                        await self.mqtt_client.subscribe_device_status(device, self._on_device_status_update)
                        # Also subscribe to device feature updates for firmware/serial info
                        await self.mqtt_client.subscribe_device_feature(device, self._on_device_feature_update)
                    except Exception as err:
                        _LOGGER.warning("Failed to subscribe to device %s: %s", device.device_info.mac_address, err)
                
                # Start periodic status requests via MQTT (every 5 minutes)
                for device in self.devices:
                    try:
                        await self.mqtt_client.start_periodic_device_status_requests(device, 300.0)
                        # Also request device info periodically (every 30 minutes for firmware info)
                        await self.mqtt_client.start_periodic_device_info_requests(device, 1800.0)
                    except Exception as err:
                        _LOGGER.warning("Failed to start periodic requests for device %s: %s", device.device_info.mac_address, err)
            
            _LOGGER.info("Successfully connected to Navien cloud service with %d devices", len(self.devices))
            
        except Exception as err:
            _LOGGER.error("Failed to setup clients: %s", err)
            await self.async_shutdown()
            raise UpdateFailed(f"Failed to connect to Navien service: {err}") from err

    def _on_device_status_event(self, event_data) -> None:
        """Handle device status event from event emitter."""
        _LOGGER.debug("Received device status event: %s", event_data)
        
        try:
            # Extract status from event data
            status = event_data.get('status')
            device = event_data.get('device')
            
            if status and device:
                mac_address = device.device_info.mac_address
                if self.data and mac_address in self.data:
                    self.data[mac_address]["status"] = status
                    self.data[mac_address]["last_update"] = time.time()
                    
                    # Schedule update for all listeners using thread-safe method
                    self.hass.loop.call_soon_threadsafe(self.async_update_listeners)
        except Exception as err:
            _LOGGER.error("Error handling device status event: %s", err)

    def _on_device_feature_event(self, event_data) -> None:
        """Handle device feature event from event emitter."""
        _LOGGER.debug("Received device feature event: %s", event_data)
        
        try:
            feature = event_data.get('feature')
            device = event_data.get('device')
            
            if feature and device:
                mac_address = device.device_info.mac_address
                self.device_features[mac_address] = feature
        except Exception as err:
            _LOGGER.error("Error handling device feature event: %s", err)

    def _on_connection_lost(self, event_data) -> None:
        """Handle MQTT connection lost event."""
        _LOGGER.warning("MQTT connection lost: %s", event_data)

    def _on_connection_restored(self, event_data) -> None:
        """Handle MQTT connection restored event."""
        _LOGGER.info("MQTT connection restored: %s", event_data)

    def _on_device_status_update(self, status) -> None:
        """Handle device status update from MQTT (legacy callback)."""
        # START DIAGNOSTIC CODE
        _LOGGER.error("NWP500 Diagnostic Data: %s", status)
        # END DIAGNOSTIC CODE
        try:
            # Find the device by checking the status data
            # The status should contain mac_address or we can match by other means
            _LOGGER.debug("Received device status update: %s", status)
            
            # Use thread-safe method to schedule update on the main event loop
            if hasattr(status, 'device') and hasattr(status.device, 'device_info'):
                mac_address = status.device.device_info.mac_address
            else:
                # If we can't identify the device, update all devices for now
                # This might need refinement based on the actual status structure
                for device in self.devices:
                    mac_address = device.device_info.mac_address
                    if self.data and mac_address in self.data:
                        self.data[mac_address]["status"] = status
                        # Use time.time() instead of loop.time() since we're in different thread
                        self.data[mac_address]["last_update"] = time.time()
                
                # Schedule update for all listeners using thread-safe method
                self.hass.loop.call_soon_threadsafe(self.async_update_listeners)
                return
            
            if self.data and mac_address in self.data:
                self.data[mac_address]["status"] = status
                # Use time.time() instead of loop.time() since we're in different thread
                self.data[mac_address]["last_update"] = time.time()
                
                # Schedule update for all listeners using thread-safe method
                self.hass.loop.call_soon_threadsafe(self.async_update_listeners)
                
        except Exception as err:
            _LOGGER.error("Error handling device status update: %s", err)

    def _on_device_feature_update(self, feature) -> None:
        """Handle device feature update from MQTT (legacy callback)."""
        try:
            _LOGGER.debug("Received device feature update: %s", feature)
            
            # Store the device feature data for use in device info
            # We'll match it to devices by checking if it matches any of our devices
            for device in self.devices:
                mac_address = device.device_info.mac_address
                # Store the feature data - it will be used by entities for device info
                self.device_features[mac_address] = feature
                break  # Assume single device for now
                
        except Exception as err:
            _LOGGER.error("Error handling device feature update: %s", err)

    async def async_control_device(self, mac_address: str, command: str, **kwargs) -> bool:
        """Send control command to device."""
        if not self.mqtt_client:
            _LOGGER.error("MQTT client not available")
            return False
        
        device = None
        for dev in self.devices:
            if dev.device_info.mac_address == mac_address:
                device = dev
                break
        
        if not device:
            _LOGGER.error("Device %s not found", mac_address)
            return False
        
        try:
            if command == "set_power":
                power_on = kwargs.get("power_on", True)
                await self.mqtt_client.set_power(device, power_on)
            elif command == "set_temperature":
                temperature = kwargs.get("temperature")
                if temperature:
                    # Use set_dhw_temperature_display which takes the display temperature directly
                    # This is more intuitive as it matches what users see on the device/app
                    await self.mqtt_client.set_dhw_temperature_display(device, int(temperature))
            elif command == "set_dhw_mode":
                mode = kwargs.get("mode")
                if mode:
                    await self.mqtt_client.set_dhw_mode(device, int(mode))
            else:
                _LOGGER.error("Unknown command: %s", command)
                return False
            
            # Request status update after command
            await self.mqtt_client.request_device_status(device)
            return True
            
        except Exception as err:
            _LOGGER.error("Failed to send command %s: %s", command, err)
            return False

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        if self.mqtt_client:
            try:
                # Remove event listeners
                self.mqtt_client.off('device_status_update', self._on_device_status_event)
                self.mqtt_client.off('device_feature_update', self._on_device_feature_event)
                self.mqtt_client.off('connection_lost', self._on_connection_lost)
                self.mqtt_client.off('connection_restored', self._on_connection_restored)
                
                self.mqtt_client.stop_all_periodic_tasks()
                self.mqtt_client.disconnect()
            except Exception as err:
                _LOGGER.debug("Error disconnecting MQTT client: %s", err)
            self.mqtt_client = None
        
        if self.auth_client:
            try:
                await self.auth_client.__aexit__(None, None, None)
            except Exception as err:
                _LOGGER.debug("Error closing auth client: %s", err)
            self.auth_client = None
        
        self.api_client = None