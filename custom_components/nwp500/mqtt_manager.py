"""MQTT Manager for Navien NWP500 integration."""

import asyncio
import logging
import time
import types
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from awscrt.exceptions import AwsCrtError

if TYPE_CHECKING:
    from nwp500 import (  # type: ignore[attr-defined]
        Device,
        DeviceFeature,
        DeviceStatus,
        MqttDiagnosticsCollector,
        NavienAuthClient,
        NavienMqttClient,
        ReservationSchedule,
        TOUReservationSchedule,
    )
    from nwp500.mqtt_events import (  # type: ignore[attr-defined]
        ConnectionInterruptedEvent,
        ConnectionResumedEvent,
    )

_LOGGER = logging.getLogger(__name__)

# Reconnection backoff delays (seconds): 2s, 5s, 15s, 30s, 60s cap
_RECONNECT_BACKOFF_DELAYS: list[float] = [2.0, 5.0, 15.0, 30.0, 60.0]
_RECONNECTION_FAILED_EVENT = "reconnection_failed"


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
        on_reconnected: Callable[[], None] | None = None,
        on_reconnection_failed: Callable[[int], None] | None = None,
        ha_instance_id: str | None = None,
    ) -> None:
        """Initialize the MQTT manager."""
        self.loop = hass_loop
        self.auth_client = auth_client
        self._ha_instance_id = ha_instance_id
        self.mqtt_client: NavienMqttClient | None = None
        self.diagnostics: MqttDiagnosticsCollector | None = None
        self._on_status_update_callback = on_status_update
        self._on_feature_update_callback = on_feature_update
        self._on_reservation_update_callback = on_reservation_update
        self._on_tou_update_callback = on_tou_update
        self._on_reconnected_callback = on_reconnected
        self._on_reconnection_failed_callback = on_reconnection_failed
        self.unit_system = unit_system

        # Connection tracking
        self.connected_since: float | None = None
        self.reconnection_in_progress: bool = False
        self.consecutive_timeouts: int = 0
        self._reconnect_attempts: int = 0
        self._last_reconnect_time: float = 0.0

        # Connection state tracking for diagnostics
        self._connection_interruptions: deque[dict[str, Any]] = deque(maxlen=20)

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
                MqttConnectionConfig,
                MqttDiagnosticsCollector,
                NavienMqttClient,
            )

            # Build a stable, per-installation client ID.
            # Format: navien-ha-{user_seq}-{ha_instance_id[:8]}
            #
            # user_seq alone is not sufficient — all HA instances authenticated
            # with the same Navien account share the same user_seq, causing
            # AWS IoT Core to kick each instance off as soon as another connects
            # (one active connection per client ID).  The HA instance ID is
            # generated once per installation and stored persistently, making
            # the combined client ID both stable across restarts and unique
            # per installation.
            user_seq = 0
            if self.auth_client.current_user:
                user_seq = self.auth_client.current_user.user_seq

            if user_seq and self._ha_instance_id:
                client_id = f"navien-ha-{user_seq}-{self._ha_instance_id[:8]}"
            elif user_seq:
                client_id = f"navien-ha-{user_seq}"
            else:
                client_id = None

            if client_id:
                _LOGGER.debug("Using stable MQTT client ID: %s", client_id)

            # Initialize diagnostics collector
            self.diagnostics = MqttDiagnosticsCollector(
                enable_verbose_logging=False
            )

            # Token validation is deferred to connect(), while construction
            # focuses on MQTT session configuration.
            # Use clean_session=False so the broker preserves subscriptions
            # across reconnections.  Combined with the stable client_id above,
            # this means a reconnect resumes the existing broker-side session
            # (session_present=True) and subscriptions are never lost, avoiding
            # the AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION errors that occur
            # when the broker discards a clean-session on every reconnect.
            self.mqtt_client = NavienMqttClient(
                self.auth_client,
                config=MqttConnectionConfig(
                    client_id=client_id,
                    clean_session=False,
                )
                if client_id
                else MqttConnectionConfig(clean_session=False),
                unit_system=self.unit_system,  # type: ignore[arg-type]
            )

            # Set up event listeners
            if self.mqtt_client:
                from nwp500.mqtt_events import (
                    MqttClientEvents,  # type: ignore[attr-defined]
                )

                # Connection lifecycle events
                self.mqtt_client.on(
                    MqttClientEvents.CONNECTION_INTERRUPTED,
                    self._on_connection_interrupted,
                )
                self.mqtt_client.on(
                    MqttClientEvents.CONNECTION_RESUMED,
                    self._on_connection_resumed,
                )
                self.mqtt_client.on(
                    _RECONNECTION_FAILED_EVENT,
                    self._on_reconnection_failed,
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
                from nwp500.mqtt_events import (
                    MqttClientEvents,  # type: ignore[attr-defined]
                )

                # Remove listeners
                self.mqtt_client.off(
                    MqttClientEvents.CONNECTION_INTERRUPTED,
                    self._on_connection_interrupted,
                )
                self.mqtt_client.off(
                    MqttClientEvents.CONNECTION_RESUMED,
                    self._on_connection_resumed,
                )
                self.mqtt_client.off(
                    _RECONNECTION_FAILED_EVENT,
                    self._on_reconnection_failed,
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

        mac_address = device.device_info.mac_address
        if mac_address not in self._tracked_mac_addresses:
            self._tracked_mac_addresses.add(mac_address)

        try:
            # Library now injects mac_address into DeviceStatus/DeviceFeature,
            # so direct method references work without device closures.
            await self.mqtt_client.subscribe_device_status(
                device,
                self._on_device_status_update_direct,
            )
            await self.mqtt_client.subscribe_device_feature(
                device,
                self._on_device_feature_update_direct,
            )
            # Subscribe to reservation responses using the typed API.
            # The library handles topic construction and response parsing.
            await self.mqtt_client.subscribe_reservation_response(
                device,
                lambda schedule: self._on_reservation_schedule(
                    mac_address, schedule
                ),
            )
            # Subscribe to TOU responses using the typed API (symmetric with
            # subscribe_reservation_response). Added to nwp500-python via PR.
            await self.mqtt_client.subscribe_tou_response(
                device,
                lambda tou: self._on_tou_schedule(mac_address, tou),
            )
        except Exception as err:
            _LOGGER.warning(
                "Failed to subscribe to device %s: %s",
                mac_address,
                err,
            )

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
            await self.mqtt_client.request_device_status(device)
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
                    await self.mqtt_client.set_power(
                        device, kwargs.get("power_on", True)
                    )
                case "set_temperature":
                    temp = kwargs.get("temperature")
                    if temp is not None:
                        await self.mqtt_client.set_dhw_temperature(
                            device, float(temp)
                        )
                case "set_dhw_mode":
                    mode = kwargs.get("mode")
                    if mode is not None:
                        await self.mqtt_client.set_dhw_mode(device, int(mode))
                case "set_tou_enabled":
                    enabled = kwargs.get("enabled", True)
                    await self.mqtt_client.set_tou_enabled(device, enabled)
                case "enable_anti_legionella":
                    period_days = kwargs.get("period_days", 14)
                    await self.mqtt_client.enable_anti_legionella(
                        device, period_days
                    )
                case "disable_anti_legionella":
                    await self.mqtt_client.disable_anti_legionella(device)
                case "update_reservations":
                    reservations = kwargs.get("reservations", [])
                    enabled = kwargs.get("enabled", True)
                    await self.mqtt_client.update_reservations(
                        device, reservations, enabled=enabled
                    )
                case "request_reservations":
                    await self.mqtt_client.request_reservations(device)
                case "configure_tou_schedule":
                    controller_serial = kwargs.get(
                        "controller_serial_number", ""
                    )
                    periods = kwargs.get("periods", [])
                    enabled = kwargs.get("enabled", True)
                    await self.mqtt_client.configure_tou_schedule(
                        device,
                        controller_serial_number=controller_serial,
                        periods=periods,
                        enabled=enabled,
                    )
                case "request_tou_settings":
                    controller_serial = kwargs.get(
                        "controller_serial_number", ""
                    )
                    await self.mqtt_client.request_tou_settings(
                        device,
                        controller_serial_number=controller_serial,
                    )
                case "set_vacation_days":
                    days = kwargs.get("days")
                    if days is not None:
                        await self.mqtt_client.set_vacation_days(
                            device, int(days)
                        )
                case "enable_demand_response":
                    await self.mqtt_client.enable_demand_response(device)
                case "disable_demand_response":
                    await self.mqtt_client.disable_demand_response(device)
                case "reset_air_filter":
                    await self.mqtt_client.reset_air_filter(device)
                case "set_recirculation_mode":
                    mode = kwargs.get("mode")
                    if mode is None:
                        _LOGGER.error(
                            "set_recirculation_mode requires 'mode' kwarg but none was provided"
                        )
                        return False
                    await self.mqtt_client.set_recirculation_mode(
                        device, int(mode)
                    )
                case "trigger_recirculation":
                    await self.mqtt_client.trigger_recirculation_hot_button(
                        device
                    )
                case _:
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
        """Force reconnection with exponential backoff.

        Uses increasing delays between attempts: 2s, 5s, 15s, 30s, 60s (cap).
        Backoff resets on successful reconnection.
        Retries indefinitely with exponential backoff until successful.
        """
        if self.reconnection_in_progress:
            return False

        self.reconnection_in_progress = True
        try:
            while True:
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
                try:
                    await asyncio.sleep(backoff_delay)
                except asyncio.CancelledError:
                    _LOGGER.debug("MQTT reconnection task was cancelled")
                    raise

                try:
                    # Re-initialize and connect (connect() will refresh auth tokens)
                    if await self.setup():
                        # Only update timestamp on successful reconnection
                        # This prevents rate-limiting from blocking retries on failed attempts
                        self._last_reconnect_time = time.time()

                        _LOGGER.info(
                            "Reconnection successful after %d attempt(s)",
                            self._reconnect_attempts + 1,
                        )
                        self.consecutive_timeouts = 0
                        self._reconnect_attempts = 0  # Reset backoff on success

                        # Re-subscribe to all devices and restart periodic tasks
                        for device in devices:
                            await self.subscribe_device(device)
                            await self.start_periodic_requests(device)
                        return True
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    _LOGGER.debug(
                        "Setup attempt failed: %s", err, exc_info=True
                    )

                # Failed - increment attempt counter for next backoff
                self._reconnect_attempts += 1
                next_backoff_index = min(
                    self._reconnect_attempts, len(_RECONNECT_BACKOFF_DELAYS) - 1
                )
                next_backoff = _RECONNECT_BACKOFF_DELAYS[next_backoff_index]
                _LOGGER.warning(
                    "Reconnection failed (attempt %d). Retrying in %.0fs...",
                    self._reconnect_attempts,
                    next_backoff,
                )
                # Loop continues - will retry after backoff

        except asyncio.CancelledError:
            raise
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

    def _on_reservation_schedule(
        self, mac_address: str, schedule: ReservationSchedule
    ) -> None:
        """Handle typed reservation schedule from device.

        Called by the library's subscribe_reservation_response() with a parsed
        ReservationSchedule object. Converts to dict for coordinator storage.
        """
        try:
            _LOGGER.debug("Received reservation schedule for %s", mac_address)
            if self._on_reservation_update_callback:
                response = (
                    schedule.model_dump()
                    if hasattr(schedule, "model_dump")
                    else {}
                )
                self._on_reservation_update_callback(mac_address, response)
        except Exception as err:
            _LOGGER.error("Error handling reservation schedule: %s", err)

    def _on_tou_schedule(
        self, mac_address: str, tou: TOUReservationSchedule
    ) -> None:
        """Handle typed TOU schedule from device.

        Called by subscribe_tou_response() with a parsed TOUReservationSchedule.
        Converts to dict for coordinator storage, mirroring _on_reservation_schedule.
        """
        try:
            _LOGGER.debug("Received TOU schedule for %s", mac_address)
            if self._on_tou_update_callback:
                response = (
                    tou.model_dump() if hasattr(tou, "model_dump") else {}
                )
                self._on_tou_update_callback(mac_address, response)
        except Exception as err:
            _LOGGER.error("Error handling TOU schedule: %s", err)

    # Event Handlers
    def _on_device_status_update_direct(self, status: DeviceStatus) -> None:
        """Handle direct MQTT status update.

        The library injects mac_address into DeviceStatus as of v8.0.0, so no
        device closure is needed for routing.
        """
        try:
            mac = status.mac_address or ""
            if not mac:
                _LOGGER.warning(
                    "Status received without device MAC; discarding"
                )
                return
            self._on_status_update_callback(mac, status)
        except Exception as err:
            _LOGGER.error("Error handling direct status update: %s", err)

    def _on_device_feature_update_direct(self, feature: DeviceFeature) -> None:
        """Handle direct MQTT feature update.

        The library injects mac_address into DeviceFeature as of v8.0.0, so no
        device closure is needed for routing.
        """
        try:
            mac = feature.mac_address or ""
            if not mac:
                _LOGGER.warning(
                    "Feature received without device MAC; discarding"
                )
                return
            self._on_feature_update_callback(mac, feature)
        except Exception as err:
            _LOGGER.error("Error handling direct feature update: %s", err)

    def _on_connection_interrupted(
        self, event: ConnectionInterruptedEvent
    ) -> None:
        """Handle connection interruption event for diagnostics."""
        self.connected_since = None
        error = event.error
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
                lambda f: (
                    _LOGGER.debug(
                        "record_connection_drop error: %s", f.exception()
                    )
                    if not f.cancelled() and f.exception()
                    else None
                )
            )

    def _on_connection_resumed(self, event: ConnectionResumedEvent) -> None:
        """Handle connection resume event for diagnostics."""
        self.connected_since = time.time()
        if self._on_reconnected_callback:
            self.loop.call_soon_threadsafe(self._on_reconnected_callback)
        if self.diagnostics:
            future = asyncio.run_coroutine_threadsafe(
                self.diagnostics.record_connection_success(
                    event_type="resumed",
                    session_present=event.session_present,
                    return_code=event.return_code,
                ),
                self.loop,
            )
            future.add_done_callback(
                lambda f: (
                    _LOGGER.debug(
                        "record_connection_success error: %s", f.exception()
                    )
                    if not f.cancelled() and f.exception()
                    else None
                )
            )

    def _on_reconnection_failed(self, attempts: int) -> None:
        """Handle fatal failure of the library's internal reconnect loop."""
        self.connected_since = None
        _LOGGER.error(
            "Library MQTT reconnection loop stopped after %d attempt(s)",
            attempts,
        )
        if self._on_reconnection_failed_callback:
            self.loop.call_soon_threadsafe(
                self._on_reconnection_failed_callback, attempts
            )
