"""MQTT Manager for Navien NWP500 integration."""

from __future__ import annotations

import asyncio
import logging
import time
import types
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from awscrt.exceptions import AwsCrtError

from .const import DeviceFeatureEvent, DeviceStatusEvent

if TYPE_CHECKING:
    from nwp500 import (  # type: ignore[attr-defined]
        Device,
        DeviceFeature,
        DeviceStatus,
        MqttDiagnosticsCollector,
        NavienAuthClient,
        NavienMqttClient,
    )

_LOGGER = logging.getLogger(__name__)

# Reconnection backoff delays (seconds): 2s, 5s, 15s, 30s, 60s cap
_RECONNECT_BACKOFF_DELAYS: list[float] = [2.0, 5.0, 15.0, 30.0, 60.0]


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
        on_reservation_update: Callable[[str, dict[str, Any]], None]
        | None = None,
        on_tou_update: Callable[[str, dict[str, Any]], None] | None = None,
        unit_system: str | None = None,
    ) -> None:
        """Initialize the MQTT manager."""
        self.loop = hass_loop
        self.auth_client = auth_client
        self.mqtt_client: NavienMqttClient | None = None
        self.diagnostics: MqttDiagnosticsCollector | None = None
        self._on_status_update_callback = on_status_update
        self._on_feature_update_callback = on_feature_update
        self._on_reservation_update_callback = on_reservation_update
        self._on_tou_update_callback = on_tou_update
        self.unit_system = unit_system

        # Connection tracking
        self.connected_since: float | None = None
        self.reconnection_in_progress: bool = False
        self.consecutive_timeouts: int = 0
        self._reconnect_attempts: int = 0
        self._last_reconnect_time: float = 0.0

        # Connection state tracking for diagnostics
        self._connection_interruptions: deque[dict[str, Any]] = deque(maxlen=20)

        # Lazily-resolved patched MQTT client class (set on first setup())
        self._patched_client_cls: type | None = None

        # Track subscribed scheduling response topics to avoid duplicates.
        # Topics are per (device_type, client_id), not per device MAC, so
        # we subscribe once and broadcast responses to all known devices.
        self._subscribed_scheduling_topics: set[str] = set()
        self._tracked_mac_addresses: set[str] = set()

    async def __aenter__(self) -> NWP500MqttManager:
        """Async context manager entry - set up MQTT connection."""
        await self.setup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit - disconnect from MQTT."""
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        """Return True if MQTT is connected."""
        return self.mqtt_client is not None and self.mqtt_client.is_connected

    @property
    def last_reconnect_time(self) -> float:
        """Return the timestamp of the last reconnection attempt."""
        return self._last_reconnect_time

    def get_connection_diagnostics(self) -> dict[str, Any]:
        """Get connection state diagnostics.

        Returns:
            Dictionary containing connection state and interruption history.
        """
        return {
            "is_connected": self.is_connected,
            "connected_since": self.connected_since,
            "consecutive_timeouts": self.consecutive_timeouts,
            "reconnection_in_progress": self.reconnection_in_progress,
            "reconnect_attempts": self._reconnect_attempts,
            "last_reconnect_time": self._last_reconnect_time,
            "connection_interruptions": list(self._connection_interruptions),
        }

    async def setup(self) -> bool:
        """Set up the MQTT client."""
        # Ensure any existing client is fully disconnected and reset
        if self.mqtt_client:
            await self.disconnect()

        try:
            from nwp500 import (  # type: ignore[attr-defined]
                MqttDiagnosticsCollector,
                NavienMqttClient,
            )

            if self._patched_client_cls is None:

                class PatchedNavienMqttClient(NavienMqttClient):
                    """Patched client to handle AWSIoT SDK callback changes."""

                    def _on_connection_resumed_internal(
                        self,
                        return_code: Any,
                        session_present: Any,
                        **kwargs: Any,
                    ) -> None:
                        """Handle connection resumed with extra kwargs."""
                        super()._on_connection_resumed_internal(
                            return_code, session_present
                        )

                self._patched_client_cls = PatchedNavienMqttClient

            # Initialize diagnostics collector
            self.diagnostics = MqttDiagnosticsCollector(
                enable_verbose_logging=False
            )

            # Token validation deferred to connect() per nwp500-python 7.3.1+
            self.mqtt_client = self._patched_client_cls(
                self.auth_client,
                unit_system=self.unit_system,  # type: ignore[arg-type]
            )

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
                self.mqtt_client.on(
                    "connection_interrupted",
                    self._on_connection_interrupted,
                )
                self.mqtt_client.on(
                    "connection_resumed",
                    self._on_connection_resumed,
                )

            return await self.connect()

        except ImportError:
            _LOGGER.error("nwp500-python library not found")
            return False
        except Exception as err:
            _LOGGER.error("Failed to setup MQTT client: %s", err)
            return False

    async def connect(self) -> bool:
        """Connect to MQTT broker, refreshing auth if needed."""
        if not self.mqtt_client:
            return False

        try:
            # Ensure auth tokens are valid before connecting
            # This handles cases where auth_client encountered network errors
            try:
                await self.auth_client.ensure_valid_token()
                _LOGGER.debug("Auth tokens validated/refreshed")
            except Exception as auth_err:
                _LOGGER.error(
                    "Failed to ensure valid auth tokens: %s", auth_err
                )
                return False

            connected = await self.mqtt_client.connect()
            if connected:
                self.connected_since = time.time()
                _LOGGER.info(
                    "MQTT connected successfully at %.3f", self.connected_since
                )
                # Record initial connection success in diagnostics
                if self.diagnostics:
                    await self.diagnostics.record_connection_success(
                        event_type="initial",
                        session_present=False,
                        return_code=0,
                    )
            else:
                _LOGGER.warning("MQTT connection failed")
                # Record connection failure in diagnostics
                if self.diagnostics:
                    await self.diagnostics.record_connection_drop(
                        error=Exception("Connection failed")
                    )
            return bool(connected)
        except Exception as err:
            _LOGGER.warning("MQTT connection failed: %s", err)
            # Record connection failure in diagnostics
            if self.diagnostics:
                await self.diagnostics.record_connection_drop(error=err)
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
                self.mqtt_client.off(
                    "connection_interrupted",
                    self._on_connection_interrupted,
                )
                self.mqtt_client.off(
                    "connection_resumed",
                    self._on_connection_resumed,
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
            # Subscribe to scheduling response topics
            await self._subscribe_scheduling_responses(device)
        except Exception as err:
            _LOGGER.warning(
                "Failed to subscribe to device %s: %s",
                device.device_info.mac_address,
                err,
            )

    async def _subscribe_scheduling_responses(self, device: Device) -> None:
        """Subscribe to reservation and TOU response topics for a device.

        Response topics are ``cmd/{device_type}/{client_id}/res/…`` — they are
        shared across all devices of the same type.  We subscribe only once per
        unique topic and broadcast incoming responses to every tracked device.
        """
        if not self.mqtt_client:
            return

        mac_address = device.device_info.mac_address
        if mac_address not in self._tracked_mac_addresses:
            self._tracked_mac_addresses.add(mac_address)

        device_type = str(device.device_info.device_type)
        client_id = self.mqtt_client.client_id

        rsv_topic = f"cmd/{device_type}/{client_id}/res/rsv/rd"
        if rsv_topic not in self._subscribed_scheduling_topics:
            await self.mqtt_client.subscribe(
                rsv_topic,
                self._on_reservation_response,
            )
            self._subscribed_scheduling_topics.add(rsv_topic)
            _LOGGER.debug(
                "Subscribed to reservation responses on %s", rsv_topic
            )

        tou_topic = f"cmd/{device_type}/{client_id}/res/tou/rd"
        if tou_topic not in self._subscribed_scheduling_topics:
            await self.mqtt_client.subscribe(
                tou_topic,
                self._on_tou_response,
            )
            self._subscribed_scheduling_topics.add(tou_topic)
            _LOGGER.debug("Subscribed to TOU responses on %s", tou_topic)

    async def start_periodic_requests(self, device: Device) -> None:
        """Start periodic status requests."""
        if not self.mqtt_client:
            return

        try:
            from nwp500 import PeriodicRequestType  # type: ignore[attr-defined]

            # Status every 5 mins
            await self.mqtt_client.start_periodic_requests(
                device, PeriodicRequestType.DEVICE_STATUS, 300.0
            )
            # Info every 30 mins
            await self.mqtt_client.start_periodic_requests(
                device, PeriodicRequestType.DEVICE_INFO, 1800.0
            )

            _LOGGER.debug(
                "Started periodic requests for %s",
                device.device_info.mac_address,
            )

        except Exception as err:
            _LOGGER.warning(
                "Failed to start periodic requests for %s: %s",
                device.device_info.mac_address,
                err,
            )

    async def request_status(self, device: Device) -> bool:
        """Request immediate status update."""
        if not self.mqtt_client:
            return False

        try:
            # Request fresh status from device
            # We use request_device_status to get a lightweight status update
            # This avoids the caching behavior of ensure_device_info_cached
            await self.mqtt_client.control.request_device_status(device)
            self.consecutive_timeouts = 0
            return True
        except Exception as err:
            self.consecutive_timeouts += 1
            return self._handle_aws_error(err, "status request")

    async def request_device_info(self, device: Device) -> None:
        """Request device info."""
        if not self.mqtt_client:
            return

        try:
            await self.mqtt_client.ensure_device_info_cached(device)
        except Exception as err:
            self._handle_aws_error(err, "device info request")

    async def send_command(
        self, device: Device, command: str, **kwargs: Any
    ) -> bool:
        """Send a control command."""
        if not self.mqtt_client:
            return False

        try:
            _LOGGER.debug("Sending command '%s' to device", command)
            match command:
                case "set_power":
                    await self.mqtt_client.control.set_power(
                        device, kwargs.get("power_on", True)
                    )
                case "set_temperature":
                    temp = kwargs.get("temperature")
                    if temp is not None:
                        await self.mqtt_client.control.set_dhw_temperature(
                            device, float(temp)
                        )
                case "set_dhw_mode":
                    mode = kwargs.get("mode")
                    if mode is not None:
                        await self.mqtt_client.control.set_dhw_mode(
                            device, int(mode)
                        )
                case "set_tou_enabled":
                    enabled = kwargs.get("enabled", True)
                    await self.mqtt_client.control.set_tou_enabled(
                        device, enabled
                    )
                case "enable_anti_legionella":
                    period_days = kwargs.get("period_days", 14)
                    await self.mqtt_client.control.enable_anti_legionella(
                        device, period_days
                    )
                case "disable_anti_legionella":
                    await self.mqtt_client.control.disable_anti_legionella(
                        device
                    )
                case "update_reservations":
                    reservations = kwargs.get("reservations", [])
                    enabled = kwargs.get("enabled", True)
                    await self.mqtt_client.control.update_reservations(
                        device, reservations, enabled=enabled
                    )
                case "request_reservations":
                    await self.mqtt_client.control.request_reservations(device)
                case "configure_tou_schedule":
                    controller_serial = kwargs.get(
                        "controller_serial_number", ""
                    )
                    periods = kwargs.get("periods", [])
                    enabled = kwargs.get("enabled", True)
                    await self.mqtt_client.control.configure_tou_schedule(
                        device,
                        controller_serial_number=controller_serial,
                        periods=periods,
                        enabled=enabled,
                    )
                case "request_tou_settings":
                    controller_serial = kwargs.get(
                        "controller_serial_number", ""
                    )
                    await self.mqtt_client.control.request_tou_settings(
                        device,
                        controller_serial_number=controller_serial,
                    )
                case "set_vacation_days":
                    days = kwargs.get("days")
                    if days is not None:
                        # Mode 5 is vacation
                        await self.mqtt_client.control.set_dhw_mode(
                            device, 5, vacation_days=int(days)
                        )
                case "enable_demand_response":
                    await self.mqtt_client.control.enable_demand_response(
                        device
                    )
                case "disable_demand_response":
                    await self.mqtt_client.control.disable_demand_response(
                        device
                    )
                case "reset_air_filter":
                    await self.mqtt_client.control.reset_air_filter(device)
                case "set_recirculation_mode":
                    mode = kwargs.get("mode")
                    if mode is None:
                        _LOGGER.error(
                            "set_recirculation_mode requires 'mode' kwarg but none was provided"
                        )
                        return False
                    await self.mqtt_client.control.set_recirculation_mode(
                        device, int(mode)
                    )
                case "trigger_recirculation":
                    await self.mqtt_client.control.trigger_recirculation_hot_button(
                        device
                    )
                case _:
                    _LOGGER.error("Unknown command: %s", command)
                    return False

            # Request update after command
            try:
                await self.mqtt_client.control.request_device_status(device)
            except Exception as err:
                self._handle_aws_error(err, "post-command status request")

            return True

        except Exception as err:
            return self._handle_aws_error(err, f"command {command}")

    async def force_reconnect(self, devices: list[Device]) -> bool:
        """Force reconnection with exponential backoff.

        Uses increasing delays between attempts: 2s, 5s, 15s, 30s, 60s (cap).
        Backoff resets on successful reconnection.
        """
        if self.reconnection_in_progress:
            return False

        self.reconnection_in_progress = True
        try:
            # Calculate backoff delay based on attempt count
            delay_index = min(
                self._reconnect_attempts, len(_RECONNECT_BACKOFF_DELAYS) - 1
            )
            backoff_delay = _RECONNECT_BACKOFF_DELAYS[delay_index]

            _LOGGER.warning(
                "Forcing MQTT reconnection (attempt %d, backoff %.0fs)...",
                self._reconnect_attempts + 1,
                backoff_delay,
            )

            # Full teardown
            await self.disconnect()

            # Wait with exponential backoff before reconnecting
            await asyncio.sleep(backoff_delay)

            self._last_reconnect_time = time.time()

            # Re-initialize and connect (connect() will refresh auth tokens)
            if await self.setup():
                _LOGGER.info(
                    "Reconnection successful after %d attempt(s)",
                    self._reconnect_attempts + 1,
                )
                self.consecutive_timeouts = 0
                self._reconnect_attempts = 0  # Reset backoff on success

                # Re-subscribe to all devices
                for device in devices:
                    await self.subscribe_device(device)
                return True

            # Failed - increment attempt counter for next backoff
            self._reconnect_attempts += 1
            _LOGGER.warning(
                "Reconnection failed (attempt %d). Next backoff: %.0fs",
                self._reconnect_attempts,
                _RECONNECT_BACKOFF_DELAYS[
                    min(
                        self._reconnect_attempts,
                        len(_RECONNECT_BACKOFF_DELAYS) - 1,
                    )
                ],
            )
            return False

        except Exception as err:
            self._reconnect_attempts += 1
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

    def _on_reservation_response(
        self, topic: str, message: dict[str, Any]
    ) -> None:
        """Handle reservation response from device.

        The response topic is shared across devices, so we broadcast to all
        tracked MAC addresses.  The coordinator stores per-MAC — the last
        writer wins, which is correct for single-device setups and acceptable
        for multi-device until the protocol includes device identification.
        """
        try:
            response = message.get("response", {})
            _LOGGER.debug("Received reservation response on %s", topic)
            if self._on_reservation_update_callback:
                for mac in self._tracked_mac_addresses:
                    self._on_reservation_update_callback(mac, response)
        except Exception as err:
            _LOGGER.error("Error handling reservation response: %s", err)

    def _on_tou_response(self, topic: str, message: dict[str, Any]) -> None:
        """Handle TOU response from device.

        See _on_reservation_response for broadcast rationale.
        """
        try:
            response = message.get("response", {})
            _LOGGER.debug("Received TOU response on %s", topic)
            if self._on_tou_update_callback:
                for mac in self._tracked_mac_addresses:
                    self._on_tou_update_callback(mac, response)
        except Exception as err:
            _LOGGER.error("Error handling TOU response: %s", err)

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
            future = asyncio.run_coroutine_threadsafe(
                self.mqtt_client.reset_reconnect(), self.loop
            )
            future.add_done_callback(
                lambda f: _LOGGER.error("reset_reconnect error: %s", f.exception())
                if not f.cancelled() and f.exception()
                else None
            )

    def _on_connection_interrupted(self, error: Exception) -> None:
        """Handle connection interruption event for diagnostics."""
        interruption_event: dict[str, Any] = {
            "timestamp": time.time(),
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
        self._connection_interruptions.append(interruption_event)

        if self.diagnostics:
            future = asyncio.run_coroutine_threadsafe(
                self.diagnostics.record_connection_drop(error=error),
                self.loop,
            )
            future.add_done_callback(
                lambda f: _LOGGER.debug("record_connection_drop error: %s", f.exception())
                if not f.cancelled() and f.exception()
                else None
            )

    def _on_connection_resumed(
        self, return_code: int, session_present: bool
    ) -> None:
        """Handle connection resume event for diagnostics."""
        if self.diagnostics:
            future = asyncio.run_coroutine_threadsafe(
                self.diagnostics.record_connection_success(
                    event_type="resumed",
                    session_present=session_present,
                    return_code=return_code,
                ),
                self.loop,
            )
            future.add_done_callback(
                lambda f: _LOGGER.debug("record_connection_success error: %s", f.exception())
                if not f.cancelled() and f.exception()
                else None
            )
