"""Tests for __init__.py module."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.nwp500 import (
    MODE_TO_DHW_ID,
    SERVICE_CLEAR_RESERVATIONS,
    SERVICE_CONFIGURE_TOU,
    SERVICE_DISABLE_DEMAND_RESPONSE,
    SERVICE_ENABLE_DEMAND_RESPONSE,
    SERVICE_REQUEST_RESERVATIONS,
    SERVICE_REQUEST_TOU,
    SERVICE_RESET_AIR_FILTER,
    SERVICE_SET_RECIRCULATION_MODE,
    SERVICE_SET_RESERVATION,
    SERVICE_SET_VACATION_DAYS,
    SERVICE_TRIGGER_RECIRCULATION,
    SERVICE_UPDATE_RESERVATIONS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.nwp500.const import DOMAIN


@pytest.fixture(autouse=True)
def _no_registry_entries():
    """Stub the entity registry lookup done during setup.

    These tests drive ``async_setup_entry`` with a ``MagicMock`` hass, which
    cannot back a real ``EntityRegistry``. Tests that care about the stale
    entity cleanup patch this again with their own entries.
    """
    with patch(
        "custom_components.nwp500.er.async_entries_for_config_entry",
        return_value=[],
    ):
        yield


class TestInit:
    """Tests for component initialization."""

    pass


class TestModeMapping:
    """Tests for mode mapping constants."""

    def test_mode_to_dhw_id_contains_all_modes(self):
        """Test all expected modes are in the mapping."""
        expected_modes = [
            "heat_pump",
            "electric",
            "energy_saver",
            "high_demand",
            "vacation",
            "power_off",
        ]
        for mode in expected_modes:
            assert mode in MODE_TO_DHW_ID

    def test_mode_values_are_correct(self):
        """Test mode values match DHW operation setting IDs."""
        assert MODE_TO_DHW_ID["heat_pump"] == 1
        assert MODE_TO_DHW_ID["electric"] == 2
        assert MODE_TO_DHW_ID["energy_saver"] == 3
        assert MODE_TO_DHW_ID["high_demand"] == 4
        assert MODE_TO_DHW_ID["vacation"] == 5
        assert MODE_TO_DHW_ID["power_off"] == 6


@pytest.mark.asyncio
async def test_async_setup_entry_update_failed():
    """Test setup entry re-raises ConfigEntryNotReady from first refresh."""
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # async_config_entry_first_refresh always raises ConfigEntryNotReady (never UpdateFailed)
    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryNotReady("Connection failed")
        )
        mock_coordinator.async_shutdown = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(mock_hass, mock_entry)

        # A failed first refresh may already have opened an auth session and
        # an MQTT connection; HA retries with a fresh coordinator, so this one
        # must be torn down or every retry strands a live connection.
        mock_coordinator.async_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_setup_entry_stores_coordinator():
    """Test setup entry stores the coordinator on entry.runtime_data."""
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Mock coordinator that succeeds
    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True
        assert mock_entry.runtime_data is mock_coordinator


def test_removes_energy_entities_dropped_in_9_3_0():
    """Stale energy sensors are dropped from the registry, others are kept."""
    from custom_components.nwp500 import _async_remove_stale_energy_entities

    def entry(unique_id: str) -> MagicMock:
        item = MagicMock()
        item.unique_id = unique_id
        item.entity_id = f"sensor.{unique_id}"
        return item

    stale = [
        entry("AABBCCDDEEFF_total_energy_capacity"),
        entry("AABBCCDDEEFF_available_energy_capacity"),
    ]
    kept = [
        entry("AABBCCDDEEFF_usable_energy"),
        entry("AABBCCDDEEFF_energy_to_setpoint"),
        entry("AABBCCDDEEFF_full_recovery_energy"),
        entry("AABBCCDDEEFF_current_inst_power"),
    ]

    mock_registry = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    with (
        patch(
            "custom_components.nwp500.er.async_get",
            return_value=mock_registry,
        ),
        patch(
            "custom_components.nwp500.er.async_entries_for_config_entry",
            return_value=[*stale, *kept],
        ),
    ):
        _async_remove_stale_energy_entities(MagicMock(), mock_entry)

    removed = [
        call.args[0] for call in mock_registry.async_remove.call_args_list
    ]
    assert removed == [e.entity_id for e in stale]


@pytest.mark.asyncio
async def test_async_setup_entry_registers_services():
    """Test setup entry registers reservation services."""
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_entry(mock_hass, mock_entry)

        # Verify all 16 services were registered
        assert mock_hass.services.async_register.call_count == 16

        # Get all service names that were registered
        registered_services = [
            call[0][1]
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


@pytest.mark.asyncio
async def test_async_setup_entry_skips_service_registration_if_exists():
    """Test setup entry skips registration if services already exist."""
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=True)
    mock_hass.services.async_register = MagicMock()

    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    with patch(
        "custom_components.nwp500.NWP500DataUpdateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_entry(mock_hass, mock_entry)

        # Services should not be registered again
        mock_hass.services.async_register.assert_not_called()


@pytest.mark.asyncio
async def test_async_unload_entry_cleanup():
    """Test unload entry performs proper cleanup."""
    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    mock_coordinator = MagicMock()
    mock_coordinator.async_shutdown = AsyncMock()
    mock_entry.runtime_data = mock_coordinator

    # Mock successful platform unload
    mock_hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=True
    )

    result = await async_unload_entry(mock_hass, mock_entry)

    assert result is True
    # Verify coordinator was shut down
    mock_coordinator.async_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_unload_entry_removes_services_when_last():
    """Test unload removes services when last entry is unloaded."""
    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    mock_coordinator = MagicMock()
    mock_coordinator.async_shutdown = AsyncMock()
    mock_entry.runtime_data = mock_coordinator

    mock_hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=True
    )
    mock_hass.services.async_remove = MagicMock()

    await async_unload_entry(mock_hass, mock_entry)

    # All 16 services should be removed
    assert mock_hass.services.async_remove.call_count == 16


@pytest.mark.asyncio
async def test_migrate_entry_runs_stale_sweep_once():
    """A pre-1.2 entry gets the sweep, then is stamped so it never repeats."""
    from custom_components.nwp500 import async_migrate_entry

    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.version = 1
    mock_entry.minor_version = 1

    with patch(
        "custom_components.nwp500._async_remove_stale_energy_entities"
    ) as mock_sweep:
        assert await async_migrate_entry(mock_hass, mock_entry) is True

    mock_sweep.assert_called_once_with(mock_hass, mock_entry)
    mock_hass.config_entries.async_update_entry.assert_called_once_with(
        mock_entry, minor_version=2
    )


@pytest.mark.asyncio
async def test_migrate_entry_is_a_noop_when_current():
    """An already-migrated entry is left alone on every later start."""
    from custom_components.nwp500 import async_migrate_entry

    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.version = 1
    mock_entry.minor_version = 2

    with patch(
        "custom_components.nwp500._async_remove_stale_energy_entities"
    ) as mock_sweep:
        assert await async_migrate_entry(mock_hass, mock_entry) is True

    mock_sweep.assert_not_called()
    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_migrate_entry_refuses_a_downgrade():
    """An entry written by a newer major version is not touched."""
    from custom_components.nwp500 import async_migrate_entry

    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.version = 2
    mock_entry.minor_version = 1

    with patch(
        "custom_components.nwp500._async_remove_stale_energy_entities"
    ) as mock_sweep:
        assert await async_migrate_entry(mock_hass, mock_entry) is False

    mock_sweep.assert_not_called()
    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_setup_entry_no_longer_sweeps_on_every_start():
    """The stale sweep is migration work, not per-setup work."""
    mock_hass = MagicMock()
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_entry = MagicMock()

    with (
        patch(
            "custom_components.nwp500.NWP500DataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch(
            "custom_components.nwp500._async_remove_stale_energy_entities"
        ) as mock_sweep,
    ):
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator_class.return_value = mock_coordinator

        assert await async_setup_entry(mock_hass, mock_entry) is True

    mock_sweep.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_stats_frontend_assets_off_the_event_loop():
    """Asset existence is stat-ed in an executor, not on the event loop."""
    from custom_components.nwp500 import async_setup

    mock_hass = MagicMock()
    mock_hass.async_add_executor_job = AsyncMock(return_value=[])
    mock_hass.http.async_register_static_paths = AsyncMock()

    assert await async_setup(mock_hass, {}) is True

    mock_hass.async_add_executor_job.assert_awaited_once()
    # Nothing present -> nothing registered.
    mock_hass.http.async_register_static_paths.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_registers_assets_independently():
    """A missing schedule card must not suppress the other frontend assets.

    The previous shape nested every registration inside `if CARD_PATH.is_file()`,
    so one absent file silently took the visual card and its image with it.
    """
    from custom_components.nwp500 import (
        VISUAL_CARD_PATH,
        VISUAL_CARD_URL,
        VISUAL_IMAGE_PATH,
        VISUAL_IMAGE_URL,
        async_setup,
    )

    mock_hass = MagicMock()
    # Schedule card absent; visual card and its image present.
    mock_hass.async_add_executor_job = AsyncMock(
        return_value=[
            (VISUAL_CARD_URL, VISUAL_CARD_PATH, True),
            (VISUAL_IMAGE_URL, VISUAL_IMAGE_PATH, False),
        ]
    )
    mock_hass.http.async_register_static_paths = AsyncMock()

    with patch("custom_components.nwp500.add_extra_js_url") as mock_add_js:
        assert await async_setup(mock_hass, {}) is True

    configs = mock_hass.http.async_register_static_paths.await_args[0][0]
    assert [c.url_path for c in configs] == [
        VISUAL_CARD_URL,
        VISUAL_IMAGE_URL,
    ]
    # Only the JS asset is added to the frontend; the PNG is served, not loaded.
    mock_add_js.assert_called_once_with(mock_hass, VISUAL_CARD_URL)


@pytest.mark.asyncio
async def test_async_setup_registers_every_asset_when_all_present():
    """With all three files present, both JS assets reach the frontend."""
    from custom_components.nwp500 import (
        CARD_PATH,
        CARD_URL,
        VISUAL_CARD_PATH,
        VISUAL_CARD_URL,
        VISUAL_IMAGE_PATH,
        VISUAL_IMAGE_URL,
        async_setup,
    )

    mock_hass = MagicMock()
    mock_hass.async_add_executor_job = AsyncMock(
        return_value=[
            (CARD_URL, CARD_PATH, True),
            (VISUAL_CARD_URL, VISUAL_CARD_PATH, True),
            (VISUAL_IMAGE_URL, VISUAL_IMAGE_PATH, False),
        ]
    )
    mock_hass.http.async_register_static_paths = AsyncMock()

    with patch("custom_components.nwp500.add_extra_js_url") as mock_add_js:
        assert await async_setup(mock_hass, {}) is True

    configs = mock_hass.http.async_register_static_paths.await_args[0][0]
    assert len(configs) == 3
    assert [c.args[1] for c in mock_add_js.call_args_list] == [
        CARD_URL,
        VISUAL_CARD_URL,
    ]


# --- External control (issue #158): the set-up hook -------------------------

# The platforms the release before the feature forwarded. Spelled out rather
# than read from PLATFORMS so that a change to the disabled path fails here.
_PRE_FEATURE_PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.WATER_HEATER,
    Platform.SWITCH,
    Platform.NUMBER,
]


def _hass_for_setup() -> MagicMock:
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()
    return mock_hass


def _entry(options: dict) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = options
    return entry


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"control_enabled": False}])
async def test_setup_with_control_off_is_the_pre_feature_setup(options):
    """Spec section 1.1.5: with the feature off, set-up is unchanged.

    The same platforms are forwarded, nothing of the feature is imported,
    and nothing of it is registered or stored.
    """
    mock_hass = _hass_for_setup()
    mock_entry = _entry(options)
    control_modules = [
        name
        for name in sys.modules
        if name.startswith("custom_components.nwp500.control")
    ]

    with (
        patch.dict(sys.modules),
        patch("custom_components.nwp500.NWP500DataUpdateCoordinator") as cls,
    ):
        for name in control_modules:
            del sys.modules[name]
        cls.return_value.async_config_entry_first_refresh = AsyncMock()

        assert await async_setup_entry(mock_hass, mock_entry) is True

        assert not [
            name
            for name in sys.modules
            if name.startswith("custom_components.nwp500.control")
        ]

    mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        mock_entry, _PRE_FEATURE_PLATFORMS
    )
    assert mock_hass.data[DOMAIN][mock_entry.entry_id] == {
        "platforms": _PRE_FEATURE_PLATFORMS
    }
    mock_entry.add_update_listener.assert_called_once()


@pytest.mark.asyncio
async def test_setup_with_control_on_starts_the_feature():
    mock_hass = _hass_for_setup()
    mock_entry = _entry({"control_enabled": True})

    with (
        patch("custom_components.nwp500.NWP500DataUpdateCoordinator") as cls,
        patch(
            "custom_components.nwp500.control.async_setup_control",
            new=AsyncMock(return_value=[Platform.BUTTON]),
        ) as setup_control,
    ):
        cls.return_value.async_config_entry_first_refresh = AsyncMock()

        assert await async_setup_entry(mock_hass, mock_entry) is True

    setup_control.assert_awaited_once_with(
        mock_hass, mock_entry, cls.return_value
    )
    mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        mock_entry, [*_PRE_FEATURE_PLATFORMS, Platform.BUTTON]
    )


@pytest.mark.asyncio
async def test_only_the_boolean_true_enables_the_feature():
    """A truthy option value that is not True must not enable writes."""
    mock_hass = _hass_for_setup()
    mock_entry = _entry({"control_enabled": "yes"})

    with patch("custom_components.nwp500.NWP500DataUpdateCoordinator") as cls:
        cls.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(mock_hass, mock_entry) is True

    mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        mock_entry, _PRE_FEATURE_PLATFORMS
    )


@pytest.mark.asyncio
async def test_unload_undoes_the_platforms_that_were_forwarded():
    """Options change before the reload, so unload must not recompute."""
    mock_hass = MagicMock()
    mock_hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=True
    )
    mock_hass.services.async_remove = MagicMock()
    mock_entry = _entry({"control_enabled": False})
    mock_entry.runtime_data.async_shutdown = AsyncMock()
    feature = MagicMock()
    feature.async_stop = AsyncMock()
    forwarded = [*_PRE_FEATURE_PLATFORMS, Platform.BUTTON]
    mock_hass.data = {
        DOMAIN: {
            mock_entry.entry_id: {"platforms": forwarded, "control": feature}
        }
    }

    assert await async_unload_entry(mock_hass, mock_entry) is True

    mock_hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        mock_entry, forwarded
    )
    feature.async_stop.assert_awaited_once()
    assert mock_entry.entry_id not in mock_hass.data[DOMAIN]
    mock_entry.runtime_data.async_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_switching_the_feature_off_removes_it_before_the_reload():
    """Spec section 1.1.6: entities and stored data go with the toggle."""
    from custom_components.nwp500 import async_reload_entry

    mock_hass = MagicMock()
    mock_hass.config_entries.async_reload = AsyncMock()
    mock_entry = _entry({"control_enabled": False})
    feature = MagicMock()
    feature.async_remove = AsyncMock()
    mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"control": feature}}}

    await async_reload_entry(mock_hass, mock_entry)

    feature.async_remove.assert_awaited_once()
    mock_hass.config_entries.async_reload.assert_awaited_once_with(
        mock_entry.entry_id
    )


@pytest.mark.asyncio
async def test_an_options_change_with_the_feature_on_keeps_it():
    from custom_components.nwp500 import async_reload_entry

    mock_hass = MagicMock()
    mock_hass.config_entries.async_reload = AsyncMock()
    mock_entry = _entry({"control_enabled": True})
    feature = MagicMock()
    feature.async_remove = AsyncMock()
    mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"control": feature}}}

    await async_reload_entry(mock_hass, mock_entry)

    feature.async_remove.assert_not_awaited()
    mock_hass.config_entries.async_reload.assert_awaited_once()
