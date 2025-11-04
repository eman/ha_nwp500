"""Pytest configuration and shared fixtures for NWP500 tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.nwp500.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN


pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
async def hass(hass: HomeAssistant) -> HomeAssistant:
    """Return Home Assistant instance."""
    return hass


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Create a mock config entry."""
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Test NWP500",
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "test_password",
        },
        options={},
        source="user",
        entry_id="test_entry_id",
        unique_id="AA:BB:CC:DD:EE:FF",
        discovery_keys={},
        subentries_data={},
    )


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
    device.location = location

    return device


@pytest.fixture
def mock_device_status() -> MagicMock:
    """Create a mock device status."""
    status = MagicMock()
    # Temperature values
    status.dhwTemperature = 120.0
    status.tankUpperTemperature = 125.0
    status.tankLowerTemperature = 115.0
    status.dhwTargetTemperatureSetting = 130.0
    status.outsideTemperature = 72.0

    # Operation modes
    status.operationMode = MagicMock()
    status.operationMode.value = 32  # HEAT_PUMP_MODE
    status.dhwOperationSetting = MagicMock()
    status.dhwOperationSetting.value = 1  # HEAT_PUMP

    # Status flags
    status.operationBusy = True
    status.dhwUse = False
    status.freezeProtectionUse = False

    # Power and energy
    status.currentInstPower = 1200
    status.dhwChargePer = 85

    # Error codes
    status.errorCode = 0
    status.subErrorCode = 0

    # Component status
    status.compUse = True
    status.heatUpperUse = False
    status.heatLowerUse = False

    # WiFi
    status.wifiRssi = -45

    return status


@pytest.fixture
def mock_nwp500_auth_client() -> Generator[AsyncMock, None, None]:
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
) -> Generator[AsyncMock, None, None]:
    """Mock the NavienAPIClient."""
    with patch(
        "custom_components.nwp500.config_flow.NavienAPIClient"
    ) as mock_api:
        client = AsyncMock()
        client.list_devices = AsyncMock(return_value=[mock_device])
        mock_api.return_value = client
        yield mock_api


@pytest.fixture
def mock_nwp500_mqtt_client() -> Generator[AsyncMock, None, None]:
    """Mock the NavienMqttClient."""
    with patch(
        "custom_components.nwp500.coordinator.NavienMqttClient"
    ) as mock_mqtt:
        client = AsyncMock()
        client.connect = AsyncMock(return_value=True)
        client.subscribe_device_status = AsyncMock()
        client.subscribe_device_feature = AsyncMock()
        client.start_periodic_device_status_requests = AsyncMock()
        client.start_periodic_device_info_requests = AsyncMock()
        client.request_device_info = AsyncMock()
        client.request_device_status = AsyncMock()
        client.set_power = AsyncMock()
        client.set_dhw_temperature_display = AsyncMock()
        client.set_dhw_mode = AsyncMock()
        client.on = MagicMock()
        mock_mqtt.return_value = client
        yield mock_mqtt


@pytest.fixture
async def mock_coordinator(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_device: MagicMock,
    mock_device_status: MagicMock,
) -> Any:
    """Create a mock coordinator with test data."""
    from custom_components.nwp500.coordinator import (
        NWP500DataUpdateCoordinator,
    )

    coordinator = NWP500DataUpdateCoordinator(hass, mock_config_entry)
    coordinator.devices = [mock_device]
    coordinator.data = {
        mock_device.device_info.mac_address: {
            "device": mock_device,
            "status": mock_device_status,
            "last_update": 1234567890.0,
        }
    }
    return coordinator
