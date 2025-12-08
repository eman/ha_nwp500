"""MQTT Manager for Navien NWP500 integration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from awscrt.exceptions import AwsCrtError

from .const import DeviceFeatureEvent, DeviceStatusEvent

if TYPE_CHECKING:
    from nwp500 import (  # type: ignore[attr-defined]
        Device,
        DeviceFeature,
        DeviceStatus,
        NavienAuthClient,
        NavienMqttClient,
    )

_LOGGER = logging.getLogger(__name__)


def get_aws_error_name(exception: Any) -> str:
    """Extract the name from an AwsCrtError safely."""
    if isinstance(exception, AwsCrtError):
        return str(getattr(exception, "name", ""))
    return ""


class NWP500MqttManager:
    """Class to manage MQTT connection and events."""

    def __init__(
        self,
        hass_loop: asyncio.AbstractEventLoop,
        auth_client: NavienAuthClient,
        on_status_update: Callable[[str, DeviceStatus], None],
        on_feature_update: Callable[[str, DeviceFeature], None],
    ) -> None:
        """Initialize the MQTT manager."""
        self.loop = hass_loop
        self.auth_client = auth_client
        self.mqtt_client: NavienMqttClient | None = None
        self._on_status_update_callback = on_status_update
        self._on_feature_update_callback = on_feature_update

        # Connection tracking
        self.connected_since: float | None = None
        self.reconnection_in_progress: bool = False
        self.consecutive_timeouts: int = 0

    @property
    def is_connected(self) -> bool:
        """Return True if MQTT is connected."""
        return self.mqtt_client is not None and self.mqtt_client.is_connected

    async def setup(self) -> bool:
        """Set up the MQTT client."""
        # Ensure any existing client is fully disconnected and reset
        if self.mqtt_client:
            await self.disconnect()

        try:
            from nwp500 import NavienMqttClient  # type: ignore[attr-defined]

            self.mqtt_client = NavienMqttClient(self.auth_client)

            # Set up event listeners
            if self.mqtt_client:
                self.mqtt_client.on(
                    "device_status_update", self._on_device_status_event
                )
                self.mqtt_client.on(
                    "device_feature_update", self._on_device_feature_event
                )
                self.mqtt_client.on("connection_lost", self._on_connection_lost)
                self.mqtt_client.on(
                    "connection_restored", self._on_connection_restored
                )
                self.mqtt_client.on(
                    "reconnection_failed", self._on_reconnection_failed
                )

            return await self.connect()

        except ImportError:
            _LOGGER.error("nwp500-python library not found")
            return False
        except Exception as err:
            _LOGGER.error("Failed to setup MQTT client: %s", err)
            return False

    async def connect(self) -> bool:
        """Connect to MQTT broker."""
        if not self.mqtt_client:
            return False

        try:
            connected = await self.mqtt_client.connect()
            if connected:
                self.connected_since = time.time()
                _LOGGER.info(
                    "MQTT connected successfully at %.3f", self.connected_since
                )
            else:
                _LOGGER.warning("MQTT connection failed")
            return bool(connected)
        except Exception as err:
            _LOGGER.warning("MQTT connection failed: %s", err)
            return False

    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self.mqtt_client:
            try:
                # Remove listeners
                self.mqtt_client.off(
                    "device_status_update", self._on_device_status_event
                )
                self.mqtt_client.off(
                    "device_feature_update", self._on_device_feature_event
                )
                self.mqtt_client.off(
                    "connection_lost", self._on_connection_lost
                )
                self.mqtt_client.off(
                    "connection_restored", self._on_connection_restored
                )
                self.mqtt_client.off(
                    "reconnection_failed", self._on_reconnection_failed
                )

                await self.mqtt_client.stop_all_periodic_tasks()
                await self.mqtt_client.disconnect()
            except Exception as err:
                _LOGGER.debug("Error disconnecting MQTT client: %s", err)
            finally:
                self.mqtt_client = None
                self.connected_since = None

    async def subscribe_device(self, device: Device) -> None:
        """Subscribe to device updates."""
        if not self.mqtt_client:
            return

        try:
            await self.mqtt_client.subscribe_device_status(
                device,
                lambda status: self._on_device_status_update_direct(
                    device, status
                ),
            )
            await self.mqtt_client.subscribe_device_feature(
                device,
                lambda feature: self._on_device_feature_update_direct(
                    device, feature
                ),
            )
        except Exception as err:
            _LOGGER.warning(
                "Failed to subscribe to device %s: %s",
                device.device_info.mac_address,
                err,
            )

    async def start_periodic_requests(self, device: Device) -> None:
        """Start periodic status requests."""
        if not self.mqtt_client:
            return

        try:
            # Status every 5 mins
            await self.mqtt_client.start_periodic_device_status_requests(
                device, 300.0
            )
            # Info every 30 mins
            await self.mqtt_client.start_periodic_device_info_requests(
                device, 1800.0
            )

            # Immediate info request
            try:
                await self.mqtt_client.request_device_info(device)
            except Exception as err:
                _LOGGER.warning("Failed immediate info request: %s", err)

        except Exception as err:
            _LOGGER.warning(
                "Failed to start periodic requests for %s: %s",
                device.device_info.mac_address,
                err,
            )

    async def request_status(self, device: Device) -> None:
        """Request immediate status update."""
        if not self.mqtt_client:
            return

        try:
            await self.mqtt_client.request_device_status(device)
            self.consecutive_timeouts = 0
        except Exception as err:
            self.consecutive_timeouts += 1
            self._handle_aws_error(err, "status request")

    async def request_device_info(self, device: Device) -> None:
        """Request device info."""
        if not self.mqtt_client:
            return

        try:
            await self.mqtt_client.request_device_info(device)
        except Exception as err:
            self._handle_aws_error(err, "device info request")

    async def send_command(
        self, device: Device, command: str, **kwargs: Any
    ) -> bool:
        """Send a control command."""
        if not self.mqtt_client:
            return False

        try:
            if command == "set_power":
                await self.mqtt_client.set_power(
                    device, kwargs.get("power_on", True)
                )
            elif command == "set_temperature":
                temp = kwargs.get("temperature")
                if temp:
                    await self.mqtt_client.set_dhw_temperature(
                        device, float(temp)
                    )
            elif command == "set_dhw_mode":
                mode = kwargs.get("mode")
                if mode:
                    await self.mqtt_client.set_dhw_mode(device, int(mode))
            elif command == "set_tou_enabled":
                enabled = kwargs.get("enabled", True)
                await self.mqtt_client.set_tou_enabled(device, enabled)
            elif command == "enable_anti_legionella":
                period_days = kwargs.get("period_days", 14)
                await self.mqtt_client.enable_anti_legionella(device, period_days)
            elif command == "disable_anti_legionella":
                await self.mqtt_client.disable_anti_legionella(device)
                enabled = kwargs.get("enabled", False)
                await self.mqtt_client.set_tou_enabled(device, enabled)
            elif command == "update_reservations":
                reservations = kwargs.get("reservations", [])
                enabled = kwargs.get("enabled", True)
                await self.mqtt_client.update_reservations(
                    device, reservations, enabled=enabled
                )
            elif command == "request_reservations":
                await self.mqtt_client.request_reservations(device)
            else:
                _LOGGER.error("Unknown command: %s", command)
                return False

            # Request update after command
            try:
                await self.mqtt_client.request_device_status(device)
            except Exception as err:
                self._handle_aws_error(err, "post-command status request")

            return True

        except Exception as err:
            return self._handle_aws_error(err, f"command {command}")

    async def force_reconnect(self, devices: list[Device]) -> bool:
        """Force reconnection logic."""
        if self.reconnection_in_progress:
            return False

        self.reconnection_in_progress = True
        try:
            _LOGGER.warning("Forcing MQTT reconnection...")

            # Full teardown
            await self.disconnect()

            # Wait a moment before reconnecting
            await asyncio.sleep(2.0)

            # Re-initialize and connect
            if await self.setup():
                _LOGGER.info("Reconnection successful")
                self.consecutive_timeouts = 0

                # Re-subscribe to all devices
                for device in devices:
                    await self.subscribe_device(device)
                return True

            return False

        except Exception as err:
            _LOGGER.error("Error during forced reconnect: %s", err)
            return False
        finally:
            self.reconnection_in_progress = False

    def _handle_aws_error(self, err: Exception, context: str) -> bool:
        """Handle AWS CRT errors gracefully.

        Returns:
            bool: True if error was handled gracefully (e.g. queued), False otherwise.
        """
        if isinstance(err, AwsCrtError):
            error_name = get_aws_error_name(err)
            if error_name == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION":
                _LOGGER.debug(
                    "Operation '%s' queued due to reconnection", context
                )
                return True
        _LOGGER.error("Error during %s: %s", context, err)
        return False

    # Event Handlers
    def _on_device_status_event(self, event_data: DeviceStatusEvent) -> None:
        """Handle status event from emitter."""
        try:
            status = event_data.get("status")
            device = event_data.get("device")

            if status and device:
                mac = device.device_info.mac_address
                self._on_status_update_callback(mac, status)
        except Exception as err:
            _LOGGER.error("Error handling status event: %s", err)

    def _on_device_feature_event(self, event_data: DeviceFeatureEvent) -> None:
        """Handle feature event from emitter."""
        try:
            feature = event_data.get("feature")
            device = event_data.get("device")

            if feature and device:
                mac = device.device_info.mac_address
                self._on_feature_update_callback(mac, feature)
        except Exception as err:
            _LOGGER.error("Error handling feature event: %s", err)

    def _on_device_status_update_direct(
        self, device: Device, status: DeviceStatus
    ) -> None:
        """Handle direct MQTT status update."""
        try:
            mac = device.device_info.mac_address
            self._on_status_update_callback(mac, status)
        except Exception as err:
            _LOGGER.error("Error handling direct status update: %s", err)

    def _on_device_feature_update_direct(
        self, device: Device, feature: DeviceFeature
    ) -> None:
        """Handle direct MQTT feature update."""
        try:
            mac = device.device_info.mac_address
            self._on_feature_update_callback(mac, feature)
        except Exception as err:
            _LOGGER.error("Error handling direct feature update: %s", err)

    def _on_connection_lost(self, event_data: dict[str, Any]) -> None:
        self.connected_since = None
        _LOGGER.error("MQTT connection lost: %s", event_data)

    def _on_connection_restored(self, event_data: dict[str, Any]) -> None:
        self.connected_since = time.time()
        _LOGGER.info("MQTT connection restored: %s", event_data)

    def _on_reconnection_failed(self, event_data: dict[str, Any] | int) -> None:
        attempt = (
            event_data.get("attempt_count", 0)
            if isinstance(event_data, dict)
            else event_data
        )
        _LOGGER.error(
            "MQTT reconnection failed (attempt %d). Resetting...", attempt
        )
        if self.mqtt_client:
            asyncio.run_coroutine_threadsafe(
                self.mqtt_client.reset_reconnect(), self.loop
            )
