"""Pytest configuration and shared fixtures for NWP500 tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.nwp500.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def set_testing_env() -> Generator[None]:
    """Set TESTING environment variable for all tests."""
    os.environ["TESTING"] = "1"
    yield
    os.environ.pop("TESTING", None)


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Create a mock config entry.

    Uses introspection to support multiple Home Assistant versions.
    The 'subentries_data' parameter was added in Home Assistant 2024.1+.
    """
    import inspect

    sig = inspect.signature(ConfigEntry.__init__)
    kwargs = {
        "version": 1,
        "minor_version": 1,
        "domain": DOMAIN,
        "title": "Test NWP500",
        "data": {
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "test_password",
        },
        "options": {},
        "source": "user",
        "entry_id": "test_entry_id",
        "unique_id": "AA:BB:CC:DD:EE:FF",
        "discovery_keys": {},
    }

    # Home Assistant 2024.1+ includes subentries_data parameter
    if "subentries_data" in sig.parameters:
        kwargs["subentries_data"] = None

    return ConfigEntry(**kwargs)


@pytest.fixture
def mock_device() -> MagicMock:
    """Create a mock NWP500 device."""
    device = MagicMock()

    # Mock device_info with proper attributes
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.model = "NWP500"
    device.device_info.device_name = "Test Water Heater"
    device.device_info.serial_number = "TEST123456"
    device.device_info.device_type = 52  # NWP500 type
    device.device_info.connected = True

    # Mock location with proper attributes (return None for optional fields)
    location = MagicMock()
    location.city = "Test City"
    location.state = "CA"
    location.address = None
    location.latitude = None
    location.longitude = None
    device.location = location

    # Mock device_features - return None to indicate not available
    device.device_features.get.return_value = None

    return device


@pytest.fixture
def mock_device_status() -> MagicMock:
    """Create a mock device status."""
    status = MagicMock()
    # Temperature values
    status.dhw_temperature = 120.0
    status.tank_upper_temperature = 125.0
    status.tank_lower_temperature = 115.0
    status.dhw_target_temperature_setting = 130.0
    status.outside_temperature = 72.0

    # Operation modes
    status.operation_mode = MagicMock()
    status.operation_mode.value = 32  # HEAT_PUMP_MODE
    status.dhw_operation_setting = MagicMock()
    status.dhw_operation_setting.value = 1  # HEAT_PUMP

    # Status flags
    status.operation_busy = True
    status.dhw_use = False
    status.freeze_protection_use = False

    # Power and energy
    status.current_inst_power = 1200
    status.dhw_charge_per = 85

    # Error codes
    status.error_code = 0
    status.sub_error_code = 0

    # Component status
    status.comp_use = True
    status.heat_upper_use = False
    status.heat_lower_use = False

    # WiFi
    status.wifi_rssi = -45

    return status


@pytest.fixture
def mock_nwp500_auth_client() -> Generator[AsyncMock]:
    """Mock the NavienAuthClient."""
    with patch(
        "custom_components.nwp500.config_flow.NavienAuthClient"
    ) as mock_auth:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        mock_auth.return_value = client
        yield mock_auth


@pytest.fixture
def mock_nwp500_api_client(
    mock_device: MagicMock,
) -> Generator[AsyncMock]:
    """Mock the NavienAPIClient."""
    with patch(
        "custom_components.nwp500.config_flow.NavienAPIClient"
    ) as mock_api:
        client = AsyncMock()
        client.list_devices = AsyncMock(return_value=[mock_device])
        mock_api.return_value = client
        yield mock_api


@pytest.fixture
def mock_nwp500_mqtt_client() -> Generator[AsyncMock]:
    """Mock the NavienMqttClient."""
    with patch("nwp500.NavienMqttClient") as mock_mqtt:
        client = AsyncMock()
        client.connect = AsyncMock(return_value=True)
        client.subscribe_device_status = AsyncMock()
        client.subscribe_device_feature = AsyncMock()
        client.start_periodic_device_status_requests = AsyncMock()
        client.start_periodic_device_info_requests = AsyncMock()
        client.request_device_info = AsyncMock()
        client.request_device_status = AsyncMock()
        client.set_power = AsyncMock()
        client.set_dhw_temperature = AsyncMock()
        client.set_dhw_mode = AsyncMock()
        client.on = MagicMock()
        mock_mqtt.return_value = client
        yield mock_mqtt


@pytest.fixture
def mock_coordinator(
    mock_config_entry: ConfigEntry,
    mock_device: MagicMock,
    mock_device_status: MagicMock,
) -> MagicMock:
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.devices = [mock_device]
    coordinator.data = {
        mock_device.device_info.mac_address: {
            "device": mock_device,
            "status": mock_device_status,
            "last_update": 1234567890.0,
        }
    }
    # Mock device_features to return None (no features available)
    coordinator.device_features = MagicMock()
    coordinator.device_features.get.return_value = None
    return coordinator
