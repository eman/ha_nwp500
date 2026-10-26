"""Offering the simulator at setup and in the options.

Through the real flow machinery. At setup it is one of the optional
features folded beneath the sign-in; in the options, one of the menu's
pages.
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


def _suggested(result: dict) -> dict:
    """Each field's suggested value, the sections' fields among them."""
    from homeassistant.data_entry_flow import section

    found: dict = {}

    def walk(schema) -> None:
        for key, value in schema.schema.items():
            if isinstance(value, section):
                walk(value.schema)
                continue
            found[str(key)] = (key.description or {}).get("suggested_value")

    walk(result["data_schema"])
    return found


def _collapsed(result) -> dict[str, bool]:
    """Whether each of the form's sections is shown folded."""
    return {
        str(key): value.options["collapsed"]
        for key, value in result["data_schema"].schema.items()
        if hasattr(value, "options") and "collapsed" in value.options
    }


def _sections(**fields) -> dict:
    """A heater's answers, in the form's sections."""
    from custom_components.nwp500.config_flow import _nest

    return _nest(fields)


async def _signed_in(
    hass: HomeAssistant, *, simulator: bool = False, control: bool = False
) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "user"
    features = {"simulator_enabled": simulator, "control_enabled": control}
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SIGN_IN, "features": features}
    )


@pytest.mark.asyncio
async def test_signing_in_alone_creates_the_entry_as_before(
    hass: HomeAssistant, enable_custom_integrations
):
    with signed_in() as install:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        # The features folded away, and no page after the sign-in.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], SIGN_IN
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Navien Heater"
    assert result["data"] == SIGN_IN
    entry = result["result"]
    assert entry.options == {}
    assert entry.unique_id == "test@example.com"
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_features_are_folded_beneath_the_sign_in(
    hass: HomeAssistant, enable_custom_integrations
):
    from homeassistant.data_entry_flow import section

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    schema = result["data_schema"].schema
    (features,) = (v for v in schema.values() if isinstance(v, section))

    assert features.options["collapsed"] is True
    assert {str(k) for k in features.schema.schema} == {
        "simulator_enabled",
        "control_enabled",
    }


@pytest.mark.asyncio
async def test_both_features_left_off_store_nothing(
    hass: HomeAssistant, enable_custom_integrations
):
    with signed_in():
        result = await _signed_in(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == SIGN_IN
    assert result["result"].options == {}


@pytest.mark.asyncio
async def test_setting_up_asks_about_each_heater(
    hass: HomeAssistant, enable_custom_integrations
):
    devices = [(MAC, "Garage"), (MAC2, "Basement")]
    with signed_in(devices=devices) as install:
        result = await _signed_in(hass, simulator=True)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "simulator_device"
        assert result["description_placeholders"]["device"] == "Garage"
        assert result["last_step"] is False

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _sections(
                air_entity="sensor.garage_air", humidity_entity="sensor.rh"
            ),
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


async def test_the_hot_water_use_and_sizes_are_folded_away(
    hass: HomeAssistant, enable_custom_integrations
):
    from homeassistant.data_entry_flow import section

    with signed_in():
        result = await _signed_in(hass, simulator=True)
    sections = {
        str(k): v.options["collapsed"]
        for k, v in result["data_schema"].schema.items()
        if isinstance(v, section)
    }

    assert sections == {
        "sensors": False,
        "hot_water_use": True,
        "household": True,
    }


async def test_setting_up_takes_the_hot_water_inputs(
    hass: HomeAssistant, enable_custom_integrations
):
    """Each optional; the sizes and the valve in Home Assistant's units.

    Stored in gallons and °F.
    """
    from homeassistant.util.unit_system import METRIC_SYSTEM

    hass.config.units = METRIC_SYSTEM
    with signed_in(devices=[(MAC, "Water heater")]):
        result = await _signed_in(hass, simulator=True)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _sections(
                inlet_entity="sensor.supply",
                demand_entity="binary_sensor.demand",
                circulation_speed_entity="sensor.pump",
                weather_entity="weather.home",
                draw_flow_entity="sensor.draw_flow",
                draw_flow_volume="tank",
                bath_gal=113.6,
                shower_gal=45.4,
                mixing_valve_f=49.0,
            ),
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


async def test_a_supply_probe_needs_the_hot_water_in_use_sensor(
    hass: HomeAssistant, enable_custom_integrations
):
    with signed_in():
        result = await _signed_in(hass, simulator=True)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _sections(inlet_entity="sensor.supply")
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "inlet_needs_demand"}
        # What was chosen is still there, its section open to fix it.
        assert _suggested(result)["inlet_entity"] == "sensor.supply"
        assert _collapsed(result) == {
            "sensors": False,
            "hot_water_use": False,
            "household": True,
        }

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _sections(
                inlet_entity="sensor.supply",
                demand_entity="binary_sensor.demand",
            ),
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_only_weather_entity_is_suggested_the_first_time(
    hass: HomeAssistant, enable_custom_integrations
):
    hass.states.async_set("weather.home", "sunny")
    with signed_in():
        result = await _signed_in(hass, simulator=True)

    assert _suggested(result)["weather_entity"] == "weather.home"


async def test_two_weather_entities_suggest_neither(
    hass: HomeAssistant, enable_custom_integrations
):
    hass.states.async_set("weather.home", "sunny")
    hass.states.async_set("weather.airport", "sunny")
    with signed_in():
        result = await _signed_in(hass, simulator=True)

    assert _suggested(result)["weather_entity"] is None


async def test_with_the_simulator_and_external_control_both_pages_follow(
    hass: HomeAssistant, enable_custom_integrations
):
    hass.states.async_set(
        "sensor.plan",
        "plan-1",
        {"protocol": "1", "intent_id": "plan-1", "segments": []},
    )
    with signed_in():
        result = await _signed_in(hass, simulator=True, control=True)
        assert result["last_step"] is False
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _sections(air_entity="sensor.garage_air")
        )
        assert result["step_id"] == "external_control"
        # The only plan entity, chosen for the user.
        assert _suggested(result)["control_intent_entity"] == "sensor.plan"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"control_intent_entity": "sensor.plan", "control_mode": "shadow"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {
        "simulator_enabled": True,
        "simulator": {MAC: {"air_entity": "sensor.garage_air"}},
        "control_enabled": True,
        "control_intent_entity": "sensor.plan",
        "control_mode": "shadow",
        "control_reservation_entry_limit": 16,
        "control_reservation_entry_reserve": 2,
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
        result = await _signed_in(hass, simulator=True)
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
        result = await _signed_in(hass, simulator=True)
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "simulator_install"
        assert result["progress_action"] == "simulator_install"

        done.set()
        result = await _through_progress(hass, result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "simulator_device"


@pytest.mark.asyncio
async def test_a_failed_install_goes_on_without_and_offers_no_retry(
    hass: HomeAssistant, enable_custom_integrations
):
    failed = DepsResult(
        ok=False, reason="install_failed", missing=("scipy>=1.11.0",)
    )
    with signed_in(missing=("scipy>=1.11.0",), ensure=failed):
        result = await _signed_in(hass, simulator=True)
        result = await _through_progress(hass, result)
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "simulator_install_failed"
        assert result["description_placeholders"]["packages"] == "scipy"
        # Home Assistant tries again only after a restart.
        assert result["menu_options"] == ["simulator_without"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "simulator_without"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {}


@pytest.mark.asyncio
async def test_packages_not_installed_by_home_assistant_can_be_retried(
    hass: HomeAssistant, enable_custom_integrations
):
    failed = DepsResult(ok=False, reason="missing", missing=("scipy>=1.11.0",))
    with signed_in(missing=("scipy>=1.11.0",), ensure=failed) as install:
        result = await _signed_in(hass, simulator=True)
        result = await _through_progress(hass, result)
        assert result["step_id"] == "simulator_packages_missing"
        assert result["menu_options"] == [
            "simulator_retry",
            "simulator_without",
        ]
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


async def _simulator_page(
    hass: HomeAssistant, entry: MockConfigEntry, *, on: bool
) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "simulator"}
    )
    assert result["step_id"] == "simulator"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"simulator_enabled": on}
    )


@pytest.mark.asyncio
async def test_the_options_are_a_menu_saying_what_is_on(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {"simulator_enabled": True})

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "settings",
        "simulator",
        "external_control",
    ]
    assert result["description_placeholders"] == {
        "simulator": "on",
        "control": "off",
    }


@pytest.mark.asyncio
async def test_the_update_interval_saves_only_itself(
    hass: HomeAssistant, enable_custom_integrations
):
    options = {
        "scan_interval": 30,
        "simulator_enabled": True,
        "simulator": {MAC: {"air_entity": "sensor.garage_air"}},
    }
    entry = _loaded_entry(hass, options)
    with packages():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "settings"}
        )
        # No simulator pages to walk through.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 45}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {**options, "scan_interval": 45}


@pytest.mark.asyncio
async def test_options_never_switched_on_store_what_the_release_did(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {"scan_interval": 30})
    with packages():
        result = await _simulator_page(hass, entry, on=False)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 30}


@pytest.mark.asyncio
async def test_options_switch_the_simulator_on(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {"scan_interval": 30})
    with packages():
        result = await _simulator_page(hass, entry, on=True)
        assert result["step_id"] == "simulator_device"
        assert result["last_step"] is True
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _sections(air_entity="sensor.garage_air")
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
    # The only weather entity: suggested the first time, not once cleared.
    hass.states.async_set("weather.home", "sunny")
    entry = _loaded_entry(
        hass,
        {
            "simulator_enabled": True,
            "simulator": {MAC: {"air_entity": "sensor.garage_air"}},
        },
    )
    with packages():
        result = await _simulator_page(hass, entry, on=True)
        suggested = _suggested(result)
        assert suggested["air_entity"] == "sensor.garage_air"
        assert suggested["weather_entity"] is None

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )

    assert entry.options["simulator"] == {MAC: {}}


@pytest.mark.asyncio
async def test_the_heaters_own_sensors_can_be_kept(
    hass: HomeAssistant, enable_custom_integrations
):
    """Its DHW in use serves a household with no circulation loop."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entry = _loaded_entry(
        hass,
        {
            "simulator_enabled": True,
            "simulator": {MAC: {"demand_entity": "binary_sensor.heater_dhw"}},
        },
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, MAC)}
    )
    er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{MAC}_dhw_use",
        device_id=device.id,
        suggested_object_id="heater_dhw",
    )
    with packages():
        result = await _simulator_page(hass, entry, on=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _sections(demand_entity="binary_sensor.heater_dhw"),
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["simulator"] == {
        MAC: {"demand_entity": "binary_sensor.heater_dhw"}
    }


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
        result = await _simulator_page(hass, entry, on=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _sections(
                draw_flow_entity="sensor.draw_flow",
                draw_flow_volume="delivered",
            ),
        )

    assert entry.options["simulator"] == {
        MAC: {"draw_flow_entity": "sensor.draw_flow"}
    }


def test_the_flow_counts_field_shows_its_default():
    """Not a blank select: delivered water, the default, stored as nothing."""
    from custom_components.nwp500.config_flow import (
        SECTION_HOT_WATER_USE,
        _simulator_device_options,
        _simulator_device_schema,
    )

    section_ = next(
        value
        for key, value in _simulator_device_schema().schema.items()
        if str(key) == SECTION_HOT_WATER_USE
    )
    validated = section_({"draw_flow_entity": "sensor.draw_flow"})

    assert validated["draw_flow_volume"] == "delivered"
    assert _simulator_device_options({SECTION_HOT_WATER_USE: validated}) == {
        "draw_flow_entity": "sensor.draw_flow"
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
        result = await _simulator_page(hass, entry, on=True)
        suggested = _suggested(result)

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
        result = await _simulator_page(hass, entry, on=False)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["simulator_enabled"] is False
    # Kept, so switching it back on offers the same sensors.
    assert entry.options["simulator"] == {
        MAC: {"air_entity": "sensor.garage_air"}
    }


@pytest.mark.asyncio
async def test_options_leave_the_simulator_off_when_it_cannot_be_installed(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(hass, {"scan_interval": 30})
    failed = DepsResult(ok=False, reason="missing", missing=("scipy>=1.11.0",))
    with packages(missing=("scipy>=1.11.0",), ensure=failed):
        result = await _simulator_page(hass, entry, on=True)
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
        result = await _simulator_page(hass, entry, on=True)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "simulator"
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
        result = await _simulator_page(hass, entry, on=True)
        assert result["step_id"] == "simulator_device"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _sections(air_entity="sensor.garage_air")
        )

    assert entry.options["simulator_enabled"] is True


@pytest.mark.asyncio
async def test_sections_holding_stored_values_are_shown_open(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = _loaded_entry(
        hass,
        {"simulator_enabled": True, "simulator": {MAC: {"bath_gal": 30.0}}},
    )
    with packages():
        result = await _simulator_page(hass, entry, on=True)

    assert _collapsed(result) == {
        "sensors": False,
        "hot_water_use": True,
        "household": False,
    }


@pytest.mark.asyncio
async def test_a_failed_sign_in_keeps_a_feature_turned_on_in_view(
    hass: HomeAssistant, enable_custom_integrations
):
    from custom_components.nwp500.config_flow import InvalidAuth

    with patch(f"{FLOW}.validate_input", AsyncMock(side_effect=InvalidAuth)):
        result = await _signed_in(hass, simulator=True)

    assert result["errors"] == {"base": "invalid_auth"}
    assert _collapsed(result) == {"features": False}
    assert _suggested(result)["simulator_enabled"] is True


@pytest.mark.asyncio
async def test_a_setup_already_open_has_words():
    """HA's own abort, raised by the unique ID: said, not its key."""
    import json
    from pathlib import Path

    root = Path(__file__).parents[2] / "custom_components" / "nwp500"
    for name in ("strings.json", "translations/en.json"):
        abort = json.loads((root / name).read_text())["config"]["abort"]
        assert abort["already_in_progress"]


def _heater_in(
    hass: HomeAssistant, entry: MockConfigEntry, area: str, sensor: str
) -> None:
    """The entry's heater device in `area`, a temperature sensor beside it."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    area_id = ar.async_get(hass).async_get_or_create(area).id
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, MAC)}
    )
    devices.async_update_device(device.id, area_id=area_id)
    er.async_get(hass).async_get_or_create(
        "sensor", "test", sensor, suggested_object_id=sensor
    )
    er.async_get(hass).async_update_entity(f"sensor.{sensor}", area_id=area_id)
    hass.states.async_set(
        f"sensor.{sensor}", "64", {"device_class": "temperature"}
    )


def _other_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Another entry registering the same heater elsewhere.

    The integration allows one entry, so only a registry built by hand
    has two; the lookup is scoped to the entry all the same.
    """
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="other@example.com",
        data={CONF_EMAIL: "other@example.com", CONF_PASSWORD: "pw"},
    )
    other.add_to_hass(hass)
    _heater_in(hass, other, "Kitchen", "kitchen_air")
    return other


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped_lookup", [True, False])
async def test_suggestions_come_from_this_entrys_heater_only(
    hass: HomeAssistant, enable_custom_integrations, monkeypatch, scoped_lookup
):
    from homeassistant.helpers import device_registry as dr

    _other_entry(hass)
    entry = _loaded_entry(hass, {})
    _heater_in(hass, entry, "Garage", "garage_air")
    if not scoped_lookup:
        # Before Home Assistant 2026.8: one unscoped lookup.
        monkeypatch.delattr(dr.DeviceRegistry, "async_get_device_by_identifier")

    with packages():
        result = await _simulator_page(hass, entry, on=True)

    air = _suggested(result)["air_entity"]
    assert air != "sensor.kitchen_air"
    if scoped_lookup:
        assert air == "sensor.garage_air"
