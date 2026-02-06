"""Tests for NWP500 reservation services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.nwp500 import (
    ATTR_DAYS,
    ATTR_DEVICE_ID,
    ATTR_ENABLED,
    ATTR_HOUR,
    ATTR_MINUTE,
    ATTR_OP_MODE,
    ATTR_RESERVATIONS,
    ATTR_TEMPERATURE,
    _async_setup_services,
    validate_reservation_temperature,
)
from custom_components.nwp500.const import DEFAULT_TEMPERATURE, DOMAIN
from custom_components.nwp500.coordinator import NWP500DataUpdateCoordinator


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    hass.data = {DOMAIN: {}}
    return hass


@pytest.fixture
def mock_device_registry():
    """Create a mock device registry."""
    with patch("custom_components.nwp500.dr.async_get") as mock_dr:
        registry = MagicMock()
        mock_dr.return_value = registry
        yield registry


@pytest.fixture
def mock_service_call():
    """Create a mock service call."""
    call = MagicMock(spec=ServiceCall)
    call.data = {}
    return call


class TestReservationValidator:
    """Test reservation data validation."""

    def test_validate_temperature_required(self):
        """Test temperature is required for heating modes."""
        data = {ATTR_OP_MODE: "heat_pump"}
        with pytest.raises(vol.Invalid, match="Temperature is required"):
            validate_reservation_temperature(data)

    def test_validate_temperature_optional_for_vacation(self):
        """Test temperature is optional for vacation mode."""
        data = {ATTR_OP_MODE: "vacation"}
        result = validate_reservation_temperature(data)
        assert result[ATTR_TEMPERATURE] == DEFAULT_TEMPERATURE

    def test_validate_temperature_optional_for_power_off(self):
        """Test temperature is optional for power_off mode."""
        data = {ATTR_OP_MODE: "power_off"}
        result = validate_reservation_temperature(data)
        assert result[ATTR_TEMPERATURE] == DEFAULT_TEMPERATURE

    def test_validate_temperature_provided(self):
        """Test provided temperature is preserved."""
        data = {ATTR_OP_MODE: "heat_pump", ATTR_TEMPERATURE: 140.0}
        result = validate_reservation_temperature(data)
        assert result[ATTR_TEMPERATURE] == 140.0


class TestReservationServices:
    """Tests for reservation service handlers."""

    @pytest.mark.asyncio
    async def test_setup_services_registers_all(self, mock_hass):
        """Test that all 5 services are registered."""
        await _async_setup_services(mock_hass)

        assert mock_hass.services.async_register.call_count == 5

    @pytest.mark.asyncio
    async def test_setup_services_skips_if_already_registered(self, mock_hass):
        """Test that services are not registered twice."""
        mock_hass.services.has_service = MagicMock(return_value=True)

        await _async_setup_services(mock_hass)

        mock_hass.services.async_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_reservation_builds_entry_correctly(
        self, mock_hass, mock_device_registry
    ):
        """Test set_reservation builds a proper reservation entry."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.device_features = {}  # Add device_features
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        # Setup device registry
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        # Get the set_reservation handler
        set_reservation_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "set_reservation":
                set_reservation_handler = call[0][2]
                break

        assert set_reservation_handler is not None

        # Create service call
        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_ENABLED: True,
            ATTR_DAYS: ["Monday", "Wednesday", "Friday"],
            ATTR_HOUR: 6,
            ATTR_MINUTE: 30,
            ATTR_OP_MODE: "energy_saver",
            ATTR_TEMPERATURE: 140,
        }

        # Mock build_reservation_entry in the encoding module
        with patch(
            "nwp500.encoding.build_reservation_entry",
            return_value={
                "enable": 1,
                "week": 42,
                "hour": 6,
                "min": 30,
                "mode": 3,
                "param": 120,
            },
        ) as mock_build:
            await set_reservation_handler(call)

            # Verify build_reservation_entry was called with correct args
            # Library now takes temperature (unit-agnostic) instead of temperature_f
            mock_build.assert_called_once_with(
                enabled=True,
                days=["Monday", "Wednesday", "Friday"],
                hour=6,
                minute=30,
                mode_id=3,  # energy_saver
                temperature=140.0,  # Value directly
                temperature_min=None,
                temperature_max=None,
            )

            # Verify coordinator was called
            mock_coordinator.async_update_reservations.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_reservation_with_device_feature_limits(
        self, mock_hass, mock_device_registry
    ):
        """Test set_reservation respects device feature min/max temperature limits."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}

        # Mock device features with actual temperature limits
        mock_features = MagicMock()
        mock_features.dhw_temperature_min = 90.0
        mock_features.dhw_temperature_max = 160.0
        mock_coordinator.device_features = {"AA:BB:CC:DD:EE:FF": mock_features}
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        # Setup device registry
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        # Get the set_reservation handler
        set_reservation_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "set_reservation":
                set_reservation_handler = call[0][2]
                break

        assert set_reservation_handler is not None

        # Create service call
        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_ENABLED: True,
            ATTR_DAYS: ["Monday"],
            ATTR_HOUR: 12,
            ATTR_MINUTE: 0,
            ATTR_OP_MODE: "heat_pump",
            ATTR_TEMPERATURE: 130,
        }

        # Mock build_reservation_entry in the encoding module
        with patch(
            "nwp500.encoding.build_reservation_entry",
            return_value={
                "enable": 1,
                "week": 2,
                "hour": 12,
                "min": 0,
                "mode": 1,
                "param": 130,
            },
        ) as mock_build:
            await set_reservation_handler(call)

            # Verify build_reservation_entry was called with device limits
            mock_build.assert_called_once_with(
                enabled=True,
                days=["Monday"],
                hour=12,
                minute=0,
                mode_id=1,  # heat_pump
                temperature=130.0,
                temperature_min=90.0,  # from device features
                temperature_max=160.0,  # from device features
            )

            # Verify coordinator was called
            mock_coordinator.async_update_reservations.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_reservation_invalid_mode_raises_error(
        self, mock_hass, mock_device_registry
    ):
        """Test set_reservation raises error for invalid mode."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        set_reservation_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "set_reservation":
                set_reservation_handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_ENABLED: True,
            ATTR_DAYS: ["Monday"],
            ATTR_HOUR: 6,
            ATTR_MINUTE: 0,
            ATTR_OP_MODE: "invalid_mode",
            ATTR_TEMPERATURE: 120,
        }

        with pytest.raises(HomeAssistantError, match="Invalid mode"):
            await set_reservation_handler(call)

    @pytest.mark.asyncio
    async def test_set_reservation_requires_temp_for_heating_modes(
        self, mock_hass, mock_device_registry
    ):
        """Test set_reservation requires temperature for heating modes."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        set_reservation_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "set_reservation":
                set_reservation_handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_ENABLED: True,
            ATTR_DAYS: ["Monday"],
            ATTR_HOUR: 6,
            ATTR_MINUTE: 0,
            ATTR_OP_MODE: "heat_pump",  # Heating mode requires temp
            # ATTR_TEMPERATURE not provided
        }

        with pytest.raises(HomeAssistantError, match="Temperature is required"):
            await set_reservation_handler(call)

    @pytest.mark.asyncio
    async def test_set_reservation_vacation_mode_uses_default_temp(
        self, mock_hass, mock_device_registry
    ):
        """Test set_reservation uses default temperature for vacation mode."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.device_features = {}  # Add device_features
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        set_reservation_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "set_reservation":
                set_reservation_handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_ENABLED: True,
            ATTR_DAYS: ["Monday"],
            ATTR_HOUR: 6,
            ATTR_MINUTE: 0,
            ATTR_OP_MODE: "vacation",
            # ATTR_TEMPERATURE not provided
        }

        with patch("nwp500.encoding.build_reservation_entry") as mock_build:
            await set_reservation_handler(call)

            mock_build.assert_called_once()
            # Verify temperature is DEFAULT_TEMPERATURE
            assert (
                mock_build.call_args.kwargs["temperature"]
                == DEFAULT_TEMPERATURE
            )

    @pytest.mark.asyncio
    async def test_clear_reservations_sends_empty_list(
        self, mock_hass, mock_device_registry
    ):
        """Test clear_reservations sends empty reservation list."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        clear_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "clear_reservations":
                clear_handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}

        await clear_handler(call)

        mock_coordinator.async_update_reservations.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", [], enabled=False
        )

    @pytest.mark.asyncio
    async def test_request_reservations_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test request_reservations calls coordinator method."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.async_request_reservations = AsyncMock(
            return_value=True
        )
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        request_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "request_reservations":
                request_handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}

        await request_handler(call)

        mock_coordinator.async_request_reservations.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_update_reservations_passes_list(
        self, mock_hass, mock_device_registry
    ):
        """Test update_reservations passes reservation list."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        mock_hass.data[DOMAIN]["entry_1"] = mock_coordinator

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        update_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "update_reservations":
                update_handler = call[0][2]
                break

        reservations = [
            {
                "enable": 1,
                "week": 42,
                "hour": 6,
                "min": 30,
                "mode": 3,
                "param": 120,
            }
        ]

        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_RESERVATIONS: reservations,
            ATTR_ENABLED: True,
        }

        await update_handler(call)

        mock_coordinator.async_update_reservations.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", reservations, enabled=True
        )

    @pytest.mark.asyncio
    async def test_device_not_found_raises_error(
        self, mock_hass, mock_device_registry
    ):
        """Test service raises error when device not found."""
        mock_hass.data[DOMAIN]["entry_1"] = MagicMock()

        mock_device_registry.async_get = MagicMock(return_value=None)

        await _async_setup_services(mock_hass)

        request_handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == "request_reservations":
                request_handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "unknown_device"}

        with pytest.raises(HomeAssistantError, match="not found"):
            await request_handler(call)
