"""Tests for entity.py module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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

    def test_device_info_with_features(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test device_info property with device features."""
        mac_address = mock_device.device_info.mac_address

        # Mock device features
        mock_feature = MagicMock()
        mock_feature.controller_serial_number = "SN123456"
        mock_feature.controller_sw_version = "1.2.3"
        mock_feature.wifi_sw_version = "4.5.6"

        # Mock VolumeCode enum (80 gallon tank = code 67)
        mock_volume_code = MagicMock()
        mock_volume_code.value = 67
        mock_feature.volume_code = mock_volume_code

        mock_coordinator.device_features.get.return_value = mock_feature

        # Mock VOLUME_CODE_TEXT
        with patch.dict(
            "sys.modules",
            {
                "nwp500.enums": MagicMock(
                    VOLUME_CODE_TEXT={mock_volume_code: "80 gallons"}
                )
            },
        ):
            entity = NWP500Entity(mock_coordinator, mac_address, mock_device)
            device_info = entity.device_info

            assert device_info is not None
            assert device_info["model"] == "NWP500"
            assert device_info["manufacturer"] == "Navien"
            assert device_info["serial_number"] == "SN123456"
            assert device_info["hw_version"] == "80 gallons"
            assert device_info["sw_version"] == "1.2.3.4.5.6"
            assert (
                device_info["configuration_url"]
                == f"https://app.naviensmartcontrol.com/device/{mac_address}"
            )

    def test_device_info_partial_firmware(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test device_info with only controller firmware (no wifi)."""
        mac_address = mock_device.device_info.mac_address

        # Mock device features with only controller firmware
        mock_feature = MagicMock()
        mock_feature.controller_serial_number = "SN789"
        mock_feature.controller_sw_version = "2.0.0"
        mock_feature.wifi_sw_version = None
        mock_feature.volume_code = None
        mock_coordinator.device_features.get.return_value = mock_feature

        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)
        device_info = entity.device_info

        assert device_info["model"] == "NWP500"  # No volume code
        assert device_info["sw_version"] == "2.0.0"  # Only controller version

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

    def test_get_status_attrs_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test _get_status_attrs returns empty dict when no status."""
        mac_address = "FF:FF:FF:FF:FF:FF"  # Non-existent device
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        attrs = entity._get_status_attrs("dhw_temperature", "error_code")

        # Should return dict with None values when status is None
        assert attrs["dhw_temperature"] is None
        assert attrs["error_code"] is None

    def test_extra_state_attributes_with_features(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test extra_state_attributes includes device features."""
        mac_address = mock_device.device_info.mac_address

        # Mock device features with all attributes
        mock_feature = MagicMock()
        mock_feature.controller_sw_version = "1.2.3"
        mock_feature.controller_sw_code = "C123"
        mock_feature.panel_sw_version = "2.3.4"
        mock_feature.panel_sw_code = "P456"
        mock_feature.wifi_sw_version = "3.4.5"
        mock_feature.wifi_sw_code = "W789"
        mock_feature.recirc_sw_version = "4.5.6"
        mock_feature.hpwh_use = True
        mock_feature.recirculation_use = False
        mock_feature.dr_setting_use = True
        mock_feature.anti_legionella_setting_use = True
        mock_feature.freeze_protection_use = True
        mock_feature.smart_diagnostic_use = True
        mock_feature.dhw_temperature_min = 120
        mock_feature.dhw_temperature_max = 140
        mock_feature.temperature_type = "F"
        mock_feature.dhw_temperature_setting_use = True
        mock_feature.install_type = "Indoor"
        mock_feature.country_code = "US"

        mock_coordinator.device_features.get.return_value = mock_feature

        # Mock device location with all attributes
        mock_device.location.address = "123 Main St"
        mock_device.location.latitude = 37.7749
        mock_device.location.longitude = -122.4194

        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)
        attrs = entity.extra_state_attributes

        # Verify firmware details
        assert attrs["controller_sw_version"] == "1.2.3"
        assert attrs["controller_sw_code"] == "C123"
        assert attrs["panel_sw_version"] == "2.3.4"
        assert attrs["panel_sw_code"] == "P456"
        assert attrs["wifi_sw_version"] == "3.4.5"
        assert attrs["wifi_sw_code"] == "W789"
        assert attrs["recirc_sw_version"] == "4.5.6"

        # Verify capabilities
        assert attrs["hpwh_use"] is True
        assert attrs["recirculation_use"] is False
        assert attrs["dr_setting_use"] is True

        # Verify operating limits
        assert attrs["dhw_temperature_min"] == 120
        assert attrs["dhw_temperature_max"] == 140

        # Verify installation info
        assert attrs["install_type"] == "Indoor"
        assert attrs["country_code"] == "US"

        # Verify location
        assert attrs["address"] == "123 Main St"
        assert attrs["latitude"] == 37.7749
        assert attrs["longitude"] == -122.4194

    def test_device_data_none_coordinator(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test device_data returns None when coordinator has no data."""
        mac_address = mock_device.device_info.mac_address
        mock_coordinator.data = None

        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        assert entity.device_data is None

    def test_device_name_property(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test device_name property returns correct name."""
        mac_address = mock_device.device_info.mac_address
        entity = NWP500Entity(mock_coordinator, mac_address, mock_device)

        # Should return the device name from mock
        assert entity.device_name == "Test Water Heater"

        # Test fallback when device_name is None
        mock_device.device_info.device_name = None
        assert entity.device_name == "NWP500"
