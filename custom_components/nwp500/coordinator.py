"""DataUpdateCoordinator for the Navien NWP500 integration."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from awscrt.exceptions import AwsCrtError

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CONF_SCAN_INTERVAL,
    SLOW_UPDATE_THRESHOLD,
    CONF_TOKEN_DATA,
)

from nwp500.exceptions import (
    AuthenticationError,
    InvalidCredentialsError,
    TokenRefreshError,
    TokenExpiredError,
    MqttError,
    MqttNotConnectedError,
    MqttConnectionError,
)

if TYPE_CHECKING:
    from nwp500 import (  # type: ignore[attr-defined]
        NavienAuthClient,
        NavienAPIClient,
        NavienMqttClient,
    )

_LOGGER = logging.getLogger(__name__)


class NWP500DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the NWP500 API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self.auth_client: NavienAuthClient | None = None
        self.api_client: NavienAPIClient | None = None
        self.mqtt_client: NavienMqttClient | None = None
        self.devices: list[Any] = []
        self.device_features: dict[str, Any] = {}
        self._periodic_task: asyncio.Task[Any] | None = None
        self._device_info_request_counter: dict[str, int] = (
            {}
        )  # Track fallback device info requests

        # Performance tracking
        self._update_count: int = 0
        self._total_update_time: float = 0.0
        self._slowest_update: float = 0.0

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

        # Register options update listener
        self.entry.async_on_unload(
            entry.add_update_listener(self.async_options_updated)
        )

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

    @staticmethod
    async def async_options_updated(
        hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Handle options update."""
        await hass.config_entries.async_reload(entry.entry_id)

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint.

        Optimized to reduce memory churn by reusing existing device_data dict
        and only updating changed fields instead of copying entire structures.

        Performance metrics are tracked and logged for monitoring.
        """
        # Track performance metrics
        start_time = time.monotonic()

        if not self.auth_client:
            await self._setup_clients()

        try:
            # Reuse existing data structure to reduce memory allocations
            # Only create new dict if this is the first update
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
                if (
                    self.mqtt_client is not None
                    and self.mqtt_client.is_connected
                ):
                    try:
                        await self.mqtt_client.request_device_status(device)
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
                                await self.mqtt_client.request_device_info(
                                    device
                                )
                                _LOGGER.debug(
                                    "Fallback device info request: %s",
                                    mac_address,
                                )
                            except AwsCrtError as info_err:
                                # Handle clean session cancellation gracefully
                                if info_err.name == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION":
                                    _LOGGER.debug(
                                        "Device info request queued due to "
                                        "MQTT reconnection for %s",
                                        mac_address,
                                    )
                                else:
                                    _LOGGER.debug(
                                        "Fallback device info request failed "
                                        "for %s: %s",
                                        mac_address,
                                        info_err,
                                    )
                            except (RuntimeError, OSError, TimeoutError) as info_err:
                                _LOGGER.debug(
                                    "Fallback device info request failed "
                                    "for %s: %s",
                                    mac_address,
                                    info_err,
                                )

                    except AwsCrtError as err:
                        # Handle clean session cancellation gracefully
                        # This occurs during MQTT reconnection and is expected
                        # The command will be queued and retried automatically
                        if err.name == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION":
                            _LOGGER.debug(
                                "Status request queued due to MQTT "
                                "reconnection for device %s",
                                mac_address,
                            )
                        else:
                            _LOGGER.warning(
                                "Failed to request status for device %s: %s",
                                mac_address,
                                err,
                            )
                    except (RuntimeError, OSError, TimeoutError) as err:
                        _LOGGER.warning(
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
                    "latency or connectivity issues. "
                    "Consider increasing scan interval in integration "
                    "options if this persists.",
                    duration,
                    SLOW_UPDATE_THRESHOLD,
                )

            return device_data

        except (
            AwsCrtError,
            RuntimeError,
            OSError,
            TimeoutError,
            AttributeError,
            KeyError,
            MqttError,
        ) as err:
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
                NavienAuthClient,
                NavienAPIClient,
                NavienMqttClient,
            )
        except ImportError as err:
            _LOGGER.error(
                "nwp500-python library not installed. Please install: "
                "pip install nwp500-python==5.0.0 awsiotsdk>=1.25.0"
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
            await self.auth_client.__aenter__()  # Authenticate or restore

            # Save tokens after successful authentication
            # This updates tokens if they were refreshed or saves new ones
            await self._save_tokens()

            # Setup API client
            self.api_client = NavienAPIClient(auth_client=self.auth_client)

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

            _LOGGER.info("Found %d devices", len(self.devices))

            # Setup MQTT client for real-time updates with event emitter
            self.mqtt_client = NavienMqttClient(self.auth_client)

            # Set up event listeners using the event emitter functionality
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

            # Connect to MQTT - this may generate blocking I/O warnings
            # from AWS IoT SDK but it's unavoidable since the underlying
            # library does blocking operations
            try:
                connected = await self.mqtt_client.connect()
            except (AwsCrtError, RuntimeError, OSError, TimeoutError) as err:
                _LOGGER.warning("MQTT connection failed: %s", err)
                connected = False

            if not connected:
                _LOGGER.warning(
                    "Failed to connect to MQTT, will continue with "
                    "API-only mode"
                )
            else:
                # Subscribe to device status updates
                for device in self.devices:
                    try:
                        await self.mqtt_client.subscribe_device_status(
                            device, self._on_device_status_update
                        )
                        # Also subscribe to device feature updates for
                        # firmware/serial info
                        await self.mqtt_client.subscribe_device_feature(
                            device, self._on_device_feature_update
                        )
                    except (AwsCrtError, RuntimeError, OSError, TimeoutError) as err:
                        _LOGGER.warning(
                            "Failed to subscribe to device %s: %s",
                            device.device_info.mac_address,
                            err,
                        )

                # Start periodic status requests via MQTT
                # (every 5 minutes)
                for device in self.devices:
                    try:
                        await (
                            self.mqtt_client
                            .start_periodic_device_status_requests(
                                device, 300.0
                            )
                        )
                        # Also request device info periodically
                        # (every 30 minutes for firmware info)
                        await (
                            self.mqtt_client
                            .start_periodic_device_info_requests(
                                device, 1800.0
                            )
                        )

                        # Make an immediate device info request to get
                        # firmware/serial data right away
                        try:
                            await self.mqtt_client.request_device_info(
                                device
                            )
                            _LOGGER.info(
                                "Sent immediate device info request "
                                "for %s",
                                device.device_info.mac_address,
                            )
                        except (AwsCrtError, RuntimeError, OSError, TimeoutError) as info_err:
                            _LOGGER.warning(
                                "Failed to send immediate device info "
                                "request for %s: %s",
                                device.device_info.mac_address,
                                info_err,
                            )

                    except (AwsCrtError, RuntimeError, OSError, TimeoutError) as err:
                        _LOGGER.warning(
                            "Failed to start periodic requests for "
                            "device %s: %s",
                            device.device_info.mac_address,
                            err,
                        )

            _LOGGER.info(
                "Successfully connected to Navien cloud service with "
                "%d devices",
                len(self.devices),
            )

        except (
            AuthenticationError,
            InvalidCredentialsError,
            TokenRefreshError,
            TokenExpiredError,
        ) as err:
            # Authentication failed - trigger reauth flow
            _LOGGER.error(
                "Authentication failed for %s: %s. Starting reauth flow.",
                email,
                err,
            )
            self.entry.async_start_reauth(self.hass)
            await self.async_shutdown()
            raise UpdateFailed(
                "Authentication failed. Please re-authenticate through "
                "the notifications panel or Settings > Devices & Services."
            ) from err
        except (AwsCrtError, RuntimeError, OSError, TimeoutError, MqttError) as err:
            _LOGGER.error("Failed to setup clients: %s", err)
            await self.async_shutdown()
            raise UpdateFailed(
                f"Failed to connect to Navien service: {err}"
            ) from err

    def _on_device_status_event(self, event_data: dict[str, Any]) -> None:
        """Handle device status event from event emitter.
        
        Uses catch-all exception handling to ensure event handler resilience
        as per EventEmitter pattern - must not allow callback errors to
        propagate to the event emitter.
        """
        _LOGGER.debug("Received device status event: %s", event_data)

        try:
            # Extract status from event data
            status = event_data.get("status")
            device = event_data.get("device")

            if status and device:
                mac_address = device.device_info.mac_address
                if self.data and mac_address in self.data:
                    self.data[mac_address]["status"] = status
                    self.data[mac_address]["last_update"] = time.time()

                    # Schedule update for all listeners using thread-safe method
                    self.hass.loop.call_soon_threadsafe(
                        self.async_update_listeners
                    )
        except Exception as err:  # noqa: BLE001 - EventEmitter callback must catch all
            _LOGGER.error("Error handling device status event: %s", err)

    def _on_device_feature_event(self, event_data: dict[str, Any]) -> None:
        """Handle device feature event from event emitter.
        
        Uses catch-all exception handling to ensure event handler resilience
        as per EventEmitter pattern - must not allow callback errors to
        propagate to the event emitter.
        """
        _LOGGER.debug("Received device feature event: %s", event_data)

        try:
            feature = event_data.get("feature")
            device = event_data.get("device")

            if feature and device:
                mac_address = device.device_info.mac_address
                self.device_features[mac_address] = feature
        except Exception as err:  # noqa: BLE001 - EventEmitter callback must catch all
            _LOGGER.error("Error handling device feature event: %s", err)

    def _on_connection_lost(self, event_data: dict[str, Any]) -> None:
        """Handle MQTT connection lost event."""
        _LOGGER.warning("MQTT connection lost: %s", event_data)

    def _on_connection_restored(self, event_data: dict[str, Any]) -> None:
        """Handle MQTT connection restored event.
        
        Connection restoration may involve token refresh, so save updated tokens.
        """
        _LOGGER.info("MQTT connection restored: %s", event_data)
        
        # Schedule token save on the main event loop (thread-safe)
        # Connection restoration may have refreshed tokens
        asyncio.run_coroutine_threadsafe(
            self._save_tokens(), self.hass.loop
        )

    def _on_reconnection_failed(self, attempt_count: int) -> None:
        """Handle MQTT reconnection failed event.

        When reconnection fails after max attempts, automatically reset
        the reconnection state and trigger a new reconnection cycle.
        """
        _LOGGER.warning(
            "MQTT reconnection failed after %d attempts. "
            "Resetting reconnection state and retrying...",
            attempt_count,
        )

        if self.mqtt_client:
            # Schedule reset_reconnect on the main event loop
            asyncio.run_coroutine_threadsafe(
                self.mqtt_client.reset_reconnect(), self.hass.loop
            )

    def _on_device_status_update(self, status: Any) -> None:
        """Handle device status update from MQTT (legacy callback).
        
        Uses catch-all exception handling to ensure callback resilience
        as per EventEmitter pattern - must not allow callback errors to
        propagate to the MQTT client.
        """
        try:
            # Find the device by checking the status data
            # Status should contain mac_address or match by other means
            _LOGGER.debug("Received device status update: %s", status)

            # Use thread-safe method to schedule update on the main
            # event loop
            if hasattr(status, "device") and hasattr(
                status.device, "device_info"
            ):
                mac_address = status.device.device_info.mac_address
            else:
                # If we can't identify the device, update all devices
                # This might need refinement based on actual structure
                for device in self.devices:
                    mac_address = device.device_info.mac_address
                    if self.data and mac_address in self.data:
                        self.data[mac_address]["status"] = status
                        # Use time.time() instead of loop.time() since
                        # we're in different thread
                        self.data[mac_address]["last_update"] = (
                            time.time()
                        )

                # Schedule update for all listeners using thread-safe
                # method
                self.hass.loop.call_soon_threadsafe(
                    self.async_update_listeners
                )
                return

            if self.data and mac_address in self.data:
                self.data[mac_address]["status"] = status
                # Use time.time() instead of loop.time() since
                # we're in different thread
                self.data[mac_address]["last_update"] = time.time()

                # Schedule update for all listeners using
                # thread-safe method
                self.hass.loop.call_soon_threadsafe(self.async_update_listeners)

        except Exception as err:  # noqa: BLE001 - MQTT callback must catch all
            _LOGGER.error("Error handling device status update: %s", err)

    def _on_device_feature_update(self, feature: Any) -> None:
        """Handle device feature update from MQTT (legacy callback).
        
        Uses catch-all exception handling to ensure callback resilience
        as per EventEmitter pattern - must not allow callback errors to
        propagate to the MQTT client.
        """
        try:
            _LOGGER.debug("Received device feature update: %s", feature)

            # Store the device feature data for use in device info
            # Match it to devices by checking against our devices
            for device in self.devices:
                mac_address = device.device_info.mac_address
                # Store feature data - used by entities for device info
                self.device_features[mac_address] = feature
                break  # Assume single device for now

        except Exception as err:  # noqa: BLE001 - MQTT callback must catch all
            _LOGGER.error("Error handling device feature update: %s", err)

    async def async_control_device(
        self, mac_address: str, command: str, **kwargs: Any
    ) -> bool:
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
                    # Use set_dhw_temperature_display which takes
                    # the display temperature directly. This is more
                    # intuitive as it matches what users see on the
                    # device/app
                    await self.mqtt_client.set_dhw_temperature_display(
                        device, int(temperature)
                    )
            elif command == "set_dhw_mode":
                mode = kwargs.get("mode")
                if mode:
                    await self.mqtt_client.set_dhw_mode(device, int(mode))
            else:
                _LOGGER.error("Unknown command: %s", command)
                return False

            # Request status update after command
            try:
                await self.mqtt_client.request_device_status(device)
            except AwsCrtError as status_err:
                # Handle clean session cancellation gracefully
                if status_err.name == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION":
                    _LOGGER.debug(
                        "Status request after command queued due to "
                        "MQTT reconnection for device %s",
                        mac_address,
                    )
                else:
                    _LOGGER.warning(
                        "Failed to request status after command for %s: %s",
                        mac_address,
                        status_err,
                    )
            return True

        except AwsCrtError as err:
            # Handle clean session cancellation gracefully
            if err.name == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION":
                _LOGGER.info(
                    "Command %s queued due to MQTT reconnection for "
                    "device %s",
                    command,
                    mac_address,
                )
                return True  # Command is queued, will be sent on reconnect
            _LOGGER.error("Failed to send command %s: %s", command, err)
            return False
        except (
            RuntimeError,
            OSError,
            TimeoutError,
            ValueError,
            TypeError,
            MqttError,
        ) as err:
            _LOGGER.error("Failed to send command %s: %s", command, err)
            return False

    async def async_request_device_info(
        self, mac_address: str | None = None
    ) -> bool:
        """Manually request device info for a specific device or all devices."""
        if not self.mqtt_client:
            _LOGGER.error("MQTT client not available")
            return False

        devices_to_update = []
        if mac_address:
            # Request for specific device
            for dev in self.devices:
                if dev.device_info.mac_address == mac_address:
                    devices_to_update.append(dev)
                    break
        else:
            # Request for all devices
            devices_to_update = self.devices

        if not devices_to_update:
            _LOGGER.error("No devices found for device info request")
            return False

        success_count = 0
        for device in devices_to_update:
            try:
                await self.mqtt_client.request_device_info(device)
                _LOGGER.info(
                    "Sent manual device info request for %s",
                    device.device_info.mac_address,
                )
                success_count += 1
            except AwsCrtError as err:
                # Handle clean session cancellation gracefully
                if err.name == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION":
                    _LOGGER.debug(
                        "Device info request queued due to MQTT "
                        "reconnection for %s",
                        device.device_info.mac_address,
                    )
                    # Do not count as success since it's only queued, not completed
                else:
                    _LOGGER.error(
                        "Failed to send manual device info request for %s: %s",
                        device.device_info.mac_address,
                        err,
                    )
            except (RuntimeError, OSError, TimeoutError, MqttError) as err:
                _LOGGER.error(
                    "Failed to send manual device info request for %s: %s",
                    device.device_info.mac_address,
                    err,
                )

        return success_count > 0

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        if self.mqtt_client:
            try:
                # Remove event listeners
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
            except (AwsCrtError, RuntimeError, OSError) as err:
                _LOGGER.debug("Error disconnecting MQTT client: %s", err)
            self.mqtt_client = None

        if self.auth_client:
            try:
                await self.auth_client.__aexit__(None, None, None)
            except (RuntimeError, OSError) as err:
                _LOGGER.debug("Error closing auth client: %s", err)
            self.auth_client = None

        self.api_client = None
