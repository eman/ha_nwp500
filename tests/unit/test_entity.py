"""Tests for entity.py module."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.nwp500.entity import NWP500Entity


class TestNWP500Entity:
    """Tests for NWP500Entity base class."""

    async def test_entity_initialization(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test entity initialization."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        assert entity.coordinator == mock_coordinator
        assert entity.mac_address == mac_address
        assert entity.device == mock_device

    async def test_device_info(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test device_info property."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        device_info = entity.device_info

        assert device_info is not None
        assert device_info["identifiers"] == {("nwp500", mac_address)}
        # Name includes location info: "Test Water Heater (Test City, CA)"
        assert "Test Water Heater" in device_info["name"]
        assert device_info["model"] == "NWP500"
        assert device_info["manufacturer"] == "Navien"

    async def test_status_property(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test _status property returns device status."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        status = entity._status

        assert status == mock_device_status

    async def test_status_property_missing_device(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test _status property returns None for missing device."""
        mac_address = "FF:FF:FF:FF:FF:FF"  # Non-existent device
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        status = entity._status

        assert status is None

    async def test_get_status_attrs(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test _get_status_attrs batch fetches attributes."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        attrs = entity._get_status_attrs(
            "dhwTemperature",
            "currentInstPower",
            "errorCode",
        )

        assert attrs["dhwTemperature"] == 120.0
        assert attrs["currentInstPower"] == 1200
        assert attrs["errorCode"] == 0

    async def test_get_status_attrs_missing_attribute(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test _get_status_attrs returns None for missing attrs."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        # Remove an attribute to test missing case
        delattr(mock_device_status, "dhwTemperature")

        attrs = entity._get_status_attrs("dhwTemperature", "errorCode")

        assert attrs["dhwTemperature"] is None
        assert attrs["errorCode"] == 0
