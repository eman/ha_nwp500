"""Offering the simulator at setup and in the options.

Through the real flow machinery.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500.const import DOMAIN
from custom_components.nwp500.simulator_deps import DepsResult

MAC = "AA:BB:CC:DD:EE:FF"
MAC2 = "11:22:33:44:55:66"
SIGN_IN = {CONF_EMAIL: "test@example.com", CONF_PASSWORD: "pw"}
FLOW = "custom_components.nwp500.config_flow"


@contextmanager
def signed_in(
    devices: list[tuple[str, str]] | None = None,
    missing: tuple[str, ...] = (),
    ensure: DepsResult | None = None,
) -> Iterator[AsyncMock]:
    """The account signs in; the simulator's packages are as given."""
    with (
        patch(
            f"{FLOW}.validate_input",
            AsyncMock(
                return_value={
                    "title": "Navien Heater",
                    "devices": devices or [(MAC, "Heater")],
                }
            ),
        ),
        patch(f"{FLOW}.async_missing", AsyncMock(return_value=missing)),
        patch(
            f"{FLOW}.async_ensure",
            AsyncMock(return_value=ensure or DepsResult(ok=True)),
        ) as install,
        patch("custom_components.nwp500.async_setup_entry", return_value=True),
    ):
        yield install


async def _to_menu(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], SIGN_IN
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "simulator_menu"
    assert result["menu_options"] == ["simulator_setup", "simulator_skip"]
    return result


@pytest.mark.asyncio
async def test_skipping_creates_the_entry_as_before(
    hass: HomeAssistant, enable_custom_integrations
):
    with signed_in() as install:
        result = await _to_menu(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_skip"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Navien Heater"
    assert result["data"] == SIGN_IN
    entry = result["result"]
    assert entry.options == {}
    assert entry.unique_id == "test@example.com"
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_setting_up_asks_about_each_heater(
    hass: HomeAssistant, enable_custom_integrations
):
    devices = [(MAC, "Garage"), (MAC2, "Basement")]
    with signed_in(devices=devices) as install:
        result = await _to_menu(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_setup"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "simulator_device"
        assert result["description_placeholders"]["device"] == "Garage"
        assert result["last_step"] is False

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"air_entity": "sensor.garage_air", "humidity_entity": "sensor.rh"},
        )
        assert result["description_placeholders"]["device"] == "Basement"
        assert result["last_step"] is True

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == SIGN_IN
    assert result["result"].options == {
        "simulator_enabled": True,
        "simulator": {
            MAC: {
                "air_entity": "sensor.garage_air",
                "humidity_entity": "sensor.rh",
            },
            MAC2: {},
        },
    }
    # The packages were there: nothing was installed.
    install.assert_not_awaited()


async def test_setting_up_takes_the_hot_water_inputs(
    hass: HomeAssistant, enable_custom_integrations
):
    """Each optional; the sizes and the valve in Home Assistant's units.

    Stored in gallons and °F.
    """
    from homeassistant.util.unit_system import METRIC_SYSTEM

    hass.config.units = METRIC_SYSTEM
    with signed_in(devices=[(MAC, "Water heater")]):
        result = await _to_menu(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_setup"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "inlet_entity": "sensor.supply",
                "demand_entity": "binary_sensor.demand",
                "circulation_speed_entity": "sensor.pump",
                "weather_entity": "weather.home",
                "draw_flow_entity": "sensor.draw_flow",
                "draw_flow_volume": "tank",
                "bath_gal": 113.6,
                "shower_gal": 45.4,
                "mixing_valve_f": 49.0,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options["simulator"][MAC] == {
        "inlet_entity": "sensor.supply",
        "demand_entity": "binary_sensor.demand",
        "circulation_speed_entity": "sensor.pump",
        "weather_entity": "weather.home",
        "draw_flow_entity": "sensor.draw_flow",
        "draw_flow_volume": "tank",
        "bath_gal": 30.01,
        "shower_gal": 11.99,
        "mixing_valve_f": 120.2,
    }


async def _through_progress(hass: HomeAssistant, result: dict) -> dict:
    """Past the install's progress step.

    An install that is done before the step shows (as a mock's is) moves
    on at once.
    """
    if result["type"] is not FlowResultType.SHOW_PROGRESS:
        return result
    assert result["progress_action"] == "simulator_install"
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    if result["type"] is FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )
    return result


@pytest.mark.asyncio
async def test_missing_packages_are_installed_behind_a_progress_step(
    hass: HomeAssistant, enable_custom_integrations
):
    with signed_in(missing=("scipy>=1.11.0",)) as install:
        result = await _to_menu(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_setup"}
        )
        result = await _through_progress(hass, result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "simulator_device"
    install.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_slow_install_shows_its_progress(
    hass: HomeAssistant, enable_custom_integrations
):
    done = asyncio.Event()

    async def install(hass: HomeAssistant) -> DepsResult:
        await done.wait()
        return DepsResult(ok=True)

    with signed_in(missing=("scipy>=1.11.0",)) as ensure:
        ensure.side_effect = install
        result = await _to_menu(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_setup"}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "simulator_install"
        assert result["progress_action"] == "simulator_install"

        done.set()
        result = await _through_progress(hass, result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "simulator_device"


@pytest.mark.asyncio
async def test_a_failed_install_can_be_skipped(
    hass: HomeAssistant, enable_custom_integrations
):
    failed = DepsResult(
        ok=False, reason="install_failed", missing=("scipy>=1.11.0",)
    )
    with signed_in(missing=("scipy>=1.11.0",), ensure=failed):
        result = await _to_menu(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_setup"}
        )
        result = await _through_progress(hass, result)
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "simulator_install_failed"
        assert result["description_placeholders"]["packages"] == "scipy"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_without"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {}


@pytest.mark.asyncio
async def test_a_failed_install_can_be_retried(
    hass: HomeAssistant, enable_custom_integrations
):
    failed = DepsResult(ok=False, reason="missing", missing=("scipy>=1.11.0",))
    with signed_in(missing=("scipy>=1.11.0",), ensure=failed) as install:
        result = await _to_menu(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_setup"}
        )
        result = await _through_progress(hass, result)
        assert result["step_id"] == "simulator_packages_missing"
        install.return_value = DepsResult(ok=True)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_retry"}
        )
        result = await _through_progress(hass, result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "simulator_device"
    assert install.await_count == 2


# The options flow.


def _loaded_entry(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test@example.com",
        data=SIGN_IN,
        options=options,
    )
    entry.add_to_hass(hass)
    device = MagicMock()
    device.device_info.device_name = "Garage"
    entry.runtime_data = MagicMock(data={MAC: {"device": device}})
    return entry


@contextmanager
def packages(
    missing: tuple[str, ...] = (), ensure: DepsResult | None = None
) -> Iterator[None]:
    with (
        patch(f"{FLOW}.async_missing", AsyncMock(return_value=missing)),
        patch(
            f"{FLOW}.async_ensure",
            AsyncMock(return_value=ensure or DepsResult(ok=True)),
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_options_never_switched_on_store_what_the_release_did(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {"scan_interval": 30})
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 45, "simulator_enabled": False},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 45}


@pytest.mark.asyncio
async def test_options_switch_the_simulator_on(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {})
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 30, "simulator_enabled": True},
        )
        assert result["step_id"] == "simulator_device"
        assert result["last_step"] is True
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"air_entity": "sensor.garage_air"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "scan_interval": 30,
        "simulator_enabled": True,
        "simulator": {MAC: {"air_entity": "sensor.garage_air"}},
    }


@pytest.mark.asyncio
async def test_options_suggest_the_sensors_chosen_and_let_them_be_cleared(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(
        hass,
        {
            "simulator_enabled": True,
            "simulator": {MAC: {"air_entity": "sensor.garage_air"}},
        },
    )
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 30, "simulator_enabled": True}
        )
        schema = result["data_schema"].schema
        suggested = {
            str(key): (key.description or {}).get("suggested_value")
            for key in schema
        }
        assert suggested["air_entity"] == "sensor.garage_air"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )

    assert entry.options["simulator"] == {MAC: {}}


@pytest.mark.asyncio
async def test_a_flow_of_delivered_water_is_the_default_and_stores_nothing(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(
        hass,
        {
            "simulator_enabled": True,
            "simulator": {MAC: {"draw_flow_volume": "tank"}},
        },
    )
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 30, "simulator_enabled": True}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "draw_flow_entity": "sensor.draw_flow",
                "draw_flow_volume": "delivered",
            },
        )

    assert entry.options["simulator"] == {
        MAC: {"draw_flow_entity": "sensor.draw_flow"}
    }


@pytest.mark.asyncio
async def test_options_show_the_sizes_in_home_assistants_unit(
    hass: HomeAssistant, enable_custom_integrations
):
    from homeassistant.util.unit_system import METRIC_SYSTEM

    hass.config.units = METRIC_SYSTEM
    entry = _loaded_entry(
        hass,
        {
            "simulator_enabled": True,
            "simulator": {
                MAC: {
                    "bath_gal": 30.0,
                    "shower_gal": 12.0,
                    "mixing_valve_f": 120.2,
                }
            },
        },
    )
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 30, "simulator_enabled": True}
        )
        suggested = {
            str(key): (key.description or {}).get("suggested_value")
            for key in result["data_schema"].schema
        }

    assert suggested["bath_gal"] == 113.6
    assert suggested["shower_gal"] == 45.4
    assert suggested["mixing_valve_f"] == 49.0


@pytest.mark.asyncio
async def test_options_switch_the_simulator_off(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(
        hass,
        {
            "simulator_enabled": True,
            "simulator": {MAC: {"air_entity": "sensor.garage_air"}},
        },
    )
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 30, "simulator_enabled": False}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["simulator_enabled"] is False
    # Kept, so switching it back on offers the same sensors.
    assert entry.options["simulator"] == {
        MAC: {"air_entity": "sensor.garage_air"}
    }


@pytest.mark.asyncio
async def test_options_go_on_to_external_control(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {})
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "scan_interval": 30,
                "control_enabled": True,
                "simulator_enabled": True,
            },
        )
        # The external control page comes after it.
        assert result["last_step"] is False
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"air_entity": "sensor.garage_air"}
        )
        assert result["step_id"] == "external_control"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "control_intent_entity": "sensor.intent",
                "control_mode": "shadow",
                "control_reservation_entry_limit": 16,
                "control_reservation_entry_reserve": 2,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["simulator_enabled"] is True
    assert entry.options["simulator"] == {
        MAC: {"air_entity": "sensor.garage_air"}
    }
    assert entry.options["control_enabled"] is True
    assert entry.options["control_intent_entity"] == "sensor.intent"


@pytest.mark.asyncio
async def test_options_leave_the_simulator_off_when_it_cannot_be_installed(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {"scan_interval": 30})
    failed = DepsResult(ok=False, reason="missing", missing=("scipy>=1.11.0",))
    with packages(missing=("scipy>=1.11.0",), ensure=failed):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 30, "simulator_enabled": True}
        )
        result = await _options_through_progress(hass, result)
        assert result["step_id"] == "simulator_packages_missing"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "simulator_without"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 30}


async def _options_through_progress(hass: HomeAssistant, result: dict) -> dict:
    if result["type"] is not FlowResultType.SHOW_PROGRESS:
        return result
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"]
    )
    if result["type"] is FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"]
        )
    return result


@pytest.mark.asyncio
async def test_options_cannot_switch_it_on_before_the_entry_loads(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {"scan_interval": 30})
    entry.runtime_data = MagicMock(data={})
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 30, "simulator_enabled": True}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"simulator_enabled": "simulator_not_loaded"}
    assert entry.options == {"scan_interval": 30}


@pytest.mark.asyncio
async def test_options_with_it_on_do_not_check_its_packages_again(
    hass: HomeAssistant, enable_custom_integrations
):
    """A missing package must not lead to the menu that switches it off."""
    entry = _loaded_entry(
        hass,
        {
            "simulator_enabled": True,
            "simulator": {MAC: {"air_entity": "sensor.garage_air"}},
        },
    )
    with packages(missing=("scipy>=1.11.0",)):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 45, "simulator_enabled": True}
        )
        assert result["step_id"] == "simulator_device"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"air_entity": "sensor.garage_air"}
        )

    assert entry.options["scan_interval"] == 45
    assert entry.options["simulator_enabled"] is True
