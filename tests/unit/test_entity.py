"""Tests for entity.py module."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.nwp500.entity import NWP500Entity


class TestNWP500Entity:
    """Tests for NWP500Entity base class."""

    def test_entity_initialization(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test entity initialization."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        assert entity.coordinator == mock_coordinator
        assert entity.mac_address == mac_address
        assert entity.device == mock_device

    def test_device_info(
        self,
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

    def test_status_property(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test _status property returns device status."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        status = entity._status

        assert status == mock_device_status

    def test_status_property_missing_device(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test _status property returns None for missing device."""
        mac_address = "FF:FF:FF:FF:FF:FF"  # Non-existent device
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        status = entity._status

        assert status is None

    def test_get_status_attrs(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test _get_status_attrs batch fetches attributes."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        attrs = entity._get_status_attrs(
            "dhw_temperature",
            "current_inst_power",
            "error_code",
        )

        assert attrs["dhw_temperature"] == 120.0
        assert attrs["current_inst_power"] == 1200
        assert attrs["error_code"] == 0

    def test_get_status_attrs_missing_attribute(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test _get_status_attrs returns None for missing attrs."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        # Remove an attribute to test missing case
        delattr(mock_device_status, "dhw_temperature")

        attrs = entity._get_status_attrs("dhw_temperature", "error_code")

        assert attrs["dhw_temperature"] is None
        assert attrs["error_code"] == 0
