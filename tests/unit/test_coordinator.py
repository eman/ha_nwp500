"""Tests for NWP500DataUpdateCoordinator."""

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
