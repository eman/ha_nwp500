"""Tests for NWP500 reservation services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.nwp500 import (
    ATTR_DAYS,
    ATTR_DEVICE_ID,
    ATTR_ENABLED,
    ATTR_HOUR,
    ATTR_MINUTE,
    ATTR_OP_MODE,
    ATTR_PERIODS,
    ATTR_RESERVATIONS,
    ATTR_TEMPERATURE,
    SERVICE_CLEAR_RESERVATIONS,
    SERVICE_CONFIGURE_TOU,
    SERVICE_CONFIGURE_TOU_SCHEMA,
    SERVICE_DISABLE_DEMAND_RESPONSE,
    SERVICE_ENABLE_DEMAND_RESPONSE,
    SERVICE_GET_ENERGY_USAGE,
    SERVICE_GET_ENERGY_USAGE_SCHEMA,
    SERVICE_REQUEST_RESERVATIONS,
    SERVICE_REQUEST_TOU,
    SERVICE_RESET_AIR_FILTER,
    SERVICE_SET_RECIRCULATION_MODE,
    SERVICE_SET_RESERVATION,
    SERVICE_SET_VACATION_DAYS,
    SERVICE_TRIGGER_RECIRCULATION,
    SERVICE_UPDATE_RESERVATIONS,
    SERVICE_UPDATE_RESERVATIONS_SCHEMA,
    _async_setup_services,
    _merge_reservation_entry,
    validate_reservation_temperature,
)
from custom_components.nwp500.const import (
    DEFAULT_TEMPERATURE_F,
    DOMAIN,
)
from custom_components.nwp500.coordinator import NWP500DataUpdateCoordinator


def stage_coordinator(mock_hass, coordinator):
    """Expose a coordinator the way a loaded config entry would.

    The integration reads coordinators off entry.runtime_data via
    hass.config_entries.async_entries(DOMAIN), so tests stage them there.
    """
    entry = MagicMock()
    entry.state = ConfigEntryState.LOADED
    entry.runtime_data = coordinator
    mock_hass.config_entries.async_entries = MagicMock(return_value=[entry])
    return entry


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
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
        # Note: Validation doesn't set the default anymore, it's done in the handler
        data = {ATTR_OP_MODE: "vacation"}
        result = validate_reservation_temperature(data)
        assert ATTR_TEMPERATURE not in result

    def test_validate_temperature_optional_for_power_off(self):
        """Test temperature is optional for power_off mode."""
        # Note: Validation doesn't set the default anymore, it's done in the handler
        data = {ATTR_OP_MODE: "power_off"}
        result = validate_reservation_temperature(data)
        assert ATTR_TEMPERATURE not in result

    def test_validate_temperature_provided(self):
        """Test provided temperature is preserved."""
        data = {ATTR_OP_MODE: "heat_pump", ATTR_TEMPERATURE: 140.0}
        result = validate_reservation_temperature(data)
        assert result[ATTR_TEMPERATURE] == 140.0


class TestReservationServices:
    """Tests for reservation service handlers."""

    @pytest.mark.asyncio
    async def test_setup_services_registers_all(self, mock_hass):
        """Test that all 13 services are registered."""
        await _async_setup_services(mock_hass)

        assert mock_hass.services.async_register.call_count == 13

        # Verify all expected services are registered
        registered_services = [
            call[0][1]  # Service name is second arg (after domain)
            for call in mock_hass.services.async_register.call_args_list
        ]
        assert SERVICE_SET_RESERVATION in registered_services
        assert SERVICE_UPDATE_RESERVATIONS in registered_services
        assert SERVICE_CLEAR_RESERVATIONS in registered_services
        assert SERVICE_REQUEST_RESERVATIONS in registered_services
        assert SERVICE_SET_VACATION_DAYS in registered_services
        assert SERVICE_CONFIGURE_TOU in registered_services
        assert SERVICE_REQUEST_TOU in registered_services
        assert SERVICE_ENABLE_DEMAND_RESPONSE in registered_services
        assert SERVICE_DISABLE_DEMAND_RESPONSE in registered_services
        assert SERVICE_RESET_AIR_FILTER in registered_services
        assert SERVICE_SET_RECIRCULATION_MODE in registered_services
        assert SERVICE_TRIGGER_RECIRCULATION in registered_services
        assert SERVICE_GET_ENERGY_USAGE in registered_services

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
        import asyncio

        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)

        mock_coordinator.unit_change_in_progress = False
        mock_coordinator.hass = mock_hass
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.device_features = {}  # Add device_features
        # A fetched-but-empty schedule. Distinct from {} (never fetched),
        # which set_reservation now refuses to write from -- see issue #104.
        mock_coordinator.reservation_schedules = {
            "AA:BB:CC:DD:EE:FF": {"reservation": []}
        }
        mock_coordinator._reservation_lock = (
            asyncio.Lock()
        )  # Add lock for async context
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        mock_coordinator.async_request_reservations = AsyncMock(
            return_value=True
        )
        stage_coordinator(mock_hass, mock_coordinator)

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
            # When device features are not available, fallback constants are used
            mock_build.assert_called_once_with(
                enabled=True,
                days=["Monday", "Wednesday", "Friday"],
                hour=6,
                minute=30,
                mode_id=3,  # energy_saver
                temperature=140.0,  # Value directly
                temperature_min=80,  # Fallback to MIN_TEMPERATURE_F when no device features
                temperature_max=150,  # Fallback to MAX_TEMPERATURE_F when no device features
            )

            # Verify coordinator was called
            mock_coordinator.async_update_reservations.assert_called_once()

        @pytest.mark.asyncio
        async def test_set_reservation_with_device_feature_limits(
            self, mock_hass, mock_device_registry
        ):
            """Test set_reservation respects device feature min/max temperature limits."""
            mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
            mock_coordinator.unit_change_in_progress = False

            mock_coordinator.hass = mock_hass

            mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}

            # Mock device features with actual temperature limits

        mock_features = MagicMock()
        mock_features.dhw_temperature_min = 90.0
        mock_features.dhw_temperature_max = 160.0
        mock_coordinator.device_features = {"AA:BB:CC:DD:EE:FF": mock_features}
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        stage_coordinator(mock_hass, mock_coordinator)

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
        mock_coordinator.unit_change_in_progress = False
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        stage_coordinator(mock_hass, mock_coordinator)

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
        mock_coordinator.unit_change_in_progress = False
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        stage_coordinator(mock_hass, mock_coordinator)

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
        import asyncio

        from homeassistant.const import UnitOfTemperature

        mock_hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT

        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)

        mock_coordinator.unit_change_in_progress = False
        mock_coordinator.hass = mock_hass
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.device_features = {}  # Add device_features
        # A fetched-but-empty schedule. Distinct from {} (never fetched),
        # which set_reservation now refuses to write from -- see issue #104.
        mock_coordinator.reservation_schedules = {
            "AA:BB:CC:DD:EE:FF": {"reservation": []}
        }
        mock_coordinator._reservation_lock = (
            asyncio.Lock()
        )  # Add lock for async context
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        stage_coordinator(mock_hass, mock_coordinator)

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        mock_coordinator.async_request_reservations = AsyncMock(
            return_value=True
        )

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
            # Verify temperature is DEFAULT_TEMPERATURE_F (since we mocked HA as Fahrenheit)
            assert (
                mock_build.call_args.kwargs["temperature"]
                == DEFAULT_TEMPERATURE_F
            )

    @pytest.mark.asyncio
    async def test_clear_reservations_sends_empty_list(
        self, mock_hass, mock_device_registry
    ):
        """Test clear_reservations sends empty reservation list."""
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.unit_change_in_progress = False
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        stage_coordinator(mock_hass, mock_coordinator)

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
        mock_coordinator.unit_change_in_progress = False
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.async_request_reservations = AsyncMock(
            return_value=True
        )
        stage_coordinator(mock_hass, mock_coordinator)

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
        mock_coordinator.unit_change_in_progress = False
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        stage_coordinator(mock_hass, mock_coordinator)

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
        stage_coordinator(mock_hass, MagicMock())

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


class TestTouAndVacationServices:
    """Tests for TOU schedule and vacation day services."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_hass, mock_device_registry):
        """Set up common test fixtures."""
        self.mock_hass = mock_hass
        self.mock_device_registry = mock_device_registry
        self.mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        self.mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        stage_coordinator(mock_hass, self.mock_coordinator)

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

    @pytest.mark.asyncio
    async def test_set_vacation_days_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test set_vacation_days calls coordinator with correct args."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=True)

        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_SET_VACATION_DAYS:
                handler = call[0][2]
                break

        assert handler is not None
        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123", ATTR_DAYS: 7}
        await handler(call)

        self.mock_coordinator.async_send_command.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", "set_vacation_days", days=7
        )

    @pytest.mark.asyncio
    async def test_set_vacation_days_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test set_vacation_days raises HomeAssistantError on failure."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=False)

        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_SET_VACATION_DAYS:
                handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123", ATTR_DAYS: 3}
        with pytest.raises(
            HomeAssistantError, match="Failed to set vacation days"
        ):
            await handler(call)

    @pytest.mark.asyncio
    async def test_configure_tou_schedule_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test configure_tou_schedule calls coordinator with converted periods."""
        self.mock_coordinator.async_configure_tou_schedule = AsyncMock(
            return_value=True
        )

        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_CONFIGURE_TOU:
                handler = call[0][2]
                break

        assert handler is not None
        period = {
            "season": 1,
            "week": 62,
            "start_hour": 8,
            "start_minute": 0,
            "end_hour": 22,
            "end_minute": 0,
            "price_min": 10,
            "price_max": 100,
            "decimal_point": 2,
        }
        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_PERIODS: [period],
            ATTR_ENABLED: True,
        }
        await handler(call)

        self.mock_coordinator.async_configure_tou_schedule.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF",
            [
                {
                    "season": 1,
                    "week": 62,
                    "startHour": 8,
                    "startMinute": 0,
                    "endHour": 22,
                    "endMinute": 0,
                    "priceMin": 10,
                    "priceMax": 100,
                    "decimalPoint": 2,
                }
            ],
            enabled=True,
        )

    @pytest.mark.asyncio
    async def test_configure_tou_schedule_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test configure_tou_schedule raises HomeAssistantError on failure."""
        self.mock_coordinator.async_configure_tou_schedule = AsyncMock(
            return_value=False
        )

        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_CONFIGURE_TOU:
                handler = call[0][2]
                break

        period = {
            "season": 0,
            "week": 0,
            "start_hour": 0,
            "start_minute": 0,
            "end_hour": 1,
            "end_minute": 0,
            "price_min": 0,
            "price_max": 1,
            "decimal_point": 0,
        }
        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_PERIODS: [period],
            ATTR_ENABLED: False,
        }
        with pytest.raises(HomeAssistantError, match="Failed to configure TOU"):
            await handler(call)

    @pytest.mark.asyncio
    async def test_request_tou_settings_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test request_tou_settings calls coordinator."""
        self.mock_coordinator.async_request_tou_settings = AsyncMock(
            return_value=True
        )

        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_REQUEST_TOU:
                handler = call[0][2]
                break

        assert handler is not None
        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        await handler(call)

        self.mock_coordinator.async_request_tou_settings.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_request_tou_settings_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test request_tou_settings raises HomeAssistantError on failure."""
        self.mock_coordinator.async_request_tou_settings = AsyncMock(
            return_value=False
        )

        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_REQUEST_TOU:
                handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        with pytest.raises(HomeAssistantError, match="Failed to request TOU"):
            await handler(call)


class TestDemandResponseAndRecirculationServices:
    """Tests for demand response and recirculation services."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_hass, mock_device_registry):
        """Set up common test fixtures."""
        self.mock_hass = mock_hass
        self.mock_device_registry = mock_device_registry
        self.mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        self.mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        stage_coordinator(mock_hass, self.mock_coordinator)

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

    @pytest.mark.asyncio
    async def test_enable_demand_response_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test enable_demand_response calls coordinator."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=True)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_ENABLE_DEMAND_RESPONSE:
                handler = call[0][2]
                break

        assert handler is not None
        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        await handler(call)

        self.mock_coordinator.async_send_command.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", "enable_demand_response"
        )

    @pytest.mark.asyncio
    async def test_enable_demand_response_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test enable_demand_response raises HomeAssistantError on failure."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=False)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_ENABLE_DEMAND_RESPONSE:
                handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        with pytest.raises(
            HomeAssistantError, match="Failed to enable demand response"
        ):
            await handler(call)

    @pytest.mark.asyncio
    async def test_disable_demand_response_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test disable_demand_response calls coordinator."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=True)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_DISABLE_DEMAND_RESPONSE:
                handler = call[0][2]
                break

        assert handler is not None
        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        await handler(call)

        self.mock_coordinator.async_send_command.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", "disable_demand_response"
        )

    @pytest.mark.asyncio
    async def test_disable_demand_response_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test disable_demand_response raises HomeAssistantError on failure."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=False)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_DISABLE_DEMAND_RESPONSE:
                handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        with pytest.raises(
            HomeAssistantError, match="Failed to disable demand response"
        ):
            await handler(call)

    @pytest.mark.asyncio
    async def test_reset_air_filter_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test reset_air_filter calls coordinator."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=True)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_RESET_AIR_FILTER:
                handler = call[0][2]
                break

        assert handler is not None
        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        await handler(call)

        self.mock_coordinator.async_send_command.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", "reset_air_filter"
        )

    @pytest.mark.asyncio
    async def test_reset_air_filter_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test reset_air_filter raises HomeAssistantError on failure."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=False)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_RESET_AIR_FILTER:
                handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        with pytest.raises(
            HomeAssistantError, match="Failed to reset air filter"
        ):
            await handler(call)

    @pytest.mark.asyncio
    async def test_set_recirculation_mode_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test set_recirculation_mode calls coordinator with correct mode."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=True)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_SET_RECIRCULATION_MODE:
                handler = call[0][2]
                break

        assert handler is not None
        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123", "mode": 2}
        await handler(call)

        self.mock_coordinator.async_send_command.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", "set_recirculation_mode", mode=2
        )

    @pytest.mark.asyncio
    async def test_set_recirculation_mode_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test set_recirculation_mode raises HomeAssistantError on failure."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=False)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_SET_RECIRCULATION_MODE:
                handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123", "mode": 1}
        with pytest.raises(
            HomeAssistantError, match="Failed to set recirculation mode"
        ):
            await handler(call)

    @pytest.mark.asyncio
    async def test_trigger_recirculation_calls_coordinator(
        self, mock_hass, mock_device_registry
    ):
        """Test trigger_recirculation calls coordinator."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=True)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_TRIGGER_RECIRCULATION:
                handler = call[0][2]
                break

        assert handler is not None
        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        await handler(call)

        self.mock_coordinator.async_send_command.assert_called_once_with(
            "AA:BB:CC:DD:EE:FF", "trigger_recirculation"
        )

    @pytest.mark.asyncio
    async def test_trigger_recirculation_raises_on_failure(
        self, mock_hass, mock_device_registry
    ):
        """Test trigger_recirculation raises HomeAssistantError on failure."""
        self.mock_coordinator.async_send_command = AsyncMock(return_value=False)
        await _async_setup_services(mock_hass)

        handler = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_TRIGGER_RECIRCULATION:
                handler = call[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        with pytest.raises(
            HomeAssistantError, match="Failed to trigger recirculation"
        ):
            await handler(call)


class TestTOUWeekBitfieldValidation:
    """Validate the configure_tou_schedule 'week' bitfield bounds.

    The TOU 'week' field uses the same weekday bitfield as reservations --
    Sun=bit7 (128) through Sat=bit1 (2) -- so Sunday-inclusive masks and the
    every-day mask (254) must be accepted. See issue #106.
    """

    @staticmethod
    def _period(week: int) -> dict[str, int]:
        return {
            "season": 4095,
            "week": week,
            "start_hour": 0,
            "start_minute": 0,
            "end_hour": 6,
            "end_minute": 0,
            "price_min": 10,
            "price_max": 20,
            "decimal_point": 2,
        }

    @pytest.mark.parametrize(
        "week",
        [
            0,  # no days
            2,  # Saturday only (bit 1)
            128,  # Sunday only (bit 7) -- rejected before the fix
            130,  # Sunday + Saturday
            254,  # every day -- rejected before the fix
        ],
    )
    def test_accepts_valid_week_bitfields(self, week):
        """Sunday-inclusive and every-day masks must validate."""
        result = SERVICE_CONFIGURE_TOU_SCHEMA(
            {
                ATTR_DEVICE_ID: "device_123",
                ATTR_PERIODS: [self._period(week)],
            }
        )
        assert result[ATTR_PERIODS][0]["week"] == week

    @pytest.mark.parametrize("week", [-1, 255, 256])
    def test_rejects_out_of_range_week(self, week):
        """Values outside the 0-254 bitfield range are still rejected."""
        with pytest.raises(vol.Invalid):
            SERVICE_CONFIGURE_TOU_SCHEMA(
                {
                    ATTR_DEVICE_ID: "device_123",
                    ATTR_PERIODS: [self._period(week)],
                }
            )

    def test_reservation_and_tou_week_bounds_agree(self):
        """Both services share one weekday bitfield, so bounds must match."""
        reservation = {
            "enable": 2,
            "week": 254,
            "hour": 6,
            "min": 30,
            "mode": 4,
            "param": 120,
        }
        SERVICE_UPDATE_RESERVATIONS_SCHEMA(
            {
                ATTR_DEVICE_ID: "device_123",
                ATTR_RESERVATIONS: [reservation],
            }
        )
        SERVICE_CONFIGURE_TOU_SCHEMA(
            {
                ATTR_DEVICE_ID: "device_123",
                ATTR_PERIODS: [self._period(254)],
            }
        )


class TestReservationMergeSemantics:
    """`set_reservation` is documented "create or update", not append.

    Before issue #104 it only appended, so repeating a call -- or programming
    a different mode at a day/time already scheduled -- accumulated
    conflicting entries with no way to update one in place.
    """

    @staticmethod
    def _entry(week=42, hour=6, minute=30, mode=3, param=120):
        return {
            "enable": 2,
            "week": week,
            "hour": hour,
            "min": minute,
            "mode": mode,
            "param": param,
        }

    def test_replaces_entry_in_the_same_slot(self):
        """Same days+time means the same reservation, so it is replaced."""
        existing = [self._entry(mode=3, param=120)]
        new = self._entry(mode=4, param=140)

        result = _merge_reservation_entry(existing, new)

        assert result == [new]

    def test_appends_when_slot_is_free(self):
        """A different day/time is a new reservation, so it is added."""
        existing = [self._entry(hour=6)]
        new = self._entry(hour=18)

        result = _merge_reservation_entry(existing, new)

        assert result == [existing[0], new]

    def test_repeated_identical_calls_do_not_accumulate(self):
        """Calling twice with the same slot leaves one entry, not two."""
        entry = self._entry()

        result = _merge_reservation_entry([], entry)
        result = _merge_reservation_entry(result, entry)
        result = _merge_reservation_entry(result, entry)

        assert result == [entry]

    def test_preserves_unrelated_entries(self):
        """Other reservations survive an update to one slot."""
        morning = self._entry(hour=6)
        evening = self._entry(hour=18)
        weekend = self._entry(week=130, hour=9)
        updated_evening = self._entry(hour=18, mode=1, param=100)

        result = _merge_reservation_entry(
            [morning, evening, weekend], updated_evening
        )

        assert morning in result
        assert weekend in result
        assert updated_evening in result
        assert evening not in result
        assert len(result) == 3

    def test_does_not_mutate_the_input_list(self):
        """The caller's list is left alone."""
        existing = [self._entry(hour=6)]
        snapshot = [dict(e) for e in existing]

        _merge_reservation_entry(existing, self._entry(hour=18))

        assert existing == snapshot


class TestSetReservationRefusesUnfetchedWrite:
    """Writing is a full-list replacement, so an empty cache must not be used.

    Before issue #104 an unfetched cache only logged a warning and then wrote
    a single-entry list, wiping every reservation on the device.
    """

    @staticmethod
    def _coordinator(mock_hass, *, fetch_result):
        import asyncio

        coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)

        coordinator.unit_change_in_progress = False
        coordinator.hass = mock_hass
        coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        coordinator.device_features = {}
        coordinator.reservation_schedules = {}  # never fetched
        coordinator._reservation_lock = asyncio.Lock()
        coordinator.async_update_reservations = AsyncMock(return_value=True)
        coordinator.async_request_reservations = AsyncMock(return_value=True)
        coordinator.async_fetch_reservations = AsyncMock(
            return_value=fetch_result
        )
        return coordinator

    @staticmethod
    def _call():
        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_ENABLED: True,
            ATTR_DAYS: ["Monday"],
            ATTR_HOUR: 6,
            ATTR_MINUTE: 30,
            ATTR_OP_MODE: "energy_saver",
            ATTR_TEMPERATURE: 140,
        }
        return call

    @staticmethod
    async def _handler(mock_hass):
        await _async_setup_services(mock_hass)
        for registered in mock_hass.services.async_register.call_args_list:
            if registered[0][1] == SERVICE_SET_RESERVATION:
                return registered[0][2]
        return None

    @pytest.mark.asyncio
    async def test_fetches_when_cache_is_empty(
        self, mock_hass, mock_device_registry
    ):
        """An unfetched cache triggers a read before the write."""
        coordinator = self._coordinator(
            mock_hass, fetch_result={"reservation": []}
        )
        stage_coordinator(mock_hass, coordinator)
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        handler = await self._handler(mock_hass)
        await handler(self._call())

        coordinator.async_fetch_reservations.assert_awaited_once()
        coordinator.async_update_reservations.assert_called_once()

    @pytest.mark.asyncio
    async def test_refuses_to_write_when_fetch_fails(
        self, mock_hass, mock_device_registry
    ):
        """If the schedule can't be read, nothing is written.

        This is the data-loss guard: previously a single-entry list was
        pushed as a full replacement, wiping the device's reservations.
        """
        coordinator = self._coordinator(mock_hass, fetch_result=None)
        stage_coordinator(mock_hass, coordinator)
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        handler = await self._handler(mock_hass)

        with pytest.raises(HomeAssistantError, match="Refusing to write"):
            await handler(self._call())

        coordinator.async_update_reservations.assert_not_called()


class TestSetReservationPreservesGlobalSwitch:
    """The device's global reservation switch is not this service's to change.

    `set_reservation`'s `enabled` field is entry-level. The schedule-wide
    switch is `reservation_use` (device bool: 2=on, 1=off), and the handler
    used to force it on with a hardcoded `enabled=True`.
    """

    @staticmethod
    def _coordinator(mock_hass, schedule):
        import asyncio

        coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)

        coordinator.unit_change_in_progress = False
        coordinator.hass = mock_hass
        coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        coordinator.device_features = {}
        coordinator.reservation_schedules = {"AA:BB:CC:DD:EE:FF": schedule}
        coordinator._reservation_lock = asyncio.Lock()
        coordinator.async_update_reservations = AsyncMock(return_value=True)
        coordinator.async_request_reservations = AsyncMock(return_value=True)
        coordinator.async_fetch_reservations = AsyncMock(return_value=schedule)
        return coordinator

    async def _run(self, mock_hass, mock_device_registry, schedule):
        coordinator = self._coordinator(mock_hass, schedule)
        stage_coordinator(mock_hass, coordinator)
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)
        handler = None
        for registered in mock_hass.services.async_register.call_args_list:
            if registered[0][1] == SERVICE_SET_RESERVATION:
                handler = registered[0][2]
                break

        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            ATTR_ENABLED: True,
            ATTR_DAYS: ["Monday"],
            ATTR_HOUR: 6,
            ATTR_MINUTE: 30,
            ATTR_OP_MODE: "energy_saver",
            ATTR_TEMPERATURE: 140,
        }
        await handler(call)
        return coordinator

    @pytest.mark.asyncio
    async def test_keeps_the_switch_off_when_device_has_it_off(
        self, mock_hass, mock_device_registry
    ):
        """A disabled reservation system stays disabled."""
        coordinator = await self._run(
            mock_hass,
            mock_device_registry,
            {"reservation": [], "reservation_use": 1},
        )

        _, kwargs = coordinator.async_update_reservations.call_args
        assert kwargs["enabled"] is False

    @pytest.mark.asyncio
    async def test_keeps_the_switch_on_when_device_has_it_on(
        self, mock_hass, mock_device_registry
    ):
        """An enabled reservation system stays enabled."""
        coordinator = await self._run(
            mock_hass,
            mock_device_registry,
            {"reservation": [], "reservation_use": 2},
        )

        _, kwargs = coordinator.async_update_reservations.call_args
        assert kwargs["enabled"] is True

    @pytest.mark.asyncio
    async def test_enables_when_device_did_not_report_the_switch(
        self, mock_hass, mock_device_registry
    ):
        """With no reported value there is nothing to preserve."""
        coordinator = await self._run(
            mock_hass, mock_device_registry, {"reservation": []}
        )

        _, kwargs = coordinator.async_update_reservations.call_args
        assert kwargs["enabled"] is True


class TestEnergyUsageService:
    """The on-demand energy report.

    Unlike every other service here it answers the caller instead of acting
    on the device, so registration and the returned payload both matter.
    """

    @staticmethod
    def _handler(mock_hass):
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_GET_ENERGY_USAGE:
                return call[0][2]
        return None

    @staticmethod
    def _coordinator(mock_hass, response):
        coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        coordinator.unit_change_in_progress = False
        coordinator.hass = mock_hass
        coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        coordinator.async_fetch_energy_usage = AsyncMock(return_value=response)
        stage_coordinator(mock_hass, coordinator)
        return coordinator

    @pytest.mark.asyncio
    async def test_registered_as_a_response_only_service(self, mock_hass):
        """Home Assistant refuses to return data from a plain service."""
        from homeassistant.core import SupportsResponse

        await _async_setup_services(mock_hass)

        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_GET_ENERGY_USAGE:
                assert call[1]["supports_response"] is SupportsResponse.ONLY
                break
        else:  # pragma: no cover - the assertion above should have run
            pytest.fail("get_energy_usage was not registered")

    @pytest.mark.asyncio
    async def test_returns_a_report_for_the_requested_period(
        self, mock_hass, mock_device_registry
    ):
        coordinator = self._coordinator(
            mock_hass,
            {
                "total": {"heat_pump_usage": 1000, "heat_element_usage": 0},
                "usage": [
                    {
                        "year": 2026,
                        "month": 3,
                        "data": [
                            {"heat_pump_usage": 1000, "heat_element_usage": 0}
                        ],
                    }
                ],
            },
        )
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)
        handler = self._handler(mock_hass)
        assert handler is not None

        call = MagicMock(spec=ServiceCall)
        call.data = {
            ATTR_DEVICE_ID: "device_123",
            "year": 2026,
            "months": [3],
        }
        report = await handler(call)

        coordinator.async_fetch_energy_usage.assert_awaited_once_with(
            "AA:BB:CC:DD:EE:FF", 2026, [3]
        )
        assert report["total"]["heat_pump_kwh"] == 1.0
        assert report["months"][0]["days"][0]["date"] == "2026-03-01"

    @pytest.mark.asyncio
    async def test_defaults_to_the_current_month(
        self, mock_hass, mock_device_registry
    ):
        """A request naming no period means "the month I am in"."""
        from homeassistant.util import dt as dt_util

        coordinator = self._coordinator(mock_hass, {"total": {}, "usage": []})
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)
        handler = self._handler(mock_hass)

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}
        await handler(call)

        today = dt_util.now()
        coordinator.async_fetch_energy_usage.assert_awaited_once_with(
            "AA:BB:CC:DD:EE:FF", today.year, [today.month]
        )

    @pytest.mark.asyncio
    async def test_a_silent_device_is_an_error_not_an_empty_report(
        self, mock_hass, mock_device_registry
    ):
        """An empty report would read as "you used nothing"."""
        self._coordinator(mock_hass, None)
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)
        handler = self._handler(mock_hass)

        call = MagicMock(spec=ServiceCall)
        call.data = {ATTR_DEVICE_ID: "device_123"}

        with pytest.raises(HomeAssistantError, match="did not report"):
            await handler(call)

    def test_months_from_the_ui_picker_are_accepted(self):
        """The month dropdown submits strings, the device wants ints.

        `services.yaml` renders `months` as a multi-select, and a select
        selector's values are strings -- so the schema has to coerce them
        or every call made from the UI would be rejected.
        """
        validated = SERVICE_GET_ENERGY_USAGE_SCHEMA(
            {ATTR_DEVICE_ID: "device_123", "months": ["7", "8"]}
        )

        assert validated["months"] == [7, 8]

    def test_a_month_outside_the_calendar_is_rejected(self):
        with pytest.raises(vol.Invalid):
            SERVICE_GET_ENERGY_USAGE_SCHEMA(
                {ATTR_DEVICE_ID: "device_123", "months": [13]}
            )


class TestUnitSystemChangeGuard:
    """Temperature-bearing services must refuse mid-transition.

    Between the coordinator, the MQTT manager and the library's unit context
    being brought into line, a temperature sent now could be read in the
    wrong scale.
    """

    @staticmethod
    async def _handler_for(mock_hass, mock_device_registry, service_name):
        mock_coordinator = MagicMock(spec=NWP500DataUpdateCoordinator)
        mock_coordinator.unit_change_in_progress = True
        mock_coordinator.hass = mock_hass
        mock_coordinator.data = {"AA:BB:CC:DD:EE:FF": {}}
        mock_coordinator.async_update_reservations = AsyncMock(
            return_value=True
        )
        stage_coordinator(mock_hass, mock_coordinator)

        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "AA:BB:CC:DD:EE:FF")}
        mock_device_registry.async_get = MagicMock(return_value=device_entry)

        await _async_setup_services(mock_hass)

        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == service_name:
                return call[0][2], mock_coordinator
        raise AssertionError(f"{service_name} was not registered")

    @pytest.mark.asyncio
    async def test_set_reservation_refuses(
        self, mock_hass, mock_device_registry
    ):
        """set_reservation carries a temperature."""
        handler, coordinator = await self._handler_for(
            mock_hass, mock_device_registry, "set_reservation"
        )

        call = MagicMock(spec=ServiceCall)
        call.data = {
            "device_id": "test_device",
            "enabled": True,
            "days": ["Monday"],
            "hour": 6,
            "minute": 0,
            "mode": "heat_pump",
            "temperature": 125,
        }

        with pytest.raises(HomeAssistantError, match="unit system change"):
            await handler(call)

    @pytest.mark.asyncio
    async def test_update_reservations_refuses(
        self, mock_hass, mock_device_registry
    ):
        """update_reservations entries carry temperatures too.

        Only set_reservation used to check, which left the bulk write -- the
        one that replaces every entry at once -- unguarded.
        """
        handler, coordinator = await self._handler_for(
            mock_hass, mock_device_registry, "update_reservations"
        )

        call = MagicMock(spec=ServiceCall)
        call.data = {
            "device_id": "test_device",
            "enabled": True,
            "reservations": [],
        }

        with pytest.raises(HomeAssistantError, match="unit system change"):
            await handler(call)

        coordinator.async_update_reservations.assert_not_called()
