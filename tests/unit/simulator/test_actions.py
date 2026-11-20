"""The simulator's actions: the library's calls on a heater's scenario.

Each action's answer must be the library's own for the scenario the
action walked, so most tests take that scenario from `get_scenario`, send
it back alone (`use_heater` off, so the clock cannot move between the two)
and compare with the library's call on it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from nwp500 import sim
from nwp500.sim import codec
from nwp500.sim.profile import heater_profile
from nwp500.sim_live import DEFAULT_PARAMS_NOTE
from pytest_homeassistant.common import MockConfigEntry

from custom_components.nwp500.const import (
    CONF_SIMULATOR,
    CONF_SIMULATOR_ENABLED,
    DOMAIN,
)
from custom_components.nwp500.control.capabilities import (
    NEAR_TERM_LEAD,
    WAKE_AFTER,
)
from custom_components.nwp500.control.entries import near_term_minute
from custom_components.nwp500.coordinator import NWP500Data
from custom_components.nwp500.simulator import (
    async_setup_simulator,
    async_unload_simulator,
    service,
)
from custom_components.nwp500.simulator.runner import SimulatorRunner

from .conftest import MAC, make_status


@pytest.fixture
async def runner(
    hass: HomeAssistant, hass_storage, coordinator
) -> AsyncIterator[SimulatorRunner]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: {MAC: {}}},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = NWP500Data(coordinator)
    await async_setup_simulator(hass, entry, coordinator)
    await hass.async_block_till_done()
    yield entry.runtime_data.simulator.runners[MAC]
    await async_unload_simulator(hass, entry)


async def snapshot(hass: HomeAssistant, runner: SimulatorRunner) -> Any:
    """The heater's scenario now, as a document."""
    return (await service.async_get_scenario(hass, runner))["result"]


def walked(document: dict[str, Any]) -> tuple[Any, Any]:
    """The scenario and parameters a document holds."""
    scenario, params = codec.scenario_from_document(document)
    assert params is not None
    return scenario, params


async def test_the_scenario_is_the_heaters(
    hass: HomeAssistant, runner: SimulatorRunner
):
    response = await service.async_get_scenario(hass, runner)

    inputs = await runner.async_inputs()
    assert not isinstance(inputs, str)
    document = response["result"]
    assert document["schema_version"] == codec.SCHEMA_VERSION
    scenario, params = walked(document)
    assert scenario.state == inputs.scenario.state
    assert scenario.settings == inputs.scenario.settings
    assert params == inputs.params
    assert response["inputs"]["reasons"] == ["reservations_unknown"]


async def test_a_document_is_laid_over_the_heaters_scenario(
    hass: HomeAssistant, runner: SimulatorRunner
):
    heater = await snapshot(hass, runner)
    over = {
        "schema_version": 1,
        "scenario": {"settings": {"setpoint_f": 125.0}, "horizon": "PT6H"},
    }

    document = (await service.async_get_scenario(hass, runner, scenario=over))[
        "result"
    ]

    assert document["scenario"]["settings"]["setpoint_f"] == 125.0
    assert document["scenario"]["horizon"] == "PT6H"
    # The rest is the heater's.
    assert document["scenario"]["state"] == heater["scenario"]["state"]
    assert document["params"] == heater["params"]


async def test_a_document_alone_needs_no_heater(
    hass: HomeAssistant, runner: SimulatorRunner, coordinator
):
    document = await snapshot(hass, runner)
    # Nothing to read from the heater.
    coordinator.data[MAC]["status"] = None

    response = await service.async_get_scenario(
        hass, runner, scenario=document, use_heater=False
    )

    assert response["result"] == document
    assert response["inputs"] == {"reasons": [], "notes": []}


async def test_another_integrations_tank_is_walked_with_no_heater(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    # Another tank: its own setpoint, no heater named.
    document["scenario"]["settings"]["setpoint_f"] = 125.0

    response = await service.async_simulate(
        hass, None, scenario=document, use_heater=False, include_points=True
    )

    scenario, params = walked(document)
    expected = codec.result_to_document(sim.simulate(scenario, params))
    assert response["result"] == expected
    assert response["inputs"] == {"reasons": [], "notes": []}


async def test_with_no_heater_the_heaters_scenario_is_not_there(
    hass: HomeAssistant,
):
    with pytest.raises(ServiceValidationError) as err:
        await service.async_get_scenario(hass, None)
    assert err.value.translation_key == "simulator_not_enabled"


async def test_a_document_without_params_takes_the_librarys_defaults(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    del document["params"]

    response = await service.async_get_scenario(
        hass, runner, scenario=document, use_heater=False
    )

    assert walked(response["result"])[1] == sim.default_params()
    assert DEFAULT_PARAMS_NOTE in response["inputs"]["notes"]


@pytest.mark.parametrize(
    "document",
    [
        None,
        {"schema_version": 2, "scenario": {}},
        {"schema_version": 1, "scenario": {"horizon": "soon"}},
        {"schema_version": 1, "scenario": {}, "params": []},
    ],
)
async def test_a_scenario_the_library_refuses_is_the_callers_mistake(
    hass: HomeAssistant, runner: SimulatorRunner, document
):
    with pytest.raises(ServiceValidationError) as err:
        await service.async_get_scenario(
            hass, runner, scenario=document, use_heater=document is not None
        )

    assert err.value.translation_key == "simulator_invalid_scenario"


async def test_malformed_params_are_the_callers_mistake(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    document["params"]["unfitted"] = None

    with pytest.raises(ServiceValidationError) as err:
        await service.async_get_scenario(
            hass, runner, scenario=document, use_heater=False
        )

    assert err.value.translation_key == "simulator_invalid_scenario"


async def test_a_null_parameter_is_the_callers_mistake(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    document["params"]["compressor"]["heat_btu_h"] = None

    with pytest.raises(ServiceValidationError) as err:
        await service.async_simulate(
            hass, runner, scenario=document, use_heater=False
        )
    assert err.value.translation_key == "simulator_invalid_scenario"
    assert "heat_btu_h" in err.value.translation_placeholders["error"]


async def test_a_walk_longer_than_a_week_is_refused(
    hass: HomeAssistant, runner: SimulatorRunner
):
    with pytest.raises(ServiceValidationError) as err:
        await service.async_simulate(
            hass,
            runner,
            scenario={"schema_version": 1, "scenario": {"horizon": "P8D"}},
        )

    assert err.value.translation_key == "simulator_horizon_too_long"


async def test_the_hours_are_laid_over_the_documents(
    hass: HomeAssistant, runner: SimulatorRunner
):
    response = await service.async_simulate(
        hass,
        runner,
        scenario={"schema_version": 1, "scenario": {"horizon": "PT6H"}},
        hours=2,
    )

    start = datetime.fromisoformat(response["result"]["start"])
    until = datetime.fromisoformat(response["result"]["until"])
    assert until - start == timedelta(hours=2)


async def test_the_walk_is_the_librarys(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False, include_points=True
    )

    assert response["result"] == codec.result_to_document(
        sim.simulate(*walked(document))
    )


async def test_time_to_ready_is_the_librarys(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)

    response = await service.async_time_to_ready(
        hass,
        runner,
        scenario=document,
        use_heater=False,
        bath_gal=30.0,
        shower_gal=12.0,
    )

    expected = sim.time_to_ready(
        *walked(document), bath_gal=30.0, shower_gal=12.0
    )
    assert response["result"] == codec.to_document(expected)
    assert response["result"]["kind"] == "readiness"


async def test_the_next_recovery_is_the_librarys(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)

    response = await service.async_next_recovery(
        hass, runner, scenario=document, use_heater=False
    )

    expected = sim.next_recovery(*walked(document))
    assert response["result"] == codec.to_document(expected)
    assert response["result"]["kind"] == "recovery"


async def test_the_forecast_is_the_librarys(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    scenario, params = walked(document)
    target = scenario.start + timedelta(hours=8)

    response = await service.async_forecast(
        hass,
        runner,
        scenario=document,
        use_heater=False,
        target_time=target,
        target_f=130.0,
    )

    expected = sim.forecast(
        scenario.state,
        scenario.settings,
        scenario.environment,
        params,
        at=scenario.start,
        target_time=target,
        target_f=130.0,
        loads=scenario.loads,
        history=scenario.history,
    )
    assert response["result"] == codec.to_document(expected)
    assert response["result"]["kind"] == "forecast"


async def test_a_forecast_the_library_refuses_says_why(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    scenario, _ = walked(document)

    with pytest.raises(HomeAssistantError, match="168 h") as err:
        await service.async_forecast(
            hass,
            runner,
            scenario=document,
            use_heater=False,
            target_time=scenario.start + timedelta(days=8),
        )
    assert not isinstance(err.value, ServiceValidationError)


async def test_the_profile_is_the_tanks_the_scenario_gives(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    document["params"]["tank"]["volume_gal"] = 40.0

    response = await service.async_heater_profile(
        hass, runner, scenario=document, use_heater=False
    )

    params = walked(document)[1]
    profile = heater_profile(params.tank, params.readiness.line_f)
    assert response["result"] == {
        **codec.to_document(profile),
        "facts": profile.facts(),
        "revision": profile.revision,
    }
    assert response["result"]["tank_gal"] == 40.0
    assert response["result"]["facts"]["tank_gal"] == 40.0


async def test_the_profiles_revision_moves_with_its_facts(
    hass: HomeAssistant, runner: SimulatorRunner
):
    """A consumer re-reads the profile when its revision moves."""
    document = await snapshot(hass, runner)
    first = await service.async_heater_profile(
        hass, runner, scenario=document, use_heater=False
    )
    again = await service.async_heater_profile(
        hass, runner, scenario=document, use_heater=False
    )
    document["params"]["tank"]["volume_gal"] = 40.0
    other = await service.async_heater_profile(
        hass, runner, scenario=document, use_heater=False
    )
    assert first["result"]["revision"] == again["result"]["revision"]
    assert first["result"]["revision"] != other["result"]["revision"]


async def test_tou_periods_not_read_do_not_hold_an_answer_up(
    hass: HomeAssistant, runner: SimulatorRunner, coordinator
):
    coordinator.data[MAC]["status"] = make_status(touStatus=True)

    response = await service.async_simulate(hass, runner, hours=2)

    assert "tou_schedule_unknown" in response["result"]["reasons"]


async def test_without_the_feature_the_librarys_defaults_answer(
    hass: HomeAssistant, runner: SimulatorRunner, coordinator
):
    coordinator.device_features = {}

    response = await service.async_heater_profile(hass, runner)

    assert (
        response["result"]["tank_gal"] == sim.default_params().tank.volume_gal
    )
    assert response["inputs"]["notes"].count(DEFAULT_PARAMS_NOTE) == 1


async def test_a_documents_params_leave_no_note_of_the_defaults(
    hass: HomeAssistant, runner: SimulatorRunner, coordinator
):
    document = await snapshot(hass, runner)
    coordinator.device_features = {}

    response = await service.async_heater_profile(
        hass,
        runner,
        scenario={
            "schema_version": 1,
            "scenario": {},
            "params": document["params"],
        },
    )

    assert DEFAULT_PARAMS_NOTE not in response["inputs"]["notes"]


async def planned_write(hass: HomeAssistant, runner: SimulatorRunner) -> Any:
    """The heater's scenario, with a setpoint write half an hour in."""
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])
    at = (start + timedelta(minutes=30)).isoformat()
    document["scenario"]["commands"] = {
        "writes": [{"at": at, "setpoint_f": 125.0}]
    }
    return document


async def test_omitted_writes_keep_the_documents(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await planned_write(hass, runner)

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False
    )

    # As it lands: on the heater's setpoint step.
    setpoints = [w["setpoint_f"] for w in response["result"]["writes"]]
    assert any(abs(value - 125.0) < 0.5 for value in setpoints)


async def test_empty_writes_clear_the_documents(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await planned_write(hass, runner)

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False, writes=[]
    )

    setpoints = [w["setpoint_f"] for w in response["result"]["writes"]]
    assert not any(abs(value - 125.0) < 0.5 for value in setpoints)


def _plan(start: datetime, *, setpoint_f: float = 125.0) -> dict[str, Any]:
    """A plan: heat pump at 130 °F now, a setpoint half an hour in."""
    return {
        "protocol": "1",
        "intent_id": "i-sim",
        "issued_at": start.isoformat(),
        "segments": [
            {
                "id": "now",
                "start": start.isoformat(),
                "mode": "heat_pump",
                "setpoint_f": 130,
            },
            {
                "id": "later",
                "start": (start + timedelta(minutes=30)).isoformat(),
                "setpoint_f": setpoint_f,
            },
        ],
        "commands": [{"id": "t", "command": "tou", "enabled": False}],
    }


async def test_a_plan_is_walked_as_its_segments_writes(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False, plan=_plan(start)
    )

    setpoints = [w["setpoint_f"] for w in response["result"]["writes"]]
    assert any(abs(value - 125.0) < 0.5 for value in setpoints)
    # In force at the start: applied once its near-term entry fires.
    assert any(abs(value - 130.0) < 0.5 for value in setpoints)


async def test_a_plans_commands_are_all_walked(
    hass: HomeAssistant, runner: SimulatorRunner
):
    """Each kind, the last of it as adoption leaves it; TOU its own field."""
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])
    plan = _plan(start)
    plan["commands"] += [
        {"id": "v1", "command": "vacation", "days": 2},
        {"id": "v2", "command": "vacation", "days": 3},
        {"id": "d", "command": "demand_response", "enabled": True},
    ]

    inputs = await service.async_inputs(
        hass, runner, scenario=document, use_heater=False
    )
    walked = service._with_plan(inputs, service._parse_plan(plan, runner))

    commands = walked.scenario.commands
    assert commands.tou_enabled is False
    assert commands.vacation is not None and commands.vacation.days == 3
    assert commands.demand_response is not None
    assert commands.demand_response.enabled is True
    assert commands.power is None


async def test_what_the_library_does_not_model_is_in_the_reasons(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])
    plan = _plan(start)
    plan["commands"].append(
        {"id": "d", "command": "demand_response", "enabled": True}
    )

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False, plan=plan
    )

    assert "demand_response_not_modelled" in response["result"]["reasons"]


async def test_segments_past_the_walk_are_left_out(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])
    plan = _plan(start)
    plan["segments"][1]["start"] = (start + timedelta(days=30)).isoformat()

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False, hours=6, plan=plan
    )

    setpoints = [w["setpoint_f"] for w in response["result"]["writes"]]
    assert not any(abs(value - 125.0) < 0.5 for value in setpoints)


async def test_a_plan_the_adapter_would_reject_is_refused_with_its_reason(
    hass: HomeAssistant, runner: SimulatorRunner
):
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])
    plan = _plan(start)
    plan["segments"][0]["mode"] = "vacation"

    with pytest.raises(ServiceValidationError) as err:
        await service.async_simulate(
            hass, runner, scenario=document, use_heater=False, plan=plan
        )
    assert err.value.translation_key == "simulator_plan_rejected"
    assert err.value.translation_placeholders["reason"] == "mode_not_allowed"


async def test_the_segment_in_force_applies_when_its_near_term_entry_fires(
    hass: HomeAssistant, runner: SimulatorRunner
):
    """Adopted at the walk's start: the entry fires once the lead allows."""
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False, plan=_plan(start)
    )

    (now,) = [
        w for w in response["result"]["writes"] if w.get("serves") == "now"
    ]
    assert datetime.fromisoformat(str(now["at"])) == near_term_minute(
        start, NEAR_TERM_LEAD
    )


async def test_a_segment_within_the_lead_applies_the_lead_after_it_begins(
    hass: HomeAssistant, runner: SimulatorRunner
):
    """As the engine asserts it, and the one in force is dropped for it."""
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])
    plan = _plan(start)
    plan["segments"][1]["start"] = (start + timedelta(minutes=1)).isoformat()

    response = await service.async_simulate(
        hass, runner, scenario=document, use_heater=False, plan=plan
    )

    writes = {w["serves"]: w["at"] for w in response["result"]["writes"]}
    # Its near-term entry fires at or after the in-force one's minute.
    assert "now" not in writes
    # From when the feature wakes for its start, as parsed: truncated to
    # the minute. A minute past the lead, since it wakes just after.
    begins = (start + timedelta(minutes=1)).replace(second=0, microsecond=0)
    at = datetime.fromisoformat(str(writes["later"]))
    assert at == near_term_minute(begins + WAKE_AFTER, NEAR_TERM_LEAD)
    assert at == begins + timedelta(minutes=3)


async def test_a_plan_outside_the_heaters_range_is_refused(
    hass: HomeAssistant, runner: SimulatorRunner
):
    """Checked against the named heater's range, as on publishing."""
    document = await snapshot(hass, runner)
    start = datetime.fromisoformat(document["scenario"]["start"])
    feature = runner.coordinator.device_features[MAC]
    feature.dhw_temperature_max_raw = 100  # 50 °C, 122 °F

    with pytest.raises(ServiceValidationError) as err:
        await service.async_simulate(
            hass, runner, scenario=document, use_heater=False, plan=_plan(start)
        )
    assert err.value.translation_placeholders["reason"] == (
        "setpoint_out_of_range"
    )


def test_a_plan_and_writes_together_are_refused():
    import voluptuous as vol

    from custom_components.nwp500.services import (
        SERVICE_SIMULATE_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SERVICE_SIMULATE_SCHEMA(
            {
                "entity_id": "water_heater.heater",
                "plan": {},
                "writes": [
                    {"at": "2026-11-19T12:00:00+00:00", "mode": "heat_pump"}
                ],
            }
        )
