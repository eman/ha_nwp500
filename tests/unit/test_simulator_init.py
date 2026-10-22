"""The simulator's set-up hook, its off path, removal and its service."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500 import (
    NWP500ServiceHandler,
    async_reload_entry,
    async_remove_entry,
    async_setup_entry,
)
from custom_components.nwp500.const import DOMAIN
from custom_components.nwp500.simulator_deps import DepsResult

MAC = "AA:BB:CC:DD:EE:FF"

# For the tests that patch the simulator package, which imports pandas.
needs_sim = pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None,
    reason="the simulator's packages are not installed",
)
_PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.WATER_HEATER,
    Platform.SWITCH,
    Platform.NUMBER,
]

# Everything the simulator imports that an install without it may lack.
_HEAVY = (
    "custom_components.nwp500.simulator",
    "custom_components.nwp500.simulator.runner",
    "custom_components.nwp500.simulator.sensor",
    "custom_components.nwp500.simulator.service",
    "custom_components.nwp500.simulator.ha_client",
    "nwp500.sim",
    "nwp500.sim_adapters",
    "nwp500.sim_live",
    "nwp500.sim_home_assistant",
    "numpy",
    "pandas",
    "scipy",
)


def _hass_for_setup() -> MagicMock:
    mock_hass = MagicMock()
    mock_hass.data = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.services.has_service = MagicMock(return_value=False)
    mock_hass.services.async_register = MagicMock()

    async def import_job(target, *args):
        return target(*args)

    mock_hass.async_add_import_executor_job = import_job
    return mock_hass


def _entry(options: dict) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = options
    return entry


def _capture_background(entry: MagicMock) -> list:
    """The coroutines set-up starts in the background, not yet run."""
    started: list = []

    def create(hass, target, name):
        started.append(target)
        return MagicMock()

    entry.async_create_background_task = MagicMock(side_effect=create)
    return started


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"simulator_enabled": False}])
async def test_setup_with_the_simulator_off_imports_nothing_of_it(options):
    """With it off, nothing of the simulator or its packages is imported.

    Each of them raises on import here.
    """
    mock_hass = _hass_for_setup()
    mock_entry = _entry(options)
    started = _capture_background(mock_entry)

    with (
        patch.dict(sys.modules, dict.fromkeys(_HEAVY)),
        patch("custom_components.nwp500.NWP500DataUpdateCoordinator") as cls,
        patch("custom_components.nwp500.ir") as issues,
        patch(
            "custom_components.nwp500._async_remove_simulator_leftovers",
            AsyncMock(),
        ) as remove,
    ):
        cls.return_value.async_config_entry_first_refresh = AsyncMock()

        assert await async_setup_entry(mock_hass, mock_entry) is True

    mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        mock_entry, _PLATFORMS
    )
    assert mock_hass.data[DOMAIN][mock_entry.entry_id] == {
        "platforms": _PLATFORMS
    }
    assert started == []
    if options:
        # Switched off: what it left goes, even if no reload removed it.
        remove.assert_awaited_once_with(mock_hass, mock_entry.entry_id)
    else:
        # Never switched on: nothing of it is touched, not even its
        # Repairs issue.
        remove.assert_not_awaited()
        looked_up = issues.async_get.return_value.async_get_issue.call_args_list
        assert (
            call(DOMAIN, "simulator_start_failed_test_entry") not in looked_up
        )


@needs_sim
@pytest.mark.asyncio
async def test_the_simulator_starts_after_the_platforms_in_the_background():
    mock_hass = _hass_for_setup()
    mock_entry = _entry({"simulator_enabled": True})
    started = _capture_background(mock_entry)

    with (
        patch("custom_components.nwp500.NWP500DataUpdateCoordinator") as cls,
        patch(
            "custom_components.nwp500.simulator_deps.async_ensure",
            AsyncMock(return_value=DepsResult(ok=True)),
        ),
        patch(
            "custom_components.nwp500.simulator.async_setup_simulator",
            new=AsyncMock(),
        ) as setup,
        patch("custom_components.nwp500.ir") as issues,
        patch("custom_components.nwp500.async_dispatcher_send") as send,
    ):
        cls.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(mock_hass, mock_entry) is True

        # Set-up is done, and the heater's platforms are forwarded, before
        # the simulator has started.
        mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            mock_entry, _PLATFORMS
        )
        setup.assert_not_awaited()
        (start,) = started
        await start

    setup.assert_awaited_once_with(mock_hass, mock_entry, cls.return_value)
    issues.async_create_issue.assert_not_called()
    issues.async_delete_issue.assert_any_call(
        mock_hass, DOMAIN, f"simulator_start_failed_{mock_entry.entry_id}"
    )
    send.assert_called_once_with(
        mock_hass, f"nwp500_simulator_started_{mock_entry.entry_id}"
    )


@needs_sim
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deps", "error", "key", "placeholders"),
    [
        (
            DepsResult(ok=False, reason="missing", missing=("scipy>=1.11.0",)),
            None,
            "simulator_packages_missing",
            {"packages": "scipy"},
        ),
        (
            DepsResult(
                ok=False,
                reason="install_failed",
                missing=("pandas>=2.0.0", "scipy>=1.11.0"),
            ),
            None,
            "simulator_install_failed",
            {"packages": "pandas, scipy"},
        ),
        (
            DepsResult(ok=True),
            RuntimeError("boom"),
            "simulator_start_failed",
            {"reason": "RuntimeError"},
        ),
    ],
)
async def test_a_simulator_that_cannot_start_is_reported_in_repairs(
    deps, error, key, placeholders
):
    mock_hass = _hass_for_setup()
    mock_entry = _entry({"simulator_enabled": True})
    started = _capture_background(mock_entry)

    with (
        patch("custom_components.nwp500.NWP500DataUpdateCoordinator") as cls,
        patch(
            "custom_components.nwp500.simulator_deps.async_ensure",
            AsyncMock(return_value=deps),
        ),
        patch(
            "custom_components.nwp500.simulator.async_setup_simulator",
            new=AsyncMock(side_effect=error),
        ),
        patch("custom_components.nwp500.ir") as issues,
        patch("custom_components.nwp500.async_dispatcher_send") as send,
    ):
        cls.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(mock_hass, mock_entry) is True
        (start,) = started
        await start

    issues.async_create_issue.assert_called_once()
    args, kwargs = issues.async_create_issue.call_args
    assert args[1:] == (DOMAIN, f"simulator_start_failed_{mock_entry.entry_id}")
    assert kwargs["translation_key"] == key
    assert kwargs["translation_placeholders"] == placeholders
    send.assert_not_called()


@pytest.mark.asyncio
async def test_unload_stops_a_simulator_still_starting(hass: HomeAssistant):
    from custom_components.nwp500 import async_unload_entry

    never = asyncio.Event()

    async def starting() -> None:
        await never.wait()

    start = hass.async_create_task(starting())
    await asyncio.sleep(0)
    mock_entry = _entry({"simulator_enabled": True})
    mock_entry.runtime_data.async_shutdown = AsyncMock()
    hass.data[DOMAIN] = {
        mock_entry.entry_id: {"platforms": _PLATFORMS, "simulator_start": start}
    }

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, mock_entry) is True

    assert start.cancelled()


async def _leftovers(
    hass: HomeAssistant, hass_storage: dict, entry: MockConfigEntry
) -> tuple[er.RegistryEntry, er.RegistryEntry]:
    registry = er.async_get(hass)
    simulated = registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_simulator_heating", config_entry=entry
    )
    other = registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_tank_upper_temperature", config_entry=entry
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"simulator_start_failed_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="simulator_start_failed",
        translation_placeholders={"reason": "x"},
    )
    return simulated, other


@pytest.mark.asyncio
async def test_switching_it_off_removes_what_it_left(
    hass: HomeAssistant, hass_storage
):
    entry = MockConfigEntry(domain=DOMAIN, options={"simulator_enabled": False})
    entry.add_to_hass(hass)
    simulated, other = await _leftovers(hass, hass_storage, entry)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()):
        await async_reload_entry(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get(simulated.entity_id) is None
    assert registry.async_get(other.entity_id) is not None
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"simulator_start_failed_{entry.entry_id}"
    )


@pytest.mark.asyncio
async def test_a_reload_with_it_on_keeps_it(hass: HomeAssistant, hass_storage):
    entry = MockConfigEntry(domain=DOMAIN, options={"simulator_enabled": True})
    entry.add_to_hass(hass)
    simulated, _ = await _leftovers(hass, hass_storage, entry)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()):
        await async_reload_entry(hass, entry)

    assert er.async_get(hass).async_get(simulated.entity_id) is not None


@pytest.mark.asyncio
async def test_removing_the_entry_removes_its_repairs_issue(
    hass: HomeAssistant, hass_storage
):
    entry = MockConfigEntry(domain=DOMAIN, options={"simulator_enabled": True})
    entry.add_to_hass(hass)
    await _leftovers(hass, hass_storage, entry)

    await async_remove_entry(hass, entry)

    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"simulator_start_failed_{entry.entry_id}"
    )


@pytest.mark.asyncio
async def test_the_service_says_when_the_simulator_is_off(hass: HomeAssistant):
    handler = NWP500ServiceHandler(hass)
    coordinator = MagicMock()
    coordinator.entry = MockConfigEntry(domain=DOMAIN, entry_id="e1")
    call = MagicMock()

    with (
        patch.object(
            handler,
            "_get_coordinator_and_mac",
            AsyncMock(return_value=(coordinator, MAC)),
        ),
        patch.dict(sys.modules, dict.fromkeys(_HEAVY)),
        pytest.raises(ServiceValidationError) as err,
    ):
        await handler.async_simulate(call)

    assert err.value.translation_key == "simulator_not_enabled"


def test_the_service_schema_fills_its_defaults():
    from custom_components.nwp500 import SERVICE_SIMULATE_SCHEMA

    data = SERVICE_SIMULATE_SCHEMA({"entity_id": "water_heater.heater"})

    assert data["hours"] == 24
    assert data["writes"] == []
    assert data["include_points"] is False


@pytest.mark.parametrize(
    "data",
    [
        {"hours": 169},
        {"hours": 0},
        {"writes": [{"at": "2026-10-21T14:00:00"}]},
        {"writes": [{"at": "2026-10-21T14:00:00", "mode": "turbo"}]},
        {"writes": [{"temperature": 120}]},
    ],
)
def test_the_service_schema_refuses_what_cannot_be_simulated(data):
    import voluptuous as vol

    from custom_components.nwp500 import SERVICE_SIMULATE_SCHEMA

    with pytest.raises(vol.Invalid):
        SERVICE_SIMULATE_SCHEMA({"entity_id": "water_heater.heater", **data})


def test_the_service_schema_takes_a_write():
    from custom_components.nwp500 import SERVICE_SIMULATE_SCHEMA

    data = SERVICE_SIMULATE_SCHEMA(
        {
            "device_id": "abc",
            "writes": [{"at": "2026-10-21T14:00:00", "temperature": "120"}],
        }
    )

    (write,) = data["writes"]
    assert write["temperature"] == 120.0
    assert write["at"].hour == 14
