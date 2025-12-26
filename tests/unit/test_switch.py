"""Tests for switch platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nwp500.switch import (
    NWP500AntiLegionellaSwitch,
    NWP500PowerSwitch,
    NWP500TOUOverrideSwitch,
    async_setup_entry,
)


class TestNWP500PowerSwitch:
    """Tests for NWP500PowerSwitch."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test switch platform setup."""
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

        # Should create power, TOU override, and anti-legionella switches
        assert len(entities_added) == 3
        assert isinstance(entities_added[0], NWP500PowerSwitch)
        assert isinstance(entities_added[1], NWP500TOUOverrideSwitch)
        assert isinstance(entities_added[2], NWP500AntiLegionellaSwitch)

    def test_switch_is_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test switch is_on property."""
        mac_address = mock_device.device_info.mac_address
        switch = NWP500PowerSwitch(mock_coordinator, mac_address, mock_device)

        # Device is on (dhwOperationSetting = 1, not 6)
        assert switch.is_on is True
        assert switch.unique_id == f"{mac_address}_power"

    def test_switch_is_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test switch is_on when powered off."""
        # Set device to power off mode (6)
        mock_device_status.dhw_operation_setting.value = 6

        mac_address = mock_device.device_info.mac_address
        switch = NWP500PowerSwitch(mock_coordinator, mac_address, mock_device)

        assert switch.is_on is False

    def test_switch_fallback_to_operation_mode(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test switch falls back to operationMode."""
        # Remove dhw_operation_setting
        delattr(mock_device_status, "dhw_operation_setting")

        mac_address = mock_device.device_info.mac_address
        switch = NWP500PowerSwitch(mock_coordinator, mac_address, mock_device)

        # Should fall back to operationMode and return True
        assert switch.is_on is True

    def test_switch_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test switch when status is unavailable."""
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
            }
        }

        mac_address = mock_device.device_info.mac_address
        switch = NWP500PowerSwitch(mock_coordinator, mac_address, mock_device)

        assert switch.is_on is None

    @pytest.mark.asyncio
    async def test_async_turn_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test turning switch on."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        switch = NWP500PowerSwitch(mock_coordinator, mac_address, mock_device)

        await switch.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_power", power_on=True
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test turning switch off."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        switch = NWP500PowerSwitch(mock_coordinator, mac_address, mock_device)

        await switch.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_power", power_on=False
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_failure(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test turning switch on fails."""
        mock_coordinator.async_control_device = AsyncMock(return_value=False)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        switch = NWP500PowerSwitch(mock_coordinator, mac_address, mock_device)

        await switch.async_turn_on()

        # Should not request refresh if control failed
        mock_coordinator.async_request_refresh.assert_not_called()


class TestNWP500TOUOverrideSwitch:
    """Tests for NWP500TOUOverrideSwitch."""

    def test_switch_is_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test TOU override switch is_on property when TOU enabled."""
        mock_device_status.tou_status = True
        mac_address = mock_device.device_info.mac_address
        switch = NWP500TOUOverrideSwitch(
            mock_coordinator, mac_address, mock_device
        )

        assert switch.is_on is True
        assert switch.unique_id == f"{mac_address}_tou"

    def test_switch_is_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test TOU override switch is_on when TOU disabled."""
        mock_device_status.tou_status = False
        mac_address = mock_device.device_info.mac_address
        switch = NWP500TOUOverrideSwitch(
            mock_coordinator, mac_address, mock_device
        )

        assert switch.is_on is False

    def test_switch_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test switch when status is unavailable."""
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
            }
        }

        mac_address = mock_device.device_info.mac_address
        switch = NWP500TOUOverrideSwitch(
            mock_coordinator, mac_address, mock_device
        )

        assert switch.is_on is None

    @pytest.mark.asyncio
    async def test_async_turn_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test enabling TOU."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        switch = NWP500TOUOverrideSwitch(
            mock_coordinator, mac_address, mock_device
        )

        await switch.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_tou_enabled", enabled=True
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test disabling TOU."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        switch = NWP500TOUOverrideSwitch(
            mock_coordinator, mac_address, mock_device
        )

        await switch.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "set_tou_enabled", enabled=False
        )
        mock_coordinator.async_request_refresh.assert_called_once()


class TestNWP500AntiLegionellaSwitch:
    """Tests for NWP500AntiLegionellaSwitch."""

    def test_switch_is_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test anti-legionella switch is_on property when enabled."""
        mock_device_status.anti_legionella_use = True
        mac_address = mock_device.device_info.mac_address
        switch = NWP500AntiLegionellaSwitch(
            mock_coordinator, mac_address, mock_device
        )

        assert switch.is_on is True
        assert switch.unique_id == f"{mac_address}_anti_legionella"

    def test_switch_is_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test anti-legionella switch is_on when disabled."""
        mock_device_status.anti_legionella_use = False
        mac_address = mock_device.device_info.mac_address
        switch = NWP500AntiLegionellaSwitch(
            mock_coordinator, mac_address, mock_device
        )

        assert switch.is_on is False

    def test_switch_no_status(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
    ):
        """Test switch when status is unavailable."""
        mock_coordinator.data = {
            mock_device.device_info.mac_address: {
                "device": mock_device,
            }
        }

        mac_address = mock_device.device_info.mac_address
        switch = NWP500AntiLegionellaSwitch(
            mock_coordinator, mac_address, mock_device
        )

        assert switch.is_on is None

    @pytest.mark.asyncio
    async def test_async_turn_on(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test enabling anti-legionella."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        switch = NWP500AntiLegionellaSwitch(
            mock_coordinator, mac_address, mock_device
        )

        await switch.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "enable_anti_legionella", period_days=14
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off(
        self,
        mock_coordinator: MagicMock,
        mock_device: MagicMock,
        mock_device_status: MagicMock,
    ):
        """Test disabling anti-legionella."""
        mock_coordinator.async_control_device = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mac_address = mock_device.device_info.mac_address
        switch = NWP500AntiLegionellaSwitch(
            mock_coordinator, mac_address, mock_device
        )

        await switch.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            mac_address, "disable_anti_legionella"
        )
        mock_coordinator.async_request_refresh.assert_called_once()
