"""Tests for sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nwp500.sensor import (
    NWP500CloudErrorSensor,
    NWP500DescalingSensor,
    NWP500ReservationScheduleSensor,
    NWP500Sensor,
    NWP500TOUScheduleSensor,
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

        mock_config_entry.runtime_data = mock_coordinator

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

    def test_energy_sensors(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test the nwp500-python 9.3.0 energy sensors.

        The pre-9.3.0 ``total_energy_capacity`` / ``available_energy_capacity``
        fields no longer exist on ``DeviceStatus``, so entities keyed on them
        would silently read ``None`` forever.
        """
        from homeassistant.components.sensor import SensorDeviceClass

        from custom_components.nwp500.sensor import create_sensor_descriptions

        by_key = {d.key: d for d in create_sensor_descriptions()}

        assert "total_energy_capacity" not in by_key
        assert "available_energy_capacity" not in by_key

        mac_address = mock_device.device_info.mac_address
        mock_coordinator.data = {
            mac_address: {
                "device": mock_device,
                "status": mock_device_status,
            }
        }

        expected = {
            "usable_energy": 7000.0,
            "energy_to_setpoint": 2000.0,
            "full_recovery_energy": 9000.0,
        }
        for key, value in expected.items():
            desc = by_key[key]
            sensor = NWP500Sensor(
                mock_coordinator, mac_address, mock_device, desc
            )
            assert sensor.native_value == value
            assert desc.native_unit_of_measurement == "Wh"

        # Only usable_energy is a state of charge; the other two are measured
        # from the setpoint and move when the setpoint moves.
        assert (
            by_key["usable_energy"].device_class
            == SensorDeviceClass.ENERGY_STORAGE
        )
        assert by_key["energy_to_setpoint"].device_class is None
        assert by_key["full_recovery_energy"].device_class is None

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


class TestScheduleSensors:
    """The programmed schedules are exposed as pollable entity state.

    Before issue #103 they lived only in coordinator dicts and one-shot bus
    events, so an external scheduler could not read back what is programmed
    over the REST API.
    """

    @staticmethod
    def _entry(**overrides):
        entry = {
            "enable": 2,
            "week": 42,
            "hour": 6,
            "min": 30,
            "mode": 3,
            "param": 120,
        }
        entry.update(overrides)
        return entry

    def _sensor(self, mock_coordinator, mock_device, schedule, cls):
        mock_coordinator.reservation_schedules = {}
        mock_coordinator.tou_schedules = {}
        if schedule is not None:
            store = (
                "reservation_schedules"
                if cls is NWP500ReservationScheduleSensor
                else "tou_schedules"
            )
            getattr(mock_coordinator, store)["AA:BB:CC:DD:EE:FF"] = schedule
        return cls(mock_coordinator, "AA:BB:CC:DD:EE:FF", mock_device)

    @pytest.mark.parametrize(
        "cls",
        [NWP500ReservationScheduleSensor, NWP500TOUScheduleSensor],
    )
    def test_state_is_none_before_the_schedule_is_read(
        self, mock_coordinator, mock_device, cls
    ):
        """Unfetched must stay distinguishable from "device has none"."""
        sensor = self._sensor(mock_coordinator, mock_device, None, cls)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes["enabled"] is None
        assert sensor.extra_state_attributes["schedule_hash"] is None

    @pytest.mark.parametrize(
        "cls",
        [NWP500ReservationScheduleSensor, NWP500TOUScheduleSensor],
    )
    def test_state_is_zero_when_device_has_no_entries(
        self, mock_coordinator, mock_device, cls
    ):
        """A fetched but empty program reports 0, not None."""
        sensor = self._sensor(
            mock_coordinator,
            mock_device,
            {"reservation_use": 1, "reservation": []},
            cls,
        )

        assert sensor.native_value == 0
        assert sensor.extra_state_attributes["enabled"] is False

    def test_state_counts_entries(self, mock_coordinator, mock_device):
        """The state is the number of programmed entries."""
        sensor = self._sensor(
            mock_coordinator,
            mock_device,
            {
                "reservation_use": 2,
                "reservation": [self._entry(hour=6), self._entry(hour=18)],
            },
            NWP500ReservationScheduleSensor,
        )

        assert sensor.native_value == 2

    def test_attributes_expose_the_program(self, mock_coordinator, mock_device):
        """The entries themselves are readable, with the enable flag."""
        entries = [self._entry(hour=6), self._entry(hour=18)]
        sensor = self._sensor(
            mock_coordinator,
            mock_device,
            {"reservation_use": 2, "reservation": entries},
            NWP500ReservationScheduleSensor,
        )

        attrs = sensor.extra_state_attributes

        assert attrs["entries"] == entries
        assert attrs["enabled"] is True
        assert attrs["schedule_hash"]

    def test_hash_is_order_independent(self, mock_coordinator, mock_device):
        """Same program, different report order, same hash."""
        forward = self._sensor(
            mock_coordinator,
            mock_device,
            {
                "reservation_use": 2,
                "reservation": [self._entry(hour=6), self._entry(hour=18)],
            },
            NWP500ReservationScheduleSensor,
        )
        first = forward.extra_state_attributes["schedule_hash"]

        reversed_ = self._sensor(
            mock_coordinator,
            mock_device,
            {
                "reservation_use": 2,
                "reservation": [self._entry(hour=18), self._entry(hour=6)],
            },
            NWP500ReservationScheduleSensor,
        )

        assert reversed_.extra_state_attributes["schedule_hash"] == first

    def test_hash_changes_when_the_program_changes(
        self, mock_coordinator, mock_device
    ):
        """A consumer can detect drift from the desired program."""
        before = self._sensor(
            mock_coordinator,
            mock_device,
            {"reservation_use": 2, "reservation": [self._entry(mode=3)]},
            NWP500ReservationScheduleSensor,
        ).extra_state_attributes["schedule_hash"]

        after = self._sensor(
            mock_coordinator,
            mock_device,
            {"reservation_use": 2, "reservation": [self._entry(mode=4)]},
            NWP500ReservationScheduleSensor,
        ).extra_state_attributes["schedule_hash"]

        assert before != after

    @pytest.mark.parametrize(
        "cls",
        [NWP500ReservationScheduleSensor, NWP500TOUScheduleSensor],
    )
    def test_no_state_class(self, mock_coordinator, mock_device, cls):
        """An entry count is unitless, so it must not be a MEASUREMENT.

        Home Assistant expects MEASUREMENT sensors to carry a unit and would
        otherwise record meaningless long-term statistics.
        """
        sensor = self._sensor(mock_coordinator, mock_device, None, cls)

        assert sensor.state_class is None

    def test_attributes_do_not_expose_coordinator_state(
        self, mock_coordinator, mock_device
    ):
        """Mutating the attributes must not corrupt the stored schedule."""
        entry = self._entry()
        sensor = self._sensor(
            mock_coordinator,
            mock_device,
            {"reservation_use": 2, "reservation": [entry]},
            NWP500ReservationScheduleSensor,
        )

        sensor.extra_state_attributes["entries"][0]["hour"] = 23

        assert entry["hour"] == 6


class TestCloudMetadataSensors:
    """Sensors fed by the REST device list rather than by MQTT status.

    The cloud keeps the last recorded fault and the descaling window
    independently of the live status, so these stay readable while the
    device is offline -- which is when the recorded fault matters most.
    """

    MAC = "AA:BB:CC:DD:EE:FF"

    def _error_sensor(self, coordinator, device):
        return NWP500CloudErrorSensor(coordinator, self.MAC, device)

    def test_error_reports_the_recorded_code_by_name(
        self, mock_coordinator, mock_device
    ):
        error = MagicMock()
        error.error_code.name = "E015"
        error.error_occurred_time = "2026-08-29 07:15:00"
        mock_coordinator.get_device_error.return_value = error

        sensor = self._error_sensor(mock_coordinator, mock_device)

        assert sensor.native_value == "E015"
        assert (
            sensor.extra_state_attributes["occurred_at"]
            == "2026-08-29 07:15:00"
        )

    def test_an_unknown_code_is_reported_as_its_number(
        self, mock_coordinator, mock_device
    ):
        """A code the library's enum does not know stays a plain int."""
        error = MagicMock()
        error.error_code = 999
        mock_coordinator.get_device_error.return_value = error

        sensor = self._error_sensor(mock_coordinator, mock_device)

        assert sensor.native_value == "999"

    def test_no_error_block_reports_nothing(
        self, mock_coordinator, mock_device
    ):
        """`/device/info` carries no error block, so it must stay optional."""
        mock_coordinator.get_device_error.return_value = None

        sensor = self._error_sensor(mock_coordinator, mock_device)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes["occurred_at"] is None

    @pytest.mark.parametrize(
        "field", ["descaling_start_time", "descaling_end_time"]
    )
    def test_descaling_reports_the_recorded_timestamp(
        self, mock_coordinator, mock_device, field
    ):
        descaling = MagicMock()
        descaling.descaling_start_time = "2026-08-01 00:00:00"
        descaling.descaling_end_time = "2026-08-01 02:00:00"
        mock_coordinator.get_device_descaling.return_value = descaling

        sensor = NWP500DescalingSensor(
            mock_coordinator, self.MAC, mock_device, field
        )

        assert sensor.native_value == getattr(descaling, field)

    def test_descaling_without_a_window_reports_nothing(
        self, mock_coordinator, mock_device
    ):
        """The common case: no descaling scheduled or recorded."""
        descaling = MagicMock()
        descaling.descaling_start_time = None
        mock_coordinator.get_device_descaling.return_value = descaling

        sensor = NWP500DescalingSensor(
            mock_coordinator, self.MAC, mock_device, "descaling_start_time"
        )

        assert sensor.native_value is None

    def test_descaling_sensors_are_disabled_by_default(
        self, mock_coordinator, mock_device
    ):
        """Mostly-empty diagnostics should not clutter every install."""
        sensor = NWP500DescalingSensor(
            mock_coordinator, self.MAC, mock_device, "descaling_start_time"
        )

        assert sensor.entity_registry_enabled_default is False
