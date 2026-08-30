"""Tests for NWP500MqttManager."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.nwp500.mqtt_manager import NWP500MqttManager


@pytest.fixture
def mock_auth_client():
    """Mock NavienAuthClient."""
    client = AsyncMock()
    client.ensure_valid_token = AsyncMock()
    client.get_access_token = AsyncMock(return_value="test_token")
    client.current_tokens = None
    return client


# Use the mock_mqtt_client fixture from conftest but override for mqtt_manager tests
@pytest.fixture
def mock_mqtt_client(monkeypatch):
    """Mock NavienMqttClient for mqtt_manager tests."""
    # Store all created clients and the last one
    state = {"last": None, "all": []}

    class MockFactory:
        """Factory that creates and tracks mock MQTT clients."""

        def __init__(self, auth_client, config=None, unit_system=None):
            """Create a mock client and track it."""
            self.auth_client = auth_client
            self.config = config
            self.unit_system = unit_system
            self.is_connected = True
            self.client_id = "test-client-id"

            # All async methods for tracking calls
            self.connect = AsyncMock(return_value=True)
            self.disconnect = AsyncMock()
            self.subscribe_device_status = AsyncMock()
            self.subscribe_device_feature = AsyncMock()
            self.subscribe_reservation_response = AsyncMock()
            self.subscribe_tou_response = AsyncMock()
            self.subscribe_energy_usage = AsyncMock()
            self.subscribe = AsyncMock()
            self.start_periodic_requests = AsyncMock()
            self.request_device_info = AsyncMock()
            self.ensure_device_info_cached = AsyncMock()
            self.stop_all_periodic_tasks = AsyncMock()
            self.reset_reconnect = AsyncMock()

            # Sync methods
            self.on = MagicMock()
            self.off = MagicMock()

            # Command methods (top-level in v8.0.0)
            self.set_power = AsyncMock()
            self.set_dhw_temperature = AsyncMock()
            self.set_dhw_mode = AsyncMock()
            self.set_tou_enabled = AsyncMock()
            self.enable_anti_legionella = AsyncMock()
            self.disable_anti_legionella = AsyncMock()
            self.update_reservations = AsyncMock()
            self.request_reservations = AsyncMock()
            self.request_device_status = AsyncMock()
            self.request_device_info = AsyncMock()
            self.request_energy_usage = AsyncMock()
            self.configure_tou_schedule = AsyncMock()
            self.trigger_recirculation_hot_button = AsyncMock()
            self.reset_air_filter = AsyncMock()
            self.enable_demand_response = AsyncMock()
            self.disable_demand_response = AsyncMock()
            self.set_recirculation_mode = AsyncMock()

            # Track this client
            state["last"] = self
            state["all"].append(self)

        def _on_connection_resumed_internal(
            self, return_code, session_present, **kwargs
        ):
            """Mock for compatibility with PatchedNavienMqttClient."""
            pass

    # Create a mock diagnostics collector with async methods
    mock_diagnostics = MagicMock()
    mock_diagnostics.record_connection_success = AsyncMock()
    mock_diagnostics.record_connection_drop = AsyncMock()

    # Patch at the import location using the factory
    monkeypatch.setattr("nwp500.NavienMqttClient", MockFactory)
    monkeypatch.setattr(
        "nwp500.MqttDiagnosticsCollector",
        MagicMock(return_value=mock_diagnostics),
    )

    # Create a wrapper that returns the most recently created client
    class ClientWrapper:
        """Wrapper that delegates to the last created client."""

        @property
        def all_clients(self):
            """Get all created clients."""
            return state["all"]

        def __getattr__(self, name):
            """Delegate to the last created client."""
            if state["last"]:
                return getattr(state["last"], name)
            raise AttributeError(f"No client created yet, attribute: {name}")

        def __setattr__(self, name, value):
            """Set attributes on the last created client."""
            # Allow setting on the wrapper itself
            if name in ("all_clients",):
                super().__setattr__(name, value)
            elif state["last"]:
                setattr(state["last"], name, value)
            else:
                raise AttributeError(
                    f"No client created yet, cannot set: {name}"
                )

    return ClientWrapper()


@pytest.fixture
def manager(mock_auth_client, mock_mqtt_client):
    """Create a NWP500MqttManager instance."""
    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
    )
    return manager


@pytest.fixture
def reconnection_failed_callback():
    """Mock callback for fatal library reconnect failures."""
    return MagicMock()


@pytest.mark.asyncio
async def test_setup_and_connect(manager, mock_mqtt_client):
    """Test setup and connection."""
    await manager.setup()

    # Verify client initialization
    mock_mqtt_client.connect.assert_called_once()
    assert manager.connected_since is not None


@pytest.mark.asyncio
async def test_disconnect(manager, mock_mqtt_client):
    """Test disconnect."""
    await manager.setup()

    await manager.disconnect()
    mock_mqtt_client.disconnect.assert_called_once()
    assert manager.connected_since is None


@pytest.mark.asyncio
async def test_subscribe_device(manager, mock_mqtt_client, mock_device):
    """Test subscribing to device topics."""
    await manager.setup()

    await manager.subscribe_device(mock_device)

    # Check called with device and ANY callback
    mock_mqtt_client.subscribe_device_status.assert_called_once()
    assert (
        mock_mqtt_client.subscribe_device_status.call_args[0][0] == mock_device
    )

    mock_mqtt_client.subscribe_device_feature.assert_called_once()
    assert (
        mock_mqtt_client.subscribe_device_feature.call_args[0][0] == mock_device
    )

    # Every response subscription the manager makes is asserted here: a
    # library method that was renamed or never existed would otherwise
    # raise inside subscribe_device and be swallowed by its broad except.
    for subscribe in (
        mock_mqtt_client.subscribe_reservation_response,
        mock_mqtt_client.subscribe_tou_response,
        mock_mqtt_client.subscribe_energy_usage,
    ):
        subscribe.assert_called_once()
        assert subscribe.call_args[0][0] == mock_device


@pytest.mark.asyncio
async def test_send_command_success(manager, mock_mqtt_client, mock_device):
    """Test sending a command successfully."""
    await manager.setup()

    result = await manager.send_command(mock_device, "set_power", power_on=True)

    assert result is True
    mock_mqtt_client.set_power.assert_called_with(mock_device, True)
    mock_mqtt_client.request_device_status.assert_called_with(mock_device)


@pytest.mark.asyncio
async def test_send_command_queued(manager, mock_mqtt_client, mock_device):
    """Test a command queued by the library during reconnection.

    Since nwp500-python 9.2.0, a clean-session cancellation is enqueued in the
    library's command queue and publish() returns 0 instead of raising, so the
    command is reported as accepted.
    """
    await manager.setup()

    mock_mqtt_client.set_power.return_value = 0  # 0 == queued by the library

    result = await manager.send_command(mock_device, "set_power", power_on=True)

    assert result is True
    mock_mqtt_client.set_power.assert_called_with(mock_device, True)


@pytest.mark.asyncio
async def test_send_command_publish_error(
    manager, mock_mqtt_client, mock_device
):
    """Test that a wrapped MqttPublishError is reported as a failure."""
    from nwp500 import MqttPublishError

    await manager.setup()

    mock_mqtt_client.set_power.side_effect = MqttPublishError(
        "Failed to publish to MQTT topic", retriable=True
    )

    result = await manager.send_command(mock_device, "set_power", power_on=True)

    assert result is False


@pytest.mark.asyncio
async def test_send_command_failure(manager, mock_mqtt_client, mock_device):
    """Test sending a command that fails."""
    await manager.setup()

    mock_mqtt_client.set_power.side_effect = RuntimeError("Some error")

    result = await manager.send_command(mock_device, "set_power", power_on=True)

    assert result is False
    mock_mqtt_client.set_power.assert_called_with(mock_device, True)


@pytest.mark.asyncio
async def test_is_connected(manager, mock_mqtt_client):
    """Test is_connected property."""
    # Initially not connected (no client)
    assert manager.is_connected is False

    await manager.setup()

    # Connected after setup
    mock_mqtt_client.is_connected = True
    assert manager.is_connected is True

    # Disconnected
    mock_mqtt_client.is_connected = False
    assert manager.is_connected is False


@pytest.mark.asyncio
async def test_force_reconnect(manager, mock_mqtt_client, mock_device):
    """Test forced reconnection."""
    await manager.setup()

    # Initial subscription
    await manager.subscribe_device(mock_device)

    result = await manager.force_reconnect([mock_device])

    assert result is True, "force_reconnect failed"

    # The first client created in setup() should have disconnect called during reconnect
    assert len(mock_mqtt_client.all_clients) >= 2
    first_client = mock_mqtt_client.all_clients[0]
    first_client.disconnect.assert_called()

    # Total connect calls across both clients (1 from setup + 1 from reconnect)
    # Each client's connect is called once
    total_connects = sum(
        c.connect.call_count for c in mock_mqtt_client.all_clients
    )
    assert total_connects == 2

    # Verify re-subscription
    # subscribe_device calls subscribe_device_status and subscribe_device_feature
    # Initial subscription (1) + Re-subscription (1) = 2
    # The last client should have these calls from re-subscription
    last_client = mock_mqtt_client.all_clients[-1]
    assert last_client.subscribe_device_status.call_count >= 1
    assert last_client.subscribe_device_feature.call_count >= 1


@pytest.mark.asyncio
async def test_force_reconnect_retries_on_setup_failure(
    manager, mock_mqtt_client, mock_device
):
    """Test that force_reconnect retries when setup fails temporarily."""
    await manager.setup()
    await manager.subscribe_device(mock_device)

    # Simulate setup failures on first attempt, then delegate to real setup
    setup_call_count = 0

    async def setup_with_one_failure():
        nonlocal setup_call_count
        setup_call_count += 1
        if setup_call_count == 1:
            raise ConnectionError("Transient auth service failure")
        # Call the real setup to actually reconnect
        return await original_setup()

    # Wrap setup to fail once then succeed with real reconnection
    original_setup = manager.setup
    manager.setup = setup_with_one_failure

    # Create a task with generous timeout to account for backoff delays
    try:
        result = await asyncio.wait_for(
            manager.force_reconnect([mock_device]), timeout=15
        )
        assert result is True, "force_reconnect should succeed after retry"
        assert setup_call_count == 2, (
            "Setup should be called twice (1 failure + 1 success)"
        )
        # Verify MQTT client was actually reconnected
        assert manager.mqtt_client is not None, (
            "MQTT client should be connected"
        )
    finally:
        manager.setup = original_setup


@pytest.mark.asyncio
async def test_force_reconnect_resets_backoff_on_success(
    manager, mock_mqtt_client, mock_device
):
    """Test that reconnection attempt counter resets on successful reconnect."""
    await manager.setup()
    await manager.subscribe_device(mock_device)

    # Manually increment attempt counter to simulate prior failures
    manager._reconnect_attempts = 3

    result = await manager.force_reconnect([mock_device])

    assert result is True
    # Counter should be reset to 0 after successful reconnection
    assert manager._reconnect_attempts == 0


@pytest.mark.asyncio
async def test_force_reconnect_handles_cancellation(
    manager, mock_mqtt_client, mock_device
):
    """Test that force_reconnect properly handles task cancellation from setup."""
    await manager.setup()
    await manager.subscribe_device(mock_device)

    # Mock setup to hang indefinitely so we can cancel it during setup
    async def setup_hang():
        await asyncio.sleep(10)
        return True

    original_setup = manager.setup
    manager.setup = setup_hang

    try:
        task = asyncio.create_task(manager.force_reconnect([mock_device]))
        # Use patch to set backoff delays to 0 so task reaches setup() immediately
        with patch(
            "custom_components.nwp500.mqtt_manager._RECONNECT_BACKOFF_DELAYS",
            [0, 0, 0, 0, 0],
        ):
            await asyncio.sleep(0.1)  # Let task reach setup()
            task.cancel()

            try:
                await task
                pytest.fail("Task should have been cancelled")
            except asyncio.CancelledError:
                pass  # Expected - CancelledError should propagate
    finally:
        manager.setup = original_setup

    # Verify callbacks are registered with the client
    assert mock_mqtt_client.on.call_count >= 3

    # Verify specific event registrations
    calls = [c[0][0] for c in mock_mqtt_client.on.call_args_list]
    assert any(
        c in ("connection_interrupted", "CONNECTION_INTERRUPTED") for c in calls
    )
    assert any(c in ("connection_resumed", "CONNECTION_RESUMED") for c in calls)
    assert "reconnection_failed" in calls


def test_reconnection_failed_callback_is_scheduled(
    mock_auth_client, mock_mqtt_client, reconnection_failed_callback
):
    """Fatal library reconnect failures are forwarded to the coordinator."""
    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
        on_reconnection_failed=reconnection_failed_callback,
    )

    manager._on_reconnection_failed(4)

    manager.loop.call_soon_threadsafe.assert_called_once_with(
        reconnection_failed_callback, 4
    )


@pytest.mark.asyncio
async def test_request_status_consecutive_timeouts(
    manager, mock_mqtt_client, mock_device
):
    """Test consecutive timeouts logic in request_status."""
    await manager.setup()

    # 1. Success should reset counter
    manager.consecutive_timeouts = 5
    await manager.request_status(mock_device)
    assert manager.consecutive_timeouts == 0

    # 2. Failure should increment counter
    mock_mqtt_client.request_device_status.side_effect = RuntimeError("Timeout")
    await manager.request_status(mock_device)
    assert manager.consecutive_timeouts == 1

    # 3. Another failure should increment again
    await manager.request_status(mock_device)
    assert manager.consecutive_timeouts == 2

    # 4. Success should reset again
    mock_mqtt_client.request_device_status.side_effect = None
    await manager.request_status(mock_device)
    assert manager.consecutive_timeouts == 0


@pytest.mark.asyncio
async def test_send_command_update_reservations(
    manager, mock_mqtt_client, mock_device
):
    """Test sending update_reservations command."""
    await manager.setup()

    reservations = [
        {"enable": 1, "week": 42, "hour": 6, "min": 30, "mode": 3, "param": 120}
    ]

    result = await manager.send_command(
        mock_device,
        "update_reservations",
        reservations=reservations,
        enabled=True,
    )

    assert result is True
    mock_mqtt_client.update_reservations.assert_called_once_with(
        mock_device, reservations, enabled=True
    )


@pytest.mark.asyncio
async def test_send_command_request_reservations(
    manager, mock_mqtt_client, mock_device
):
    """Test sending request_reservations command."""
    await manager.setup()

    result = await manager.send_command(mock_device, "request_reservations")

    assert result is True
    mock_mqtt_client.request_reservations.assert_called_once_with(mock_device)


@pytest.mark.asyncio
async def test_is_connected_property(manager):
    """Test is_connected property."""
    # Not connected initially
    assert manager.is_connected is False

    # After setup, should be connected
    with patch("nwp500.NavienMqttClient"):
        await manager.setup()
        assert manager.mqtt_client is not None


@pytest.mark.asyncio
async def test_request_device_info(manager, mock_mqtt_client, mock_device):
    """Test request_device_info sends device info request."""
    await manager.setup()

    await manager.request_device_info(mock_device)

    mock_mqtt_client.ensure_device_info_cached.assert_called_once_with(
        mock_device
    )


def test_connected_since_property(manager):
    """Test connected_since and manager properties."""
    # Initially None
    assert manager.connected_since is None
    assert manager.is_connected is False
    assert manager.consecutive_timeouts == 0
    assert manager.diagnostics is None
    assert manager.reconnection_in_progress is False

    # Set a value
    manager.connected_since = 1234567890.0
    assert manager.connected_since == 1234567890.0


@pytest.mark.asyncio
async def test_request_device_info_no_client(mock_auth_client, mock_device):
    """Test request_device_info does nothing when no MQTT client."""
    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
    )

    # Should return early when mqtt_client is None
    await manager.request_device_info(mock_device)

    # No error should be raised
    assert manager.mqtt_client is None


@pytest.mark.asyncio
async def test_setup_ensures_valid_token(manager, mock_mqtt_client):
    """Test that setup calls ensure_valid_token before creating MQTT client."""
    # Mock ensure_valid_token
    manager.auth_client.ensure_valid_token = AsyncMock()

    await manager.setup()

    # Verify ensure_valid_token was called at least once during setup
    # (called in setup() and again in connect())
    assert manager.auth_client.ensure_valid_token.call_count >= 1
    # Verify MQTT client was created and connected after token refresh
    assert manager.mqtt_client is not None
    mock_mqtt_client.connect.assert_called_once()


@pytest.mark.asyncio
async def test_setup_client_id_includes_ha_instance_id(
    mock_auth_client, mock_mqtt_client
):
    """Client ID must embed both user_seq and ha_instance_id for per-installation uniqueness."""
    mock_auth_client.current_user = MagicMock()
    mock_auth_client.current_user.user_seq = 36283

    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
        ha_instance_id="abcd1234ef567890",
    )
    await manager.setup()

    assert mock_mqtt_client.config is not None
    assert mock_mqtt_client.config.client_id == "navien-ha-36283-abcd1234"
    assert mock_mqtt_client.config.clean_session is False


@pytest.mark.asyncio
async def test_setup_client_id_falls_back_without_ha_instance_id(
    mock_auth_client, mock_mqtt_client
):
    """Client ID falls back to user_seq only when ha_instance_id is not supplied."""
    mock_auth_client.current_user = MagicMock()
    mock_auth_client.current_user.user_seq = 36283

    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
    )
    await manager.setup()

    assert mock_mqtt_client.config is not None
    assert mock_mqtt_client.config.client_id == "navien-ha-36283"
    assert mock_mqtt_client.config.clean_session is False


@pytest.mark.asyncio
async def test_setup_clean_session_false_without_user_seq(
    mock_auth_client, mock_mqtt_client
):
    """clean_session=False must be set even when no user info is available."""
    mock_auth_client.current_user = None

    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
    )
    await manager.setup()

    assert mock_mqtt_client.config is not None
    assert mock_mqtt_client.config.clean_session is False


def _error_from_module(module_name, exc):
    """Return `exc` carrying a traceback raised from `module_name`.

    The manager distinguishes an AWS SDK stale-module condition from an
    ordinary bug by where the exception came from, so tests need a
    realistic traceback rather than a bare exception object.
    """
    namespace = {"__name__": module_name, "_exc": exc}
    exec("def _raise():\n    raise _exc", namespace)  # noqa: S102
    try:
        namespace["_raise"]()
    except type(exc) as raised:
        return raised
    raise AssertionError("expected the exception to be raised")


class TestSdkUpgradedDuringStartup:
    """An AttributeError from awscrt means stale modules, not a bad config.

    Home Assistant can install a newer awscrt/awsiotsdk while it is already
    running. The process then holds the old modules alongside ones imported
    after the upgrade, and new code reads attributes the old classes never
    defined -- observed in the wild as:

        'ClientTlsContext' object has no attribute '_certificate_source'

    Only a restart clears it, so the log has to say that rather than surface
    a bare AttributeError.
    """

    @staticmethod
    def _patch_failing_client(monkeypatch, error):
        """Patch the client factory so connect() raises `error`."""

        class FailingClient:
            def __init__(self, *args, **kwargs):
                self.is_connected = False
                self.client_id = "test-client-id"
                self.on = MagicMock()
                self.off = MagicMock()
                self.connect = AsyncMock(side_effect=error)
                self.disconnect = AsyncMock()

        diagnostics = MagicMock()
        diagnostics.record_connection_success = AsyncMock()
        diagnostics.record_connection_drop = AsyncMock()

        monkeypatch.setattr("nwp500.NavienMqttClient", FailingClient)
        monkeypatch.setattr(
            "nwp500.MqttDiagnosticsCollector",
            MagicMock(return_value=diagnostics),
        )

    @pytest.mark.asyncio
    async def test_reports_restart_guidance(self, manager, monkeypatch, caplog):
        """The real-world failure is logged with an actionable message."""
        self._patch_failing_client(
            monkeypatch,
            _error_from_module(
                "awscrt.aws_iot_metrics",
                AttributeError(
                    "'ClientTlsContext' object has no attribute "
                    "'_certificate_source'"
                ),
            ),
        )

        with caplog.at_level(logging.ERROR):
            connected = await manager.setup()

        assert connected is False
        assert "Restart Home Assistant" in caplog.text
        assert "awscrt/awsiotsdk" in caplog.text
        # The underlying error is still reported, not swallowed
        assert "_certificate_source" in caplog.text

    @pytest.mark.asyncio
    async def test_detects_a_different_sdk_attribute(
        self, manager, monkeypatch, caplog
    ):
        """Detection is by origin, so a future SDK break is covered too."""
        self._patch_failing_client(
            monkeypatch,
            _error_from_module(
                "awsiot.mqtt_connection_builder",
                AttributeError(
                    "'Foo' object has no attribute '_something_new'"
                ),
            ),
        )

        with caplog.at_level(logging.ERROR):
            connected = await manager.setup()

        assert connected is False
        assert "Restart Home Assistant" in caplog.text

    @pytest.mark.asyncio
    async def test_our_own_attribute_error_is_not_blamed_on_the_sdk(
        self, manager, monkeypatch, caplog
    ):
        """An AttributeError from our code is a bug, not a stale module.

        Telling users to restart would send them chasing the wrong thing.
        """
        self._patch_failing_client(
            monkeypatch,
            _error_from_module(
                "custom_components.nwp500.mqtt_manager",
                AttributeError("'NoneType' object has no attribute 'connect'"),
            ),
        )

        with caplog.at_level(logging.DEBUG):
            connected = await manager.setup()

        assert connected is False
        assert "'NoneType' object has no attribute 'connect'" in caplog.text
        assert "Restart Home Assistant" not in caplog.text

    @pytest.mark.asyncio
    async def test_other_failures_keep_the_plain_warning(
        self, manager, monkeypatch, caplog
    ):
        """A normal connection failure must not blame the SDK version."""
        self._patch_failing_client(monkeypatch, RuntimeError("network down"))

        with caplog.at_level(logging.WARNING):
            connected = await manager.setup()

        assert connected is False
        assert "network down" in caplog.text
        assert "Restart Home Assistant" not in caplog.text


@pytest.mark.asyncio
async def test_energy_usage_callback_routes_a_parsed_response(
    mock_auth_client, mock_mqtt_client, mock_device
):
    """The subscription's callback dumps the model and names the device.

    The reply topic is shared by every device on this MQTT client, so the
    MAC comes from the closure the subscription was made with -- which is
    what the coordinator logs the report against.
    """
    received: list[tuple[str, dict]] = []
    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
        on_energy_usage=lambda mac, response: received.append((mac, response)),
    )
    await manager.setup()
    await manager.subscribe_device(mock_device)

    callback = mock_mqtt_client.subscribe_energy_usage.call_args[0][1]
    usage = MagicMock()
    usage.model_dump.return_value = {"total": {"heat_pump_usage": 5}}

    callback(usage)

    assert received == [
        (mock_device.device_info.mac_address, {"total": {"heat_pump_usage": 5}})
    ]


@pytest.mark.asyncio
async def test_an_unreadable_energy_payload_is_dropped(
    mock_auth_client, mock_mqtt_client, mock_device
):
    """Delivering {} would be worse than delivering nothing.

    An empty dict satisfies the coordinator's checks and builds a report
    of all zeros, which reads as measured data. Dropping it lets the
    request time out and say the device did not report.
    """
    received: list[tuple[str, dict]] = []
    manager = NWP500MqttManager(
        hass_loop=MagicMock(),
        auth_client=mock_auth_client,
        on_status_update=MagicMock(),
        on_feature_update=MagicMock(),
        on_energy_usage=lambda mac, response: received.append((mac, response)),
    )
    await manager.setup()
    await manager.subscribe_device(mock_device)

    callback = mock_mqtt_client.subscribe_energy_usage.call_args[0][1]

    callback(object())

    assert received == []


@pytest.mark.asyncio
async def test_send_command_request_energy_usage(
    manager, mock_mqtt_client, mock_device
):
    """The command branch reaches the library with the period it was given.

    The coordinator's own tests replace `async_send_command`, so without
    this a wrong method name or argument shape would pass the suite.
    """
    await manager.setup()

    result = await manager.send_command(
        mock_device, "request_energy_usage", year=2026, months=[7, 8]
    )

    assert result is True
    mock_mqtt_client.request_energy_usage.assert_called_once_with(
        mock_device, year=2026, months=[7, 8]
    )


@pytest.mark.asyncio
async def test_request_energy_usage_coerces_the_period(
    manager, mock_mqtt_client, mock_device
):
    """Months arrive from a UI selector as strings; the device wants ints."""
    await manager.setup()

    await manager.send_command(
        mock_device, "request_energy_usage", year="2026", months=("7",)
    )

    mock_mqtt_client.request_energy_usage.assert_called_once_with(
        mock_device, year=2026, months=[7]
    )


@pytest.mark.asyncio
async def test_force_reconnect_gives_up_on_repeated_credential_rejection(
    manager, mock_mqtt_client, mock_device
):
    """A cause retrying cannot fix must reach the user, not loop forever.

    When the credentials are rejected, connect() fails identically on every
    attempt. Retrying without limit looked exactly like working: warnings
    every 60s, no reauth prompt, and nothing that ever escalated.
    """
    from custom_components.nwp500 import mqtt_manager as mm

    failed = MagicMock()
    manager._on_reconnection_failed_callback = failed
    manager.loop = MagicMock()

    await manager.setup()

    async def rejected():
        manager._last_failure_was_credentials = True
        return False

    manager.setup = AsyncMock(side_effect=rejected)

    with patch.object(mm, "_RECONNECT_BACKOFF_DELAYS", [0.0]):
        result = await asyncio.wait_for(
            manager.force_reconnect([mock_device]), timeout=15
        )

    assert result is False
    assert manager.setup.await_count == mm._MAX_CREDENTIAL_FAILURES
    # Reported through the loop, the way the library's own failure event is.
    manager.loop.call_soon_threadsafe.assert_called_once_with(
        failed, mm._MAX_CREDENTIAL_FAILURES
    )
    assert manager._reconnect_attempts == 0
    assert manager.reconnection_in_progress is False


@pytest.mark.asyncio
async def test_force_reconnect_keeps_retrying_through_a_network_outage(
    manager, mock_mqtt_client, mock_device
):
    """An outage must not be reported as an authentication failure.

    setup()/connect() return False for outages, brokers refusing
    connections and AWS SDK errors just as they do for bad credentials.
    Escalating on attempt count alone would prompt the user to replace
    credentials that are perfectly valid, and stop retrying the one thing
    that does recover on its own.
    """
    from custom_components.nwp500 import mqtt_manager as mm

    failed = MagicMock()
    manager._on_reconnection_failed_callback = failed
    manager.loop = MagicMock()

    await manager.setup()

    original_setup = manager.setup
    attempts = 0

    async def down_then_back():
        nonlocal attempts
        attempts += 1
        if attempts <= mm._MAX_CREDENTIAL_FAILURES * 3:
            # A transport failure: not a credential rejection.
            manager._last_failure_was_credentials = False
            return False
        return await original_setup()

    manager.setup = down_then_back

    with patch.object(mm, "_RECONNECT_BACKOFF_DELAYS", [0.0]):
        result = await asyncio.wait_for(
            manager.force_reconnect([mock_device]), timeout=15
        )

    # Kept trying well past the credential threshold, then recovered.
    assert result is True
    assert attempts == mm._MAX_CREDENTIAL_FAILURES * 3 + 1
    failed.assert_not_called()
    manager.loop.call_soon_threadsafe.assert_not_called()


@pytest.mark.asyncio
async def test_force_reconnect_resets_credential_run_on_a_transport_failure(
    manager, mock_mqtt_client, mock_device
):
    """Only *consecutive* rejections escalate.

    A credential failure followed by an outage is not evidence the password
    is wrong, so the run restarts rather than accumulating toward reauth.
    """
    from custom_components.nwp500 import mqtt_manager as mm

    failed = MagicMock()
    manager._on_reconnection_failed_callback = failed
    manager.loop = MagicMock()

    await manager.setup()

    original_setup = manager.setup
    # Alternate rejection / outage so no run ever reaches the threshold.
    pattern = [True, False] * (mm._MAX_CREDENTIAL_FAILURES * 2)
    calls = 0

    async def alternating():
        nonlocal calls
        if calls < len(pattern):
            manager._last_failure_was_credentials = pattern[calls]
            calls += 1
            return False
        return await original_setup()

    manager.setup = alternating

    with patch.object(mm, "_RECONNECT_BACKOFF_DELAYS", [0.0]):
        result = await asyncio.wait_for(
            manager.force_reconnect([mock_device]), timeout=15
        )

    assert result is True
    failed.assert_not_called()


def test_only_a_rejected_login_counts_as_a_credential_failure():
    """The classifier is what keeps an outage off the reauth path.

    Only InvalidCredentialsError carries the invalid-login contract. In
    nwp500-python 9.3.1 it is raised from one place -- sign_in(), on a 401
    or an "invalid"/"unauthorized" message -- while every other non-200 from
    that same response becomes a bare AuthenticationError. Counting the
    broader types would answer a Navien outage with a reauth prompt and
    stop reconnecting.
    """
    from nwp500.exceptions import (
        AuthenticationError,
        InvalidCredentialsError,
        MqttCredentialsError,
        TokenRefreshError,
    )

    from custom_components.nwp500.mqtt_manager import _is_credential_failure

    assert _is_credential_failure(InvalidCredentialsError("nope")) is True

    # Raised when tokens or AWS credentials were missing when the broker
    # needed them -- ordinary at token expiry and during a reconnect.
    assert _is_credential_failure(MqttCredentialsError("no tokens")) is False

    # The library's catch-all for any other non-200, for unparseable
    # responses, and for state errors. It defaults to retriable=False, so
    # that flag cannot be used to tell them apart.
    service_error = AuthenticationError("Authentication failed: 500")
    assert getattr(service_error, "retriable", None) is False
    assert _is_credential_failure(service_error) is False

    malformed = AuthenticationError("Invalid response format: expecting value")
    assert _is_credential_failure(malformed) is False

    assert _is_credential_failure(TokenRefreshError("refresh failed")) is False
    assert _is_credential_failure(OSError("network unreachable")) is False
    assert _is_credential_failure(TimeoutError()) is False


@pytest.mark.asyncio
async def test_credential_run_does_not_survive_a_successful_reconnect(
    manager, mock_mqtt_client, mock_device
):
    """A success ends the run; it must not carry into a later outage.

    Two rejections then a success used to leave the count part-way to the
    threshold, so the first rejection of some later outage escalated
    straight to reauth.
    """
    from custom_components.nwp500 import mqtt_manager as mm

    failed = MagicMock()
    manager._on_reconnection_failed_callback = failed
    manager.loop = MagicMock()

    await manager.setup()
    original_setup = manager.setup

    rejections = mm._MAX_CREDENTIAL_FAILURES - 1
    calls = 0

    async def reject_then_succeed():
        nonlocal calls
        calls += 1
        if calls <= rejections:
            manager._last_failure_was_credentials = True
            return False
        return await original_setup()

    manager.setup = reject_then_succeed

    with patch.object(mm, "_RECONNECT_BACKOFF_DELAYS", [0.0]):
        assert await asyncio.wait_for(
            manager.force_reconnect([mock_device]), timeout=15
        )

    assert manager._consecutive_credential_failures == 0
    failed.assert_not_called()


@pytest.mark.asyncio
async def test_a_setup_failure_before_connect_is_not_counted_as_credentials(
    manager, mock_mqtt_client, mock_device
):
    """setup() can fail before connect() ever classifies anything.

    The previous attempt's verdict must not be reused, or a single real
    rejection followed by unrelated setup failures would reach the
    threshold and prompt for reauthentication.
    """
    from custom_components.nwp500 import mqtt_manager as mm

    failed = MagicMock()
    manager._on_reconnection_failed_callback = failed
    manager.loop = MagicMock()

    await manager.setup()
    original_setup = manager.setup

    calls = 0

    async def one_rejection_then_setup_failures():
        nonlocal calls
        calls += 1
        if calls == 1:
            # A genuine credential rejection, classified by connect().
            manager._last_failure_was_credentials = True
            return False
        if calls <= mm._MAX_CREDENTIAL_FAILURES * 2:
            # setup() bailing out before connect(): it classifies nothing,
            # so the flag must already have been cleared for this attempt.
            return False
        return await original_setup()

    manager.setup = one_rejection_then_setup_failures

    with patch.object(mm, "_RECONNECT_BACKOFF_DELAYS", [0.0]):
        assert await asyncio.wait_for(
            manager.force_reconnect([mock_device]), timeout=15
        )

    failed.assert_not_called()
    manager.loop.call_soon_threadsafe.assert_not_called()


@pytest.mark.asyncio
async def test_connect_marks_a_rejected_login_as_a_credential_failure(
    manager, mock_mqtt_client, mock_auth_client
):
    """connect() is where the classification actually happens."""
    from nwp500.exceptions import InvalidCredentialsError

    await manager.setup()
    mock_auth_client.ensure_valid_token = AsyncMock(
        side_effect=InvalidCredentialsError("Invalid credentials: bad login")
    )

    assert await manager.connect() is False
    assert manager._last_failure_was_credentials is True


@pytest.mark.asyncio
async def test_connect_does_not_blame_credentials_for_a_service_outage(
    manager, mock_mqtt_client, mock_auth_client
):
    """A Navien outage must not be recorded as a rejected login.

    The library turns any non-401 sign-in failure into a bare
    AuthenticationError with retriable=False, so this is what a 500 from
    the auth service looks like from here.
    """
    from nwp500.exceptions import AuthenticationError

    await manager.setup()
    mock_auth_client.ensure_valid_token = AsyncMock(
        side_effect=AuthenticationError("Authentication failed: 500")
    )

    assert await manager.connect() is False
    assert manager._last_failure_was_credentials is False
