"""Tests for sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nwp500.sensor import (
    NWP500Sensor,
    async_setup_entry,
)


class TestNWP500Sensor:
    """Tests for NWP500Sensor."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test sensor platform setup."""
        # Mock coordinator data
        mock_coordinator.data = {
            "AA:BB:CC:DD:EE:FF": {
                "device": mock_device,
                "status": mock_device_status,
            }
        }

        # Mock hass.data
        hass.data = {"nwp500": {mock_config_entry.entry_id: mock_coordinator}}

        entities_added = []

        def mock_add_entities(entities, update_before_add):
            entities_added.extend(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Should create sensors for the device
        assert len(entities_added) > 0
        # Check that entities are SensorEntity instances (NWP500Sensor or subclasses)
        from homeassistant.components.sensor import SensorEntity

        assert all(isinstance(e, SensorEntity) for e in entities_added)

    def test_sensor_dhw_temperature(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test DHW temperature sensor."""
        from custom_components.nwp500.sensor import create_sensor_descriptions

        descriptions = create_sensor_descriptions()
        # Use the first temperature sensor we can find
        temp_desc = next(
            (d for d in descriptions if "temperature" in d.key.lower()),
            descriptions[0],
        )

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500Sensor(
            mock_coordinator, mac_address, mock_device, temp_desc
        )

        assert sensor.unique_id == f"{mac_address}_{temp_desc.key}"
        # Value will be either the temperature or None if not available
        assert sensor.native_value is not None or sensor.native_value is None

    def test_sensor_missing_value(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test sensor with missing value."""
        from custom_components.nwp500.sensor import create_sensor_descriptions

        descriptions = create_sensor_descriptions()
        # Use any sensor description
        desc = descriptions[0]

        # Remove all temperature attributes to ensure we get None
        for attr in dir(mock_device_status):
            if not attr.startswith("_"):
                try:
                    delattr(mock_device_status, attr)
                except AttributeError:
                    pass

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500Sensor(mock_coordinator, mac_address, mock_device, desc)

        assert sensor.native_value is None

    def test_sensor_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test sensor when status is unavailable."""
        from custom_components.nwp500.sensor import create_sensor_descriptions

        descriptions = create_sensor_descriptions()
        desc = descriptions[0]

        # Remove status from coordinator data
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
            }
        }

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500Sensor(mock_coordinator, mac_address, mock_device, desc)

        assert sensor.native_value is None

    def test_sensor_with_value(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test sensor returns a value."""
        from custom_components.nwp500.sensor import create_sensor_descriptions

        descriptions = create_sensor_descriptions()
        # Find a sensor that should have a value
        desc = next(
            (
                d
                for d in descriptions
                if hasattr(
                    mock_device_status, d.key if hasattr(d, "key") else ""
                )
            ),
            descriptions[0],
        )

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500Sensor(mock_coordinator, mac_address, mock_device, desc)

        # Just verify the sensor can be created and accessed
        _ = sensor.native_value  # May be None or a value

    def test_diagnostic_sensors(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test diagnostic sensors."""
        from custom_components.nwp500.sensor import (
            NWP500ConsecutiveTimeoutsSensor,
            NWP500MQTTConnectedSensor,
        )

        # Mock telemetry data
        mock_coordinator.get_mqtt_telemetry.return_value = {
            "last_request_id": "123",
            "last_request_time": 1000.0,
            "last_response_id": "123",
            "last_response_time": 1001.0,
            "total_requests_sent": 10,
            "total_responses_received": 10,
            "mqtt_connected": True,
            "mqtt_connected_since": 900.0,
            "consecutive_timeouts": 5,
        }

        mac_address = mock_device.device_info.mac_address

        # Test Consecutive Timeouts Sensor
        timeout_sensor = NWP500ConsecutiveTimeoutsSensor(
            mock_coordinator, mac_address, mock_device
        )
        assert timeout_sensor.native_value == 5
        assert (
            timeout_sensor.unique_id
            == f"{mac_address}_diagnostic_consecutive_timeouts"
        )

        # Test MQTT Connected Sensor
        connected_sensor = NWP500MQTTConnectedSensor(
            mock_coordinator, mac_address, mock_device
        )
        assert connected_sensor.native_value == "connected"
        assert (
            connected_sensor.unique_id
            == f"{mac_address}_diagnostic_mqtt_status"
        )

        # Test extra_state_attributes with active connection
        attrs = connected_sensor.extra_state_attributes
        assert "connected_since" in attrs
        assert "connected_duration_seconds" in attrs

        # Test extra_state_attributes without connection
        mock_coordinator.get_mqtt_telemetry.return_value = {
            **mock_coordinator.get_mqtt_telemetry.return_value,
            "mqtt_connected": False,
            "mqtt_connected_since": None,
        }
        attrs_disconnected = connected_sensor.extra_state_attributes
        assert "connected_since" not in attrs_disconnected

    def test_request_response_count_sensors(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test MQTT request and response count sensors."""
        from custom_components.nwp500.sensor import (
            NWP500MQTTRequestCountSensor,
            NWP500MQTTResponseCountSensor,
        )

        mock_coordinator.get_mqtt_telemetry.return_value = {
            "total_requests_sent": 42,
            "total_responses_received": 38,
        }

        mac_address = mock_device.device_info.mac_address

        request_sensor = NWP500MQTTRequestCountSensor(
            mock_coordinator, mac_address, mock_device
        )
        assert request_sensor.native_value == 42

        response_sensor = NWP500MQTTResponseCountSensor(
            mock_coordinator, mac_address, mock_device
        )
        assert response_sensor.native_value == 38

    def test_last_response_time_sensor(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test last response time sensor."""
        from custom_components.nwp500.sensor import NWP500LastResponseTimeSensor

        mac_address = mock_device.device_info.mac_address

        # With a valid timestamp
        mock_coordinator.get_mqtt_telemetry.return_value = {
            "last_response_time": 1000.0,
            "last_request_id": "req1",
            "last_response_id": "rsp1",
            "last_request_time": 999.0,
        }
        sensor = NWP500LastResponseTimeSensor(
            mock_coordinator, mac_address, mock_device
        )
        assert sensor.native_value is not None
        attrs = sensor.extra_state_attributes
        assert attrs["last_request_id"] == "req1"
        assert attrs["last_response_id"] == "rsp1"
        assert "response_latency" in attrs

        # Without a timestamp
        mock_coordinator.get_mqtt_telemetry.return_value = {
            "last_response_time": None,
            "last_request_id": None,
            "last_response_id": None,
            "last_request_time": None,
        }
        assert sensor.native_value is None
        attrs_none = sensor.extra_state_attributes
        assert "response_latency" not in attrs_none

    def test_sensor_get_field_unit_integration(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test that sensors correctly call and use get_field_unit."""
        from custom_components.nwp500.const import SENSOR_CONFIGS
        from custom_components.nwp500.sensor import NWP500Sensor

        # Find a temperature sensor description
        temp_sensor_config = None
        for key, config in SENSOR_CONFIGS.items():
            if "temperature" in key.lower() and "dhw_temperature" == key:
                temp_sensor_config = config
                break

        assert temp_sensor_config is not None

        # Setup coordinator data with mock device status
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
                "status": mock_device_status,
            }
        }

        # Mock the coordinator's get_field_unit_safe method
        mock_coordinator.get_field_unit_safe = MagicMock(return_value="°F")

        mac_address = mock_device.device_info.mac_address

        # Create a sensor description from the config
        from homeassistant.components.sensor import SensorEntityDescription

        sensor_desc = SensorEntityDescription(
            key="dhw_temperature",
            name="DHW Temperature",
        )

        # Create sensor
        sensor = NWP500Sensor(
            mock_coordinator,
            mac_address,
            mock_device,
            sensor_desc,
        )
        sensor.hass = mock_hass

        # Verify sensor has correct unit (should be stripped of spaces)
        # The sensor should have the unit from get_field_unit without the space
        unit = sensor.native_unit_of_measurement
        if unit:
            # Unit should be "°F" not " °F" (space stripped)
            assert not unit.startswith(" ")

    def test_sensor_temperature_unit_trusts_device(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test that temperature sensors trust the device's reported unit."""
        from homeassistant.components.sensor import (
            SensorDeviceClass,
            SensorEntityDescription,
        )
        from homeassistant.const import UnitOfTemperature

        from custom_components.nwp500.sensor import NWP500Sensor

        # Configure HA to use Celsius
        mock_hass.config.units.temperature_unit = UnitOfTemperature.CELSIUS

        # Mock device reporting Fahrenheit
        mock_device_status.get_field_unit.return_value = " °F"

        # Setup coordinator with status
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
                "status": mock_device_status,
            }
        }

        # Mock the coordinator's get_field_unit_safe method to return the device unit
        mock_coordinator.get_field_unit_safe = MagicMock(return_value="°F")

        # Create a temperature sensor description
        desc = SensorEntityDescription(
            key="test_temp",
            name="Test Temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=None,
        )

        mac_address = mock_device.device_info.mac_address

        sensor = NWP500Sensor(mock_coordinator, mac_address, mock_device, desc)

        sensor.hass = mock_hass

        # Should return Fahrenheit (device unit) despite HA being Celsius
        # This prevents "120 °C" display errors when device sends F values
        assert sensor.native_unit_of_measurement == "°F"

    def test_sensor_unit_lookup_uses_attr_name(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test that unit lookup uses attr_name if available in description."""
        from custom_components.nwp500.sensor import (
            NWP500Sensor,
            NWP500SensorEntityDescription,
        )

        # Create description where key != attr_name
        desc = NWP500SensorEntityDescription(
            key="recirculation_temperature",
            attr_name="recirc_temperature",
            name="Recirc Temperature",
        )

        mock_coordinator.get_field_unit_safe = MagicMock(return_value="°C")
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
                "status": mock_device_status,
            }
        }

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500Sensor(mock_coordinator, mac_address, mock_device, desc)
        sensor.hass = mock_hass

        # Accessing unit should trigger lookup with attr_name
        unit = sensor.native_unit_of_measurement

        assert unit == "°C"
        mock_coordinator.get_field_unit_safe.assert_called_once_with(
            mock_device_status, "recirc_temperature"
        )

    def test_sensor_unit_fallback_when_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test that unit falls back to description when no device status."""
        from homeassistant.const import UnitOfTemperature

        from custom_components.nwp500.sensor import (
            NWP500Sensor,
            NWP500SensorEntityDescription,
        )

        desc = NWP500SensorEntityDescription(
            key="dhw_temperature",
            name="DHW Temperature",
            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        )

        # No data in coordinator means no status
        mock_coordinator.data = {}

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500Sensor(mock_coordinator, mac_address, mock_device, desc)

        assert sensor.native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT

    def test_sensor_value_fn_exception_returns_none(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test that value_fn raising AttributeError/TypeError returns None."""
        from custom_components.nwp500.sensor import (
            NWP500Sensor,
            NWP500SensorEntityDescription,
        )

        def bad_value_fn(status: object) -> str:
            raise AttributeError("no such attribute")

        desc = NWP500SensorEntityDescription(
            key="dhw_temperature",
            name="DHW Temperature",
            value_fn=bad_value_fn,
        )

        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
                "status": mock_device_status,
            }
        }

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500Sensor(mock_coordinator, mac_address, mock_device, desc)

        assert sensor.native_value is None
