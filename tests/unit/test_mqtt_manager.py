"""Tests for NWP500MqttManager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from awscrt.exceptions import AwsCrtError

from custom_components.nwp500.mqtt_manager import (
    NWP500MqttManager,
    get_aws_error_name,
)


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
            self.request_tou_settings = AsyncMock()
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
    """Test sending a command that gets queued due to clean session."""
    await manager.setup()

    # Simulate AwsCrtError for clean session
    # We need to mock the exception object to have the name attribute
    error = AwsCrtError(
        code=0,
        name="AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION",
        message="Clean session",
    )
    # If name is not set by constructor (depends on version), force it
    error.name = "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION"

    mock_mqtt_client.set_power.side_effect = error

    result = await manager.send_command(mock_device, "set_power", power_on=True)

    assert result is True  # Should return True as it's queued
    mock_mqtt_client.set_power.assert_called_with(mock_device, True)


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

    # Simulate setup failures on first attempt, then success
    setup_call_count = 0

    async def setup_with_one_failure():
        nonlocal setup_call_count
        setup_call_count += 1
        if setup_call_count == 1:
            raise ConnectionError("Transient auth service failure")
        return True

    # Mock setup to fail once then succeed
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
    """Test that force_reconnect properly handles task cancellation."""
    await manager.setup()
    await manager.subscribe_device(mock_device)

    # Mock setup to hang indefinitely so we can cancel it
    async def setup_hang():
        await asyncio.sleep(10)
        return True

    original_setup = manager.setup
    manager.setup = setup_hang

    try:
        task = asyncio.create_task(manager.force_reconnect([mock_device]))
        await asyncio.sleep(0.1)  # Let task start
        task.cancel()

        try:
            await task
            pytest.fail("Task should have been cancelled")
        except asyncio.CancelledError:
            pass  # Expected
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


def test_get_aws_error_name_with_awscrterror():
    """Test get_aws_error_name extracts name from AwsCrtError."""
    error = AwsCrtError(
        code=0,
        name="AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION",
        message="Test error",
    )
    error.name = "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION"

    result = get_aws_error_name(error)

    assert result == "AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION"


def test_get_aws_error_name_with_regular_exception():
    """Test get_aws_error_name returns empty string for non-AWS errors."""
    error = RuntimeError("Regular error")

    result = get_aws_error_name(error)

    assert result == ""


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
