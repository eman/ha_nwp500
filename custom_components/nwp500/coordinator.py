"""DataUpdateCoordinator for the Navien NWP500 integration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from awscrt.exceptions import AwsCrtError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from nwp500.exceptions import (
    AuthenticationError,
    InvalidCredentialsError,
    MqttError,
    TokenExpiredError,
    TokenRefreshError,
)

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_DATA,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SLOW_UPDATE_THRESHOLD,
)
from .mqtt_manager import NWP500MqttManager, get_aws_error_name

if TYPE_CHECKING:
    from nwp500 import (  # type: ignore[attr-defined]
        Device,
        DeviceFeature,
        DeviceStatus,
        NavienAPIClient,
        NavienAuthClient,
    )

_LOGGER = logging.getLogger(__name__)


class NWP500DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the NWP500 API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self.auth_client: NavienAuthClient | None = None
        self.api_client: NavienAPIClient | None = None
        self.mqtt_manager: NWP500MqttManager | None = None
        self.devices: list[Device] = []
        self._devices_by_mac: dict[str, Device] = {}  # O(1) device lookup cache
        self.device_features: dict[str, DeviceFeature] = {}
        self._periodic_task: asyncio.Task[Any] | None = None
        self._reconnect_task: asyncio.Task[Any] | None = None  # Track reconnection task
        self._device_info_request_counter: dict[
            str, int
        ] = {}  # Track fallback device info requests

        # Performance tracking
        self._update_count: int = 0
        self._total_update_time: float = 0.0
        self._slowest_update: float = 0.0

        # MQTT communication telemetry
        self._last_request_id: str | None = None
        self._last_request_time: float | None = None
        self._last_response_id: str | None = None
        self._last_response_time: float | None = None
        self._total_requests_sent: int = 0
        self._total_responses_received: int = 0
        self._mqtt_connected_since: float | None = None
        self._consecutive_timeouts: int = 0
        self._reconnection_in_progress: bool = False
        # Use deque for efficient circular buffer (automatic maxlen enforcement)
        self._timeout_history: deque[dict[str, Any]] = deque(maxlen=20)

        # Get scan interval from options, fall back to default
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

        # Install custom exception handler to suppress benign AWS CRT errors
        # Must be called after super().__init__() so self.hass.loop is available
        self._install_exception_handler()

    def _install_exception_handler(self) -> None:
        """Install custom exception handler to suppress benign AWS CRT errors.

        AWS IoT SDK creates internal futures during MQTT operations. When a clean
        session reconnection occurs, pending operations are cancelled with
        AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION. These exceptions can complete
        after our await/timeout handling, causing "Future exception was never
        retrieved" errors in Home Assistant logs.

        This handler suppresses these benign errors while allowing other exceptions
        to propagate normally.
        """
        loop = self.hass.loop
        original_handler = loop.get_exception_handler()

        def custom_exception_handler(
            loop: asyncio.AbstractEventLoop, context: dict[str, Any]
        ) -> None:
            """Handle uncaught exceptions in the event loop."""
            exception = context.get("exception")

            # Suppress AWS CRT clean session errors - these are benign
            if isinstance(exception, AwsCrtError):
                error_name = get_aws_error_name(exception)
                if error_name == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION":
                    _LOGGER.debug(
                        "Suppressed benign AWS CRT error during MQTT reconnection: %s",
                        exception,
                    )
                    return

            # For all other exceptions, use the original handler or default
            if original_handler:
                original_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(custom_exception_handler)

    def _update_device_cache(self) -> None:
        """Update the devices-by-MAC lookup cache for O(1) access.

        Call this after updating self.devices to keep the cache in sync.
        """
        self._devices_by_mac = {d.device_info.mac_address: d for d in self.devices}

    async def _save_tokens(self) -> None:
        """Save current authentication tokens to entry.data.

        This enables token persistence across HA restarts, reducing API load
        and improving startup time by reusing valid tokens.
        """
        if not self.auth_client or not self.auth_client.current_tokens:
            return

        tokens = self.auth_client.current_tokens

        # Serialize tokens using the library's to_dict() method
        token_data = tokens.to_dict()

        # Update entry data with tokens (encrypted by HA)
        new_data = {
            **self.entry.data,
            CONF_TOKEN_DATA: token_data,
        }

        self.hass.config_entries.async_update_entry(
            self.entry,
            data=new_data,
        )

        _LOGGER.debug(
            "Saved authentication tokens (expires at: %s)",
            tokens.expires_at,
        )

    def get_performance_stats(self) -> dict[str, Any]:
        """Get coordinator performance statistics.

        Returns:
            Dictionary with performance metrics including:
            - update_count: Number of updates performed
            - average_time: Average update duration in seconds
            - slowest_time: Slowest update duration in seconds
            - total_time: Total time spent in updates
        """
        if self._update_count == 0:
            return {
                "update_count": 0,
                "average_time": 0.0,
                "slowest_time": 0.0,
                "total_time": 0.0,
            }

        return {
            "update_count": self._update_count,
            "average_time": self._total_update_time / self._update_count,
            "slowest_time": self._slowest_update,
            "total_time": self._total_update_time,
        }

    def get_mqtt_telemetry(self) -> dict[str, Any]:
        """Get MQTT telemetry data for diagnostic sensors.

        Returns:
            Dictionary containing:
            - last_request_id: ID of last status request
            - last_request_time: Timestamp of last status request
            - last_response_id: ID of last status response
            - last_response_time: Timestamp of last status response
            - total_requests_sent: Total requests sent
            - total_responses_received: Total responses received
            - mqtt_connected: Whether MQTT is currently connected
            - mqtt_connected_since: Timestamp when MQTT connected
            - consecutive_timeouts: Current consecutive timeout count
            - timeout_history: Recent timeout events
        """
        mqtt_connected = False
        if self.mqtt_manager:
            mqtt_connected = self.mqtt_manager.is_connected

        return {
            "last_request_id": self._last_request_id,
            "last_request_time": self._last_request_time,
            "last_response_id": self._last_response_id,
            "last_response_time": self._last_response_time,
            "total_requests_sent": self._total_requests_sent,
            "total_responses_received": self._total_responses_received,
            "mqtt_connected": mqtt_connected,
            "mqtt_connected_since": (
                self.mqtt_manager.connected_since if self.mqtt_manager else None
            ),
            "consecutive_timeouts": self._consecutive_timeouts,
            "timeout_history": self._timeout_history,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint.

        Optimized to reduce memory churn by reusing existing device_data dict
        and only updating changed fields instead of copying entire structures.

        Performance metrics are tracked and logged for monitoring.

        Note: This method may be called multiple times in quick succession during
        startup (once for first_refresh, once for scheduled update). This is
        expected Home Assistant behavior and not a bug.
        """
        # Track performance metrics
        start_time = time.monotonic()

        if not self.auth_client:
            await self._setup_clients()

        # Check MQTT connection state before attempting requests
        if self.mqtt_manager:
            if not self.mqtt_manager.is_connected:
                _LOGGER.error(
                    "MQTT client is not connected. Device status requests "
                    "will fail. Connection may have been lost or failed "
                    "to reconnect."
                )

        try:
            # Reuse existing data structure to leverage Python 3.13's optimized
            # dictionary operations. Only create new dict on first update.
            device_data = dict(self.data) if self.data else {}

            for device in self.devices:
                mac_address = device.device_info.mac_address

                # Initialize device entry if missing, otherwise reuse existing
                if mac_address not in device_data:
                    device_data[mac_address] = {
                        "device": device,
                        "status": None,  # Will be updated via MQTT
                        "last_update": None,
                    }
                else:
                    # Just update the device reference
                    device_data[mac_address]["device"] = device

                # Request fresh status via MQTT (async, will update)
                if self.mqtt_manager:
                    try:
                        # Generate request ID for tracking
                        request_id = f"{mac_address}_{int(time.time() * 1000)}"
                        self._last_request_id = request_id
                        self._last_request_time = time.time()
                        self._total_requests_sent += 1

                        _LOGGER.debug(
                            "MQTT Status Request [%s] - Device: %s, "
                            "Request #%d, Time: %.3f",
                            request_id,
                            mac_address,
                            self._total_requests_sent,
                            self._last_request_time,
                        )

                        # Add timeout to prevent hanging on MQTT issues
                        success = await asyncio.wait_for(
                            self.mqtt_manager.request_status(device),
                            timeout=10.0,
                        )

                        if not success:
                            raise MqttError(
                                f"MQTT status request failed for device "
                                f"{mac_address}: internal client error"
                            )

                        _LOGGER.debug(
                            "Requested status update for device %s", mac_address
                        )

                        # Also request device info occasionally
                        # (every 10th coordinator update cycle)
                        # This provides a fallback if periodic requests fail
                        counter = (
                            self._device_info_request_counter.get(
                                mac_address, 0
                            )
                            + 1
                        )
                        counter = counter % 10
                        self._device_info_request_counter[mac_address] = counter

                        if counter == 0:
                            # Every 10th update (~5 minutes)
                            try:
                                await asyncio.wait_for(
                                    self.mqtt_manager.request_device_info(
                                        device
                                    ),
                                    timeout=10.0,
                                )
                                _LOGGER.debug(
                                    "Fallback device info request: %s",
                                    mac_address,
                                )
                            except TimeoutError:
                                _LOGGER.warning(
                                    "Timeout on fallback device info request "
                                    "for %s",
                                    mac_address,
                                )

                    except (TimeoutError, MqttError):
                        self._consecutive_timeouts += 1

                        # Record timeout event in history
                        timeout_event: dict[str, Any] = {
                            "timestamp": time.time(),
                            "device_mac": mac_address,
                            "consecutive_count": self._consecutive_timeouts,
                        }
                        self._timeout_history.append(timeout_event)

                        _LOGGER.error(
                            "Timeout requesting status for device %s - "
                            "MQTT may be disconnected (consecutive timeouts: %d). "
                            "Check MQTT connection state.",
                            mac_address,
                            self._consecutive_timeouts,
                        )

                        # After 3 consecutive timeouts, force reconnection
                        if self._consecutive_timeouts >= 3:
                            _LOGGER.warning(
                                "Detected %d consecutive MQTT timeouts. "
                                "Will attempt forced reconnection.",
                                self._consecutive_timeouts,
                            )
                            # Schedule reconnection asynchronously and track the task
                            # Cancel any existing reconnection task first
                            if self._reconnect_task and not self._reconnect_task.done():
                                self._reconnect_task.cancel()
                                try:
                                    await self._reconnect_task
                                except asyncio.CancelledError:
                                    _LOGGER.debug(
                                        "Previous MQTT reconnection task was cancelled"
                                    )
                            # Create and track new reconnection task
                            self._reconnect_task = asyncio.create_task(
                                self.mqtt_manager.force_reconnect(self.devices)
                            )
                    except AwsCrtError as err:
                        # Handle clean session cancellation gracefully
                        # This occurs during MQTT reconnection and is expected
                        # The command will be queued and retried automatically
                        if (
                            err.name
                            == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION"
                        ):
                            _LOGGER.debug(
                                "Status request queued due to MQTT "
                                "reconnection for device %s",
                                mac_address,
                            )
                        else:
                            _LOGGER.error(
                                "Failed to request status for device %s: %s",
                                mac_address,
                                err,
                            )
                    except (RuntimeError, OSError) as err:
                        _LOGGER.error(
                            "Failed to request status for device %s: %s",
                            mac_address,
                            err,
                        )

            # Calculate and log performance metrics
            duration = time.monotonic() - start_time
            self._update_count += 1
            self._total_update_time += duration

            if duration > self._slowest_update:
                self._slowest_update = duration

            # Log performance for monitoring
            avg_time = self._total_update_time / self._update_count

            _LOGGER.debug(
                "Coordinator update #%d completed in %.2fs for %d "
                "device(s) (avg: %.2fs, slowest: %.2fs)",
                self._update_count,
                duration,
                len(self.devices),
                avg_time,
                self._slowest_update,
            )

            # Warn if update is unusually slow
            if duration > SLOW_UPDATE_THRESHOLD:
                _LOGGER.warning(
                    "Slow coordinator update detected: %.2fs "
                    "(threshold: %.1fs). This may indicate network "
                    "latency or connectivity issues.",
                    duration,
                    SLOW_UPDATE_THRESHOLD,
                )

            return device_data

        except Exception as err:
            # Track failed update time as well
            duration = time.monotonic() - start_time
            _LOGGER.error(
                "Error fetching data after %.2fs: %s",
                duration,
                err,
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def _setup_clients(self) -> None:
        """Set up the API and MQTT clients."""
        try:
            from nwp500 import (  # type: ignore[attr-defined]
                NavienAPIClient,
                NavienAuthClient,
            )
        except ImportError as err:
            _LOGGER.error(
                "nwp500-python library not installed. Please install: "
                "pip install nwp500-python==7.2.3 awsiotsdk>=1.27.0"
            )
            raise UpdateFailed(
                f"nwp500-python library not available: {err}"
            ) from err

        email = self.entry.data[CONF_EMAIL]
        password = self.entry.data[CONF_PASSWORD]

        try:
            # Try to restore from stored tokens for faster startup
            stored_token_data = self.entry.data.get(CONF_TOKEN_DATA)
            stored_tokens = None

            if stored_token_data:
                try:
                    from nwp500.auth import AuthTokens

                    stored_tokens = AuthTokens.from_dict(stored_token_data)

                    if not stored_tokens.is_expired:
                        _LOGGER.info(
                            "Found valid stored tokens (expires: %s), "
                            "skipping initial authentication",
                            stored_tokens.expires_at,
                        )
                    else:
                        _LOGGER.info(
                            "Stored tokens expired (%s), will re-authenticate",
                            stored_tokens.expires_at,
                        )
                        stored_tokens = None
                except (KeyError, ValueError, TypeError) as err:
                    _LOGGER.warning(
                        "Failed to restore stored tokens: %s. "
                        "Will perform full authentication.",
                        err,
                    )
                    stored_tokens = None

            # Setup authentication client with stored tokens if available
            self.auth_client = NavienAuthClient(
                email, password, stored_tokens=stored_tokens
            )
            assert self.auth_client is not None
            await self.auth_client.__aenter__()  # Authenticate or restore

            # Save tokens after successful authentication
            await self._save_tokens()

            # Setup API client
            self.api_client = NavienAPIClient(auth_client=self.auth_client)
            assert self.api_client is not None

            # Get devices
            self.devices = await self.api_client.list_devices()
            if not self.devices:
                _LOGGER.error(
                    "No devices found for account %s. "
                    "Please verify: (1) Device is registered in "
                    "NaviLink app, (2) Device is online and connected "
                    "to WiFi, (3) Using correct account credentials.",
                    email,
                )
                raise UpdateFailed(
                    "No devices found for this account. "
                    "Please check the NaviLink app to verify your device "
                    "is registered and online."
                )

            # Build device lookup cache for O(1) access
            self._update_device_cache()

            _LOGGER.info("Found %d devices", len(self.devices))

            # Setup MQTT Manager
            self.mqtt_manager = NWP500MqttManager(
                self.hass.loop,
                self.auth_client,
                self._on_device_status_update,
                self._on_device_feature_update,
            )

            # Connect to MQTT
            connected = await self.mqtt_manager.setup()

            if not connected:
                _LOGGER.warning(
                    "Failed to connect to MQTT, will continue with "
                    "API-only mode"
                )
            else:
                # Subscribe and start periodic requests
                for device in self.devices:
                    await self.mqtt_manager.subscribe_device(device)
                    await self.mqtt_manager.start_periodic_requests(device)

                    # Immediately request device info to populate device features
                    try:
                        _LOGGER.info(
                            "Requesting initial device info for %s",
                            device.device_info.mac_address,
                        )
                        await self.mqtt_manager.request_device_info(device)
                    except Exception as err:
                        _LOGGER.warning(
                            "Failed to request initial device info: %s", err
                        )

            _LOGGER.info(
                "Successfully connected to Navien cloud service with "
                "%d devices",
                len(self.devices),
            )

        except InvalidCredentialsError as err:
            # Invalid credentials - trigger reauth flow
            _LOGGER.error(
                "Invalid credentials for %s: %s. Starting reauth flow.",
                email,
                err,
            )
            self.entry.async_start_reauth(self.hass)
            await self.async_shutdown()
            raise UpdateFailed(
                "Authentication failed. Please re-authenticate through "
                "the notifications panel or Settings > Devices & Services."
            ) from err
        except (
            TokenRefreshError,
            TokenExpiredError,
            AuthenticationError,
        ) as err:
            # Token or authentication errors - check if retriable
            # Network errors are marked as retriable in nwp500-python 7.2.3+
            # Only non-retriable errors should trigger reauth
            if err.retriable:
                # Network error during auth/token refresh - will retry
                _LOGGER.warning(
                    "Network error during authentication for %s (will retry): %s",
                    email,
                    err,
                )
            else:
                # Actual auth failure - trigger reauth
                _LOGGER.error(
                    "Authentication failed for %s: %s. Starting reauth flow.",
                    email,
                    err,
                )
                self.entry.async_start_reauth(self.hass)
            await self.async_shutdown()
            raise UpdateFailed(
                f"Authentication error: {err}. Will retry on next update cycle."
            ) from err
        except (
            AwsCrtError,
            RuntimeError,
            OSError,
            TimeoutError,
            MqttError,
        ) as err:
            _LOGGER.error("Failed to setup clients: %s", err)
            await self.async_shutdown()
            raise UpdateFailed(
                f"Failed to connect to Navien service: {err}"
            ) from err

    def _on_device_status_update(
        self, mac_address: str, status: DeviceStatus
    ) -> None:
        """Handle device status update from MQTT Manager."""
        try:
            _LOGGER.debug("Received device status update for %s", mac_address)

            # Track response telemetry
            response_time = time.time()
            self._total_responses_received += 1
            self._consecutive_timeouts = 0

            # Calculate time since last request
            time_since_request = (
                response_time - self._last_request_time
                if self._last_request_time
                else 0
            )

            response_id = f"{mac_address}_{int(response_time * 1000)}"
            self._last_response_id = response_id
            self._last_response_time = response_time

            _LOGGER.debug(
                "MQTT Status Response [%s] - Device: %s, Last Request: %s, Latency: %.3fs",
                response_id,
                mac_address,
                self._last_request_id or "N/A",
                time_since_request,
            )

            if self.data and mac_address in self.data:
                self.data[mac_address]["status"] = status
                self.data[mac_address]["last_update"] = time.time()

                # Schedule update for all listeners using thread-safe method
                self.hass.loop.call_soon_threadsafe(self.async_update_listeners)

        except Exception as err:
            _LOGGER.error("Error handling device status update: %s", err)

    def _on_device_feature_update(
        self, mac_address: str, feature: DeviceFeature
    ) -> None:
        """Handle device feature update from MQTT Manager."""
        try:
            _LOGGER.info("Received device feature update for %s", mac_address)

            # Debug: log feature data structure
            if hasattr(feature, "model_dump"):
                feature_dict = feature.model_dump()
                _LOGGER.info(
                    "Device feature keys: %s", sorted(feature_dict.keys())
                )
                # Log specific fields we're interested in
                _LOGGER.info(
                    "Serial: %s, Volume: %s, Controller FW: %s",
                    feature_dict.get("controller_serial_number"),
                    feature_dict.get("volume_code"),
                    feature_dict.get("controller_sw_version"),
                )

            self.device_features[mac_address] = feature
        except Exception as err:
            _LOGGER.error("Error handling device feature update: %s", err)

    async def async_control_device(
        self, mac_address: str, command: str, **kwargs: Any
    ) -> bool:
        """Send control command to device."""
        if not self.mqtt_manager:
            _LOGGER.error("MQTT manager not available")
            return False

        device = self._devices_by_mac.get(mac_address)

        if not device:
            _LOGGER.error("Device %s not found", mac_address)
            return False

        return await self.mqtt_manager.send_command(device, command, **kwargs)

    async def async_request_device_info(
        self, mac_address: str | None = None
    ) -> bool:
        """Manually request device info for a specific device or all devices."""
        if not self.mqtt_manager:
            _LOGGER.error("MQTT manager not available")
            return False

        devices_to_update = []
        if mac_address:
            # Request for specific device
            device = self._devices_by_mac.get(mac_address)
            if device:
                devices_to_update.append(device)
        else:
            # Request for all devices
            devices_to_update = self.devices

        if not devices_to_update:
            _LOGGER.error("No devices found for device info request")
            return False

        success_count = 0
        for device in devices_to_update:
            try:
                await self.mqtt_manager.request_device_info(device)
                _LOGGER.info(
                    "Sent manual device info request for %s",
                    device.device_info.mac_address,
                )
                success_count += 1
            except Exception as err:
                _LOGGER.error(
                    "Failed to send manual device info request for %s: %s",
                    device.device_info.mac_address,
                    err,
                )

        return success_count > 0

    async def async_update_reservations(
        self,
        mac_address: str,
        reservations: list[dict[str, int]],
        enabled: bool = True,
    ) -> bool:
        """Update reservation schedules for a device.

        Args:
            mac_address: Device MAC address
            reservations: List of reservation entries (built with build_reservation_entry)
            enabled: Whether the reservation system is enabled

        Returns:
            True if command was sent successfully
        """
        if not self.mqtt_manager:
            _LOGGER.error("MQTT manager not available")
            return False

        device = self._devices_by_mac.get(mac_address)

        if not device:
            _LOGGER.error("Device %s not found", mac_address)
            return False

        return await self.mqtt_manager.send_command(
            device,
            "update_reservations",
            reservations=reservations,
            enabled=enabled,
        )

    async def async_request_reservations(self, mac_address: str) -> bool:
        """Request current reservation schedules from a device.

        Args:
            mac_address: Device MAC address

        Returns:
            True if request was sent successfully
        """
        if not self.mqtt_manager:
            _LOGGER.error("MQTT manager not available")
            return False

        device = self._devices_by_mac.get(mac_address)

        if not device:
            _LOGGER.error("Device %s not found", mac_address)
            return False

        return await self.mqtt_manager.send_command(
            device, "request_reservations"
        )

    async def async_send_command(
        self, mac_address: str, command: str, **kwargs: Any
    ) -> bool:
        """Send a control command to a device.

        Args:
            mac_address: Device MAC address
            command: Command name
            **kwargs: Command arguments

        Returns:
            True if command was sent successfully
        """
        if not self.mqtt_manager:
            _LOGGER.error("MQTT manager not available")
            return False

        device = self._devices_by_mac.get(mac_address)

        if not device:
            _LOGGER.error("Device %s not found", mac_address)
            return False

        return await self.mqtt_manager.send_command(device, command, **kwargs)

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        # Cancel any pending reconnection task
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        if self.mqtt_manager:
            await self.mqtt_manager.disconnect()
            self.mqtt_manager = None

        if self.auth_client:
            try:
                await self.auth_client.__aexit__(None, None, None)
            except (RuntimeError, OSError) as err:
                _LOGGER.debug("Error closing auth client: %s", err)
            self.auth_client = None

        self.api_client = None
