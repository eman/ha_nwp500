"""Tests for NWP500DataUpdateCoordinator."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.nwp500.coordinator import NWP500DataUpdateCoordinator


@pytest.fixture
def mock_entry():
    """Mock ConfigEntry."""
    entry = MagicMock()
    entry.options = {}
    entry.data = {"email": "test@example.com", "password": "password"}
    return entry


@pytest.fixture
def coordinator(mock_hass, mock_entry):
    """Create NWP500DataUpdateCoordinator instance."""
    with (
        patch(
            "custom_components.nwp500.coordinator.DataUpdateCoordinator.__init__"
        ),
        patch(
            "custom_components.nwp500.coordinator.NWP500DataUpdateCoordinator._install_exception_handler"
        ),
    ):
        coordinator = NWP500DataUpdateCoordinator(mock_hass, mock_entry)
        coordinator.hass = mock_hass
        coordinator.data = {}
        return coordinator


def _make_disconnected_mqtt_manager(last_reconnect_offset: float = -9999.0) -> MagicMock:
    """Return a mock mqtt_manager that reports as disconnected."""
    mgr = MagicMock()
    mgr.is_connected = False
    mgr.last_reconnect_time = time.time() + last_reconnect_offset
    mgr.force_reconnect = AsyncMock(return_value=True)
    return mgr


def test_on_device_status_update_schedules_loop_task(coordinator, mock_hass):
    """Test that _on_device_status_update schedules a task in the event loop."""
    mac = "AA:BB:CC:DD:EE:FF"
    status = MagicMock()

    coordinator._on_device_status_update(mac, status)

    # Verify call_soon_threadsafe was called with the handler
    mock_hass.loop.call_soon_threadsafe.assert_called_once()
    args = mock_hass.loop.call_soon_threadsafe.call_args[0]
    assert args[0] == coordinator._handle_status_update_in_loop
    assert args[1] == mac
    assert args[2] == status


def test_handle_status_update_in_loop(coordinator):
    """Test that _handle_status_update_in_loop updates data and notifies listeners."""
    mac = "AA:BB:CC:DD:EE:FF"
    status = MagicMock()
    coordinator.data = {
        mac: {"device": MagicMock(), "status": None, "last_update": None}
    }
    coordinator.async_update_listeners = MagicMock()

    coordinator._handle_status_update_in_loop(mac, status)

    assert coordinator.data[mac]["status"] == status
    assert coordinator.data[mac]["last_update"] is not None
    coordinator.async_update_listeners.assert_called_once()


def test_on_device_feature_update_schedules_loop_task(coordinator, mock_hass):
    """Test that _on_device_feature_update schedules a task in the event loop."""
    mac = "AA:BB:CC:DD:EE:FF"
    feature = MagicMock()

    coordinator._on_device_feature_update(mac, feature)

    # Verify call_soon_threadsafe was called with the handler
    mock_hass.loop.call_soon_threadsafe.assert_called_once()
    args = mock_hass.loop.call_soon_threadsafe.call_args[0]
    assert args[0] == coordinator._handle_feature_update_in_loop
    assert args[1] == mac
    assert args[2] == feature


def test_handle_feature_update_in_loop(coordinator):
    """Test that _handle_feature_update_in_loop updates device_features."""
    mac = "AA:BB:CC:DD:EE:FF"
    feature = MagicMock()
    # Mock model_dump to avoid issues if it's called
    feature.model_dump = MagicMock(return_value={})

    coordinator._handle_feature_update_in_loop(mac, feature)

    assert coordinator.device_features[mac] == feature


@pytest.mark.asyncio
async def test_async_update_data_syncs_unit_system(coordinator, mock_hass):
    """Test that _async_update_data synchronizes the unit system."""
    from homeassistant.const import UnitOfTemperature

    mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS
    coordinator.unit_system = "metric"
    # Set auth_client to mock to avoid _setup_clients() call which triggers network
    coordinator.auth_client = AsyncMock()

    # We need to mock the module that is imported inside the function
    with (
        patch("nwp500.unit_system.set_unit_system") as mock_set_unit_system,
        patch(
            "custom_components.nwp500.coordinator.DataUpdateCoordinator._async_update_data",
            side_effect=lambda: None,
        ),
    ):
        # We can't easily mock the super()._async_update_data() call inside the method if we don't mock the method itself
        # But we want to test the method logic.
        # Since DataUpdateCoordinator._async_update_data is not called in the implementation of NWP500DataUpdateCoordinator._async_update_data
        # (it overrides it completely without calling super), we don't need to patch it.
        pass

    with patch("nwp500.unit_system.set_unit_system") as mock_set_unit_system:
        await coordinator._async_update_data()

        mock_set_unit_system.assert_called_once_with("metric")


# ---------------------------------------------------------------------------
# MQTT disconnection handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_update_returns_cached_data_when_mqtt_disconnected(
    coordinator, mock_hass
):
    """When MQTT is not connected, cached data is returned without sending requests."""
    cached = {"mac1": {"device": MagicMock(), "status": MagicMock(), "last_update": 1.0}}
    coordinator.data = cached
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager()

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        result = await coordinator._async_update_data()

    assert result == cached
    coordinator.mqtt_manager.force_reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_async_update_increments_consecutive_timeouts_when_disconnected(
    coordinator, mock_hass
):
    """Each update cycle while disconnected increments _consecutive_timeouts."""
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager()
    coordinator._consecutive_timeouts = 0

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()
        assert coordinator._consecutive_timeouts == 1

        await coordinator._async_update_data()
        assert coordinator._consecutive_timeouts == 2


@pytest.mark.asyncio
async def test_async_update_triggers_force_reconnect_after_threshold(
    coordinator, mock_hass
):
    """After 3 consecutive disconnected cycles, force_reconnect is triggered."""
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    # last_reconnect far in the past so interval guard passes
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager(last_reconnect_offset=-9999.0)
    coordinator._consecutive_timeouts = 2  # one more will hit threshold
    coordinator._reconnect_task = None

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    coordinator.mqtt_manager.force_reconnect.assert_called_once()
    # Counter resets after triggering reconnect
    assert coordinator._consecutive_timeouts == 0


@pytest.mark.asyncio
async def test_async_update_skips_reconnect_within_min_interval(
    coordinator, mock_hass
):
    """force_reconnect is skipped when last attempt was within MIN_RECONNECT_INTERVAL."""
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    # Simulate a reconnect that happened just 5 seconds ago
    coordinator.mqtt_manager = _make_disconnected_mqtt_manager(last_reconnect_offset=-5.0)
    coordinator._consecutive_timeouts = 2
    coordinator._reconnect_task = None

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    coordinator.mqtt_manager.force_reconnect.assert_not_called()
    # Counter resets to prevent immediate re-trigger
    assert coordinator._consecutive_timeouts == 0


@pytest.mark.asyncio
async def test_async_update_resets_consecutive_timeouts_on_successful_request(
    coordinator, mock_hass
):
    """A successful MQTT request resets _consecutive_timeouts to 0."""
    device = MagicMock()
    device.device_info.mac_address = "aabbcc001122"
    coordinator.devices = [device]
    coordinator.data = {}
    coordinator.auth_client = AsyncMock()
    coordinator._consecutive_timeouts = 5

    mock_mqtt = MagicMock()
    mock_mqtt.is_connected = True
    mock_mqtt.request_status = AsyncMock(return_value=True)
    mock_mqtt.request_device_info = AsyncMock()
    coordinator.mqtt_manager = mock_mqtt

    mock_hass.config.units.temperature_unit = "°F"
    coordinator.unit_system = "us_customary"

    with patch("nwp500.unit_system.set_unit_system"):
        await coordinator._async_update_data()

    assert coordinator._consecutive_timeouts == 0
