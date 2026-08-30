"""Tests for number platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.nwp500.number import (
    NWP500TargetTemperature,
    async_setup_entry,
)


class TestNWP500TargetTemperature:
    """Tests for NWP500TargetTemperature."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test number platform setup."""
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

        # Should create target temperature number entity
        assert len(entities_added) == 1
        assert isinstance(entities_added[0], NWP500TargetTemperature)

    def test_native_value(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test native_value property."""
        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        assert number.native_value == 130.0
        assert number.unique_id == f"{mac_address}_target_temperature"

    def test_native_value_fallback(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test native_value falls back to dhwTemperatureSetting."""
        # Remove dhw_target_temperature_setting
        delattr(mock_device_status, "dhw_target_temperature_setting")
        mock_device_status.dhw_temperature_setting = 125.0

        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        assert number.native_value == 125.0

    def test_native_value_missing(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test native_value when temperature is missing."""
        # Remove both temperature attributes
        if hasattr(mock_device_status, "dhw_target_temperature_setting"):
            delattr(mock_device_status, "dhw_target_temperature_setting")
        if hasattr(mock_device_status, "dhw_temperature_setting"):
            delattr(mock_device_status, "dhw_temperature_setting")

        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        assert number.native_value is None

    def test_native_value_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test native_value when status is unavailable."""
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
            }
        }

        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        assert number.native_value is None

    @pytest.mark.asyncio
    async def test_async_set_native_value(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test setting native value."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        await number.async_set_native_value(135.0)

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_temperature", temperature=135
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_set_native_value_failure(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
        mock_hass: MagicMock,
    ):
        """Test setting native value fails."""
        mock_coordinator.async_control_device = AsyncMock(return_value=False)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        number = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        number.hass = mock_hass

        await number.async_set_native_value(135.0)

        # Should not request refresh if control failed
        mock_coordinator.async_request_refresh.assert_not_called()


class TestTargetTemperatureUnitGuard:
    """The Number entity dispatches the same `set_temperature` command.

    It is a temperature sender like the water heater, so it must serialize
    against a unit-system transition too: the conversion to device units
    happens inside the library, after the publish has awaited.
    """

    @staticmethod
    def _entity(mock_coordinator, mock_device, mock_hass):
        mac_address = mock_device.device_info.mac_address
        entity = NWP500TargetTemperature(
            mock_coordinator, mac_address, mock_device
        )
        entity.hass = mock_hass
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        return entity

    @pytest.mark.asyncio
    async def test_dispatches_when_no_transition_is_underway(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
    ):
        """The ordinary case still writes."""
        entity = self._entity(mock_coordinator, mock_device, mock_hass)

        await entity.async_set_native_value(125.0)

        mock_coordinator.async_control_device.assert_awaited_once_with(
            mock_device.device_info.mac_address,
            "set_temperature",
            temperature=125.0,
        )

    @pytest.mark.asyncio
    async def test_refuses_during_unit_system_change(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_hass: MagicMock,
        raising_unit_guard,
    ):
        """A write mid-transition could be encoded in the wrong scale.

        This path was missed when the guard was introduced: the water
        heater held it but the Number entity issued the same command
        without it.
        """
        entity = self._entity(mock_coordinator, mock_device, mock_hass)
        mock_coordinator.unit_transition_guard = raising_unit_guard

        with pytest.raises(HomeAssistantError):
            await entity.async_set_native_value(125.0)

        mock_coordinator.async_control_device.assert_not_called()
