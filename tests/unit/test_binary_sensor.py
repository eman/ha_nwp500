"""Tests for binary_sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nwp500.binary_sensor import (
    NWP500BinarySensor,
    async_setup_entry,
)


class TestNWP500BinarySensor:
    """Tests for NWP500BinarySensor."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test binary sensor platform setup."""
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

        # Should create binary sensors for the device
        assert len(entities_added) > 0
        assert all(isinstance(e, NWP500BinarySensor) for e in entities_added)

    async def test_binary_sensor_operation_busy(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test operation busy binary sensor."""
        from custom_components.nwp500.binary_sensor import (
            create_binary_sensor_descriptions,
        )

        descriptions = create_binary_sensor_descriptions()
        op_busy_desc = next(
            d for d in descriptions if d.key == "operation_busy"
        )

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500BinarySensor(
            mock_coordinator, mac_address, mock_device, op_busy_desc
        )

        assert sensor.is_on is True
        assert sensor.unique_id == f"{mac_address}_operation_busy"

    async def test_binary_sensor_false(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test binary sensor returning False."""
        from custom_components.nwp500.binary_sensor import (
            create_binary_sensor_descriptions,
        )

        descriptions = create_binary_sensor_descriptions()
        freeze_desc = next(
            d for d in descriptions if d.key == "freeze_protection_use"
        )

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500BinarySensor(
            mock_coordinator, mac_address, mock_device, freeze_desc
        )

        assert sensor.is_on is False

    async def test_binary_sensor_missing_value(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test binary sensor with missing attribute."""
        from custom_components.nwp500.binary_sensor import (
            create_binary_sensor_descriptions,
        )

        descriptions = create_binary_sensor_descriptions()
        op_busy_desc = next(
            d for d in descriptions if d.key == "operation_busy"
        )

        # Remove the attribute
        delattr(mock_device_status, "operationBusy")

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500BinarySensor(
            mock_coordinator, mac_address, mock_device, op_busy_desc
        )

        assert sensor.is_on is None

    async def test_binary_sensor_no_status(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test binary sensor when status is unavailable."""
        from custom_components.nwp500.binary_sensor import (
            create_binary_sensor_descriptions,
        )

        descriptions = create_binary_sensor_descriptions()
        op_busy_desc = next(
            d for d in descriptions if d.key == "operation_busy"
        )

        # Remove status
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
            }
        }

        mac_address = mock_device.device_info.mac_address
        sensor = NWP500BinarySensor(
            mock_coordinator, mac_address, mock_device, op_busy_desc
        )

        assert sensor.is_on is None
