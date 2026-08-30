"""Tests for the diagnostics module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from custom_components.nwp500.diagnostics import (
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_coordinator(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test diagnostics when coordinator not initialized."""
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result == {"error": "Integration not initialized"}


@pytest.mark.asyncio
async def test_diagnostics_reports_location_keys_but_redacts_values(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Location appears in diagnostics as structure only, never as values."""
    device = MagicMock()
    device.device_info.device_name = "NWP500"
    device.device_info.device_type = 52
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.connected = True
    device.location.address = "123 Main St"
    device.location.city = "San Rafael"
    device.location.state = "CA"
    device.location.latitude = 37.9735
    device.location.longitude = -122.5311
    device.location.altitude = 12.0

    mock_coordinator.mqtt_manager = None
    mock_coordinator.devices = [device]

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    location = result["devices"][0]["location"]
    # Keys retained so maintainers can see which fields the cloud populated.
    assert set(location) == {
        "address",
        "city",
        "state",
        "latitude",
        "longitude",
        "altitude",
    }
    assert all(value == "**REDACTED**" for value in location.values())

    # No PII leaks anywhere else in the payload.
    for leaked in ("123 Main St", "San Rafael", "37.9735", "-122.5311"):
        assert leaked not in str(result)


@pytest.mark.asyncio
async def test_diagnostics_omits_unpopulated_location_fields(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Unset location fields are omitted, not redacted.

    Redaction replaces values by key, so emitting every key unconditionally
    would render an unset field indistinguishable from a populated one.
    """
    device = MagicMock()
    device.device_info.device_name = "NWP500"
    device.device_info.device_type = 52
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.connected = True
    device.location.address = None
    device.location.city = "San Rafael"
    device.location.state = "CA"
    device.location.latitude = None
    device.location.longitude = None
    device.location.altitude = None

    mock_coordinator.mqtt_manager = None
    mock_coordinator.devices = [device]

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert set(result["devices"][0]["location"]) == {"city", "state"}


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_mqtt_manager(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when MQTT manager not available."""
    mock_coordinator.mqtt_manager = None

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "entry_id" in result
    assert "version" in result
    assert result["mqtt_manager_status"] == "MQTT manager not available"


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_with_mqtt_diagnostics(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics with MQTT diagnostics available."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {
        "connected": True,
        "last_connect_time": "2024-01-01T00:00:00Z",
    }

    mock_diagnostics = MagicMock()
    mock_diagnostics.export_json.return_value = '{"test": "data"}'
    mock_mqtt_manager.diagnostics = mock_diagnostics

    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {
        "messages_sent": 10,
        "messages_received": 5,
    }
    mock_coordinator.get_performance_stats.return_value = {
        "update_count": 100,
        "error_count": 2,
    }

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "entry_id" in result
    assert "version" in result
    assert "mqtt_connection_state" in result
    assert result["mqtt_connection_state"]["connected"] is True
    assert "mqtt_diagnostics" in result
    assert result["mqtt_diagnostics"]["test"] == "data"
    assert "coordinator_telemetry" in result
    assert "performance_stats" in result


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_invalid_json(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when export returns invalid format."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {}

    mock_diagnostics = MagicMock()
    mock_diagnostics.export_json.return_value = 12345  # Not a string
    mock_mqtt_manager.diagnostics = mock_diagnostics

    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {}
    mock_coordinator.get_performance_stats.return_value = {}

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "mqtt_diagnostics_error" in result
    assert "Invalid diagnostics format" in result["mqtt_diagnostics_error"]


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_export_exception(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when export raises exception."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {}

    mock_diagnostics = MagicMock()
    mock_diagnostics.export_json.side_effect = ValueError("Export failed")
    mock_mqtt_manager.diagnostics = mock_diagnostics

    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {}
    mock_coordinator.get_performance_stats.return_value = {}

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "mqtt_diagnostics_error" in result
    assert "Export failed" in result["mqtt_diagnostics_error"]


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_no_diagnostics_collector(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Test diagnostics when diagnostics collector not initialized."""
    mock_mqtt_manager = MagicMock()
    mock_mqtt_manager.get_connection_diagnostics.return_value = {}
    mock_mqtt_manager.diagnostics = None

    mock_coordinator.mqtt_manager = mock_mqtt_manager
    mock_coordinator.get_mqtt_telemetry.return_value = {}
    mock_coordinator.get_performance_stats.return_value = {}

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "mqtt_diagnostics_status" in result
    assert (
        result["mqtt_diagnostics_status"]
        == "Diagnostics collector not initialized"
    )


@pytest.mark.asyncio
async def test_diagnostics_reports_the_cloud_recorded_fault_and_descaling(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """The REST-only device metadata is included, with the installer redacted.

    `error` and `descaling` are readable without an MQTT connection, so they
    are the parts of a diagnostics dump most likely to explain an offline
    device. The installer id identifies another party, so it is redacted.
    """
    device = MagicMock()
    device.device_info.device_name = "NWP500"
    device.device_info.device_type = 52
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.connected = True
    device.device_info.model_type_code = 7
    device.device_info.installer_id = "installer@example.com"
    device.error.error_code = MagicMock()
    device.error.error_code.name = "E015"
    device.error.error_occurred_time = "2026-08-29 07:15:00"
    device.descaling.descaling_start_time = "2026-08-01 00:00:00"
    device.descaling.descaling_end_time = None
    device.location.address = None
    device.location.city = None
    device.location.state = None
    device.location.latitude = None
    device.location.longitude = None
    device.location.altitude = None

    mock_coordinator.mqtt_manager = None
    mock_coordinator.devices = [device]

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    entry = result["devices"][0]
    assert entry["model_type_code"] == 7
    assert entry["installer_id"] == "**REDACTED**"
    assert "installer@example.com" not in str(result)
    assert entry["error"] == {
        "error_code": "E015",
        "error_occurred_time": "2026-08-29 07:15:00",
    }
    assert entry["descaling"] == {
        "descaling_start_time": "2026-08-01 00:00:00",
        "descaling_end_time": None,
    }


@pytest.mark.asyncio
async def test_diagnostics_omits_blocks_the_cloud_did_not_send(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """`/device/info` carries no error block, and both blocks are optional."""
    device = MagicMock()
    device.device_info.device_name = "NWP500"
    device.device_info.device_type = 52
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.connected = True
    device.device_info.model_type_code = None
    device.device_info.installer_id = None
    device.error = None
    device.descaling = None
    device.location = None

    mock_coordinator.mqtt_manager = None
    mock_coordinator.devices = [device]

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    entry = result["devices"][0]
    assert "error" not in entry
    assert "descaling" not in entry


@pytest.mark.asyncio
async def test_diagnostics_omits_an_installer_id_the_cloud_did_not_send(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_coordinator: MagicMock,
) -> None:
    """Redaction rewrites by key, so an unset value must not be emitted.

    Otherwise "**REDACTED**" would be indistinguishable from a populated
    installer id -- the trap the location block already avoids.
    """
    device = MagicMock()
    device.device_info.device_name = "NWP500"
    device.device_info.device_type = 52
    device.device_info.mac_address = "AA:BB:CC:DD:EE:FF"
    device.device_info.connected = True
    device.device_info.model_type_code = None
    device.device_info.installer_id = None
    device.error = None
    device.descaling = None
    device.location = None

    mock_coordinator.mqtt_manager = None
    mock_coordinator.devices = [device]

    mock_config_entry.runtime_data = mock_coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "installer_id" not in result["devices"][0]


class TestMacRedaction:
    """The MAC pattern must catch MACs without eating neighbouring values."""

    @staticmethod
    def _redact(value: str) -> str:
        from custom_components.nwp500.diagnostics import _redact_macs

        return _redact_macs(value)

    @pytest.mark.parametrize(
        "raw",
        [
            "AA:BB:CC:DD:EE:FF",
            "aa-bb-cc-dd-ee-ff",
            "04786332fca0",
        ],
    )
    def test_redacts_mac_addresses(self, raw: str):
        """Delimited and bare MAC addresses are still removed."""
        assert self._redact(raw) == "**REDACTED**"

    def test_redacts_a_mac_embedded_in_a_request_id(self):
        """Telemetry IDs are '<mac>_<epoch_ms>'; only the MAC is a MAC."""
        assert self._redact("04786332fca0_1766955369594") == (
            "**REDACTED**_1766955369594"
        )

    @pytest.mark.parametrize(
        "raw",
        [
            # Longer hex runs are not MAC addresses. Without boundaries the
            # bare-12 branch chewed 12 characters out of each of these.
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            "1766955369594",
            "deadbeefcafebabe1234",
        ],
    )
    def test_leaves_longer_identifiers_intact(self, raw: str):
        """A 12-character window inside a longer run is not a MAC."""
        assert self._redact(raw) == raw

    def test_leaves_timestamps_intact(self):
        """ISO timestamps survive; they carry no MAC."""
        raw = "2025-12-28T20:56:37.611283+00:00"
        assert self._redact(raw) == raw
