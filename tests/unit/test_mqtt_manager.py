"""Tests for NWP500MqttManager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from awscrt.exceptions import AwsCrtError

from custom_components.nwp500.mqtt_manager import NWP500MqttManager


@pytest.fixture
def mock_auth_client():
    """Mock NavienAuthClient."""
    client = AsyncMock()
    client.get_access_token = AsyncMock(return_value="test_token")
    return client


@pytest.fixture
def mock_mqtt_client():
    """Mock NavienMqttClient."""
    with patch("nwp500.NavienMqttClient") as mock:
        client = MagicMock()
        client.connect = AsyncMock(return_value=True)
        client.disconnect = AsyncMock()
        client.subscribe_device_status = AsyncMock()
        client.subscribe_device_feature = AsyncMock()
        client.start_periodic_device_status_requests = AsyncMock()
        client.start_periodic_device_info_requests = AsyncMock()
        client.request_device_info = AsyncMock()
        client.request_device_status = AsyncMock()
        client.set_power = AsyncMock()
        client.set_dhw_temperature = AsyncMock()
        client.set_dhw_mode = AsyncMock()
        client.stop_all_periodic_tasks = AsyncMock()
        client.reset_reconnect = AsyncMock()

        mock.return_value = client
        yield client


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

    mock_mqtt_client.disconnect.assert_called()
    # Initial connect (1) + Reconnect (1) = 2
    assert mock_mqtt_client.connect.call_count == 2

    # Verify re-subscription
    # subscribe_device calls subscribe_device_status and subscribe_device_feature
    # Initial subscription (1) + Re-subscription (1) = 2
    assert mock_mqtt_client.subscribe_device_status.call_count == 2
    assert mock_mqtt_client.subscribe_device_feature.call_count == 2


@pytest.mark.asyncio
async def test_callbacks(manager, mock_mqtt_client):
    """Test that callbacks are registered correctly."""
    await manager.setup()

    # Verify callbacks are registered with the client
    # Since we can't easily check 'on' calls without more mocking of the client's internal structure
    # or inspecting the mock calls to 'on'.

    # Check that 'on' was called for various events (now 7 events including diagnostics)
    assert mock_mqtt_client.on.call_count >= 7

    # Verify specific event registrations
    calls = [c[0][0] for c in mock_mqtt_client.on.call_args_list]
    assert "device_status_update" in calls
    assert "device_feature_update" in calls
    assert "connection_lost" in calls
    assert "connection_restored" in calls
    assert "reconnection_failed" in calls
    assert "connection_interrupted" in calls
    assert "connection_resumed" in calls


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

    # Mock the update_reservations method
    mock_mqtt_client.update_reservations = AsyncMock()

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

    # Mock the request_reservations method
    mock_mqtt_client.request_reservations = AsyncMock()

    result = await manager.send_command(mock_device, "request_reservations")

    assert result is True
    mock_mqtt_client.request_reservations.assert_called_once_with(mock_device)
