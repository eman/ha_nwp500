"""The simulator's calibration: the library's, run on the recorder's history.

The library's readers and its fit are patched: these check what the
integration hands them, what it keeps, and that the runner walks on it.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from nwp500 import sim_adapters as sa
from nwp500 import sim_calibrate
from nwp500.sim.params import (
    UNFITTED_TERMS,
    params_from_document,
    params_to_document,
)
from nwp500.sim_home_assistant import HeaterHistory, RecordInputs
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nwp500 import NWP500ServiceHandler
from custom_components.nwp500.const import (
    CONF_SIMULATOR,
    CONF_SIMULATOR_ENABLED,
    DATA_SIMULATOR,
    DOMAIN,
    SIMULATOR_HEATER_ENTITIES,
)
from custom_components.nwp500.simulator import (
    SimulatorFeature,
    async_setup_simulator,
    async_unload_simulator,
)
from custom_components.nwp500.simulator.button import (
    SimulatorCalibrateButton,
    create_simulator_buttons,
)
from custom_components.nwp500.simulator.calibration import (
    CALIBRATE_EVERY,
    FIRST_AFTER,
    RETRY_AFTER,
    CalibrationError,
)
from custom_components.nwp500.simulator.sensor import (
    SimulatorCalibrationSensor,
)

from .conftest import MAC, make_feature

KEY = "nwp500.simulator_calibration.entry1"
OPTIONS = {
    "air_entity": "sensor.air",
    "humidity_entity": "sensor.rh",
    "demand_entity": "binary_sensor.demand",
    "circulation_speed_entity": "sensor.pump",
    "inlet_entity": "sensor.supply",
}


def _register_heater(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    for domain, key in SIMULATOR_HEATER_ENTITIES:
        registry.async_get_or_create(domain, DOMAIN, f"{MAC}_{key}")


async def _started(
    hass: HomeAssistant, coordinator: MagicMock
) -> SimulatorFeature:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: {MAC: OPTIONS}},
    )
    entry.add_to_hass(hass)
    # The recorder is the history's; `_Library` stands in for its reads.
    hass.config.components.add("recorder")
    await async_setup_simulator(hass, entry, coordinator)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id][DATA_SIMULATOR]


async def _stop(hass: HomeAssistant) -> None:
    entry = hass.config_entries.async_get_entry("entry1")
    assert entry is not None
    await async_unload_simulator(hass, entry)
    await hass.async_block_till_done()


def _fitted() -> Any:
    """Parameters as a calibration returns them: the defaults, marked."""
    return dataclasses.replace(
        sa.params(make_feature()), unfitted=(UNFITTED_TERMS[0],)
    )


def _done(params: Any) -> MagicMock:
    done = MagicMock()
    done.calibration.params = params
    done.notes = ("a note",)
    done.judged = ()
    return done


class _Library:
    """The library's readers and fit, patched; what each was given."""

    def __init__(self, params: Any = None, error: Exception | None = None):
        self.history = HeaterHistory(
            rows=MagicMock(), observed=[dt_util.utcnow()], notes=("read",)
        )
        self.record = RecordInputs(None, None, None, None, ())
        self.read = AsyncMock(return_value=self.history)
        self.inputs = AsyncMock(return_value=self.record)
        self.fit = MagicMock(
            side_effect=error, return_value=_done(params or _fitted())
        )
        self.report = MagicMock(
            return_value=[
                sim_calibrate.Term("UA", "1.2", "40", "0.1", True),
                sim_calibrate.Term("COP scale", "1", "0", "", False, "few"),
            ]
        )
        base = "custom_components.nwp500.simulator.calibration"
        self._patches = [
            patch(f"{base}.read_heater_history", self.read),
            patch(f"{base}.record_inputs", self.inputs),
            patch(f"{base}.sim_calibrate.calibrate_changes", self.fit),
            patch(f"{base}.sim_calibrate.report", self.report),
            patch(
                f"{base}.get_instance",
                MagicMock(return_value=MagicMock(keep_days=10)),
            ),
        ]

    def __enter__(self) -> _Library:
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in reversed(self._patches):
            p.stop()


async def test_a_calibration_is_the_librarys_and_the_runner_walks_on_it(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    runner = feature.runners[MAC]
    assert runner.calibrated_params is None

    with _Library() as library:
        record = await feature.async_calibrate(MAC)
        await hass.async_block_till_done()

    # The recorder's whole retention, the heater's entities and the
    # simulator's inputs; the inlet and the weather aren't the fit's.
    _, heater, start, end = library.read.await_args.args
    assert heater == runner.heater_entities()
    assert end - start == timedelta(days=10)
    entities = library.inputs.await_args.args[1]
    assert (entities.air, entities.humidity) == ("sensor.air", "sensor.rh")
    assert entities.demand == "binary_sensor.demand"
    assert entities.circulation_speed == "sensor.pump"
    assert entities.inlet is None
    # From the defaults for the tank's size, the first time.
    args, kwargs = library.fit.call_args
    assert args == (library.history.rows, library.history.observed)
    assert kwargs["params"] == sa.params(make_feature())
    assert kwargs["keep_better"] is False
    assert kwargs["record"] is library.record
    assert kwargs["notes"] == ("read",)

    fitted = _fitted()
    assert params_from_document(record["params"]) == fitted
    assert record["error"] is None
    assert record["terms"][0]["name"] == "UA"
    assert record["notes"] == ["a note"]
    assert runner.calibrated_params == fitted
    assert hass_storage[KEY]["data"]["heaters"][MAC]["params"] == (
        params_to_document(fitted)
    )
    # Every walk, and every action, on the calibrated parameters.
    inputs = await runner.async_inputs()
    assert not isinstance(inputs, str)
    assert inputs.params == fitted
    assert inputs.live.params == fitted
    await _stop(hass)


async def test_a_later_calibration_refines_the_kept_one(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)

    with _Library() as library:
        await feature.async_calibrate(MAC)
        await feature.async_calibrate(MAC)
        kwargs = library.fit.call_args.kwargs
        assert kwargs["params"] == _fitted()
        assert kwargs["keep_better"] is True
        assert feature.calibration.record(MAC)["refined"] is True

        await feature.async_calibrate(MAC, from_defaults=True)
        kwargs = library.fit.call_args.kwargs
        assert kwargs["params"] == sa.params(make_feature())
        assert kwargs["keep_better"] is False
    await _stop(hass)


async def test_a_failed_calibration_keeps_the_last_one(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    runner = feature.runners[MAC]
    with _Library():
        await feature.async_calibrate(MAC)
    kept = runner.calibrated_params

    refused = sim_calibrate.CalibrationInputError("no quiet windows")
    with _Library(error=refused), pytest.raises(CalibrationError) as caught:
        await feature.async_calibrate(MAC)
    await hass.async_block_till_done()

    assert str(caught.value) == "no quiet windows"
    assert runner.calibrated_params == kept
    record = feature.calibration.record(MAC)
    assert record["error"] == "no quiet windows"
    assert record["attempted_at"] > record["calibrated_at"]
    assert hass_storage[KEY]["data"]["heaters"][MAC]["error"] == (
        "no quiet windows"
    )
    await _stop(hass)


async def test_what_a_calibration_cant_do_without(
    hass: HomeAssistant, hass_storage, coordinator
):
    feature = await _started(hass, coordinator)

    with _Library(), pytest.raises(CalibrationError, match="Last status"):
        await feature.async_calibrate(MAC)

    _register_heater(hass)
    coordinator.device_features = {}
    with _Library(), pytest.raises(CalibrationError, match="tank size"):
        await feature.async_calibrate(MAC)
    await _stop(hass)


async def test_the_kept_calibration_is_used_after_a_restart(
    hass: HomeAssistant, hass_storage, coordinator
):
    hass_storage[KEY] = {
        "version": 1,
        "key": KEY,
        "data": {
            "heaters": {
                MAC: {
                    "params": params_to_document(_fitted()),
                    "calibrated_at": dt_util.utcnow().isoformat(),
                    "attempted_at": dt_util.utcnow().isoformat(),
                    "error": None,
                }
            }
        },
    }
    feature = await _started(hass, coordinator)

    assert feature.runners[MAC].calibrated_params == _fitted()
    await _stop(hass)


async def test_unreadable_parameters_are_dropped(
    hass: HomeAssistant, hass_storage, coordinator
):
    hass_storage[KEY] = {
        "version": 1,
        "key": KEY,
        "data": {"heaters": {MAC: {"params": {"tank": "?"}}}},
    }
    feature = await _started(hass, coordinator)

    assert feature.runners[MAC].calibrated_params is None
    assert feature.calibration.record(MAC)["params"] is None
    await _stop(hass)


async def test_a_dropped_calibration_is_made_again_soon(
    hass: HomeAssistant, hass_storage, coordinator
):
    """#274 review: not a week after the attempt that made it."""
    attempted = (dt_util.utcnow() - timedelta(days=1)).isoformat()
    hass_storage[KEY] = {
        "version": 1,
        "key": KEY,
        "data": {
            "heaters": {
                MAC: {
                    "params": {"schema": "retired"},
                    "calibrated_at": attempted,
                    "attempted_at": attempted,
                    "error": None,
                }
            }
        },
    }
    feature = await _started(hass, coordinator)
    started = dt_util.utcnow()

    assert feature.calibration.due(MAC, started + FIRST_AFTER, started)
    await _stop(hass)


async def test_what_the_library_and_the_recorder_say_is_passed_on(
    hass: HomeAssistant, hass_storage, coordinator
):
    """#274 review: the library's reason, not its type's name."""
    from nwp500.sim_home_assistant import HomeAssistantError as LibraryError

    _register_heater(hass)
    feature = await _started(hass, coordinator)

    with _Library() as library:
        library.read.side_effect = LibraryError("sensor.x: does not exist")
        with pytest.raises(CalibrationError) as caught:
            await feature.async_calibrate(MAC)
    assert str(caught.value) == "sensor.x: does not exist"

    hass.config.components.remove("recorder")
    with _Library(), pytest.raises(CalibrationError, match="recorder"):
        await feature.async_calibrate(MAC)
    await _stop(hass)


async def test_a_refinement_needs_no_tank_size(
    hass: HomeAssistant, hass_storage, coordinator
):
    """#274 review: only the defaults need the device information."""
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    with _Library():
        await feature.async_calibrate(MAC)

    coordinator.device_features = {}
    with _Library() as library:
        await feature.async_calibrate(MAC)
        assert library.fit.call_args.kwargs["keep_better"] is True
    await _stop(hass)


async def test_another_tanks_calibration_is_neither_used_nor_refined(
    hass: HomeAssistant, hass_storage, coordinator
):
    """#274 review: a calibration is for the tank it was made on."""
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    runner = feature.runners[MAC]
    with _Library():
        await feature.async_calibrate(MAC)

    bigger = make_feature()
    bigger.volume_code = 3
    coordinator.device_features = {MAC: bigger}
    inputs = await runner.async_inputs()
    assert not isinstance(inputs, str)
    assert inputs.params.tank.volume_gal == (sa.params(bigger).tank.volume_gal)
    # Nor shown as in use (#274 review).
    sensor = SimulatorCalibrationSensor(feature, runner)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["fitted"] == {}
    assert sensor.extra_state_attributes["on_defaults"] == []
    with _Library(params=sa.params(bigger)) as library:
        await feature.async_calibrate(MAC)
        kwargs = library.fit.call_args.kwargs
        assert kwargs["params"] == sa.params(bigger)
        assert kwargs["keep_better"] is False
    await _stop(hass)


async def test_a_cancelled_calibration_holds_the_fit_until_its_thread_ends(
    hass: HomeAssistant, hass_storage, coordinator
):
    """#274 review: the executor's thread fits on after a cancel."""
    import asyncio

    from custom_components.nwp500.simulator import calibration

    _register_heater(hass)
    feature = await _started(hass, coordinator)
    fitting: asyncio.Future[Any] = hass.loop.create_future()
    with (
        _Library() as library,
        patch.object(
            hass, "async_add_executor_job", MagicMock(return_value=fitting)
        ),
    ):
        task = hass.async_create_task(feature.async_calibrate(MAC))
        while not hass.async_add_executor_job.called:  # type: ignore[attr-defined]
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Cancelled, but the fit still runs: the lock is still held.
        assert not task.done()
        assert calibration._FITTING.locked()  # noqa: SLF001

        fitting.set_result(library.fit.return_value)
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not calibration._FITTING.locked()  # noqa: SLF001
    assert feature.runners[MAC].calibrated_params is None
    await _stop(hass)


async def test_a_recorder_that_never_purges_gives_the_most_history(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)

    with (
        _Library() as library,
        patch(
            "custom_components.nwp500.simulator.calibration.get_instance",
            MagicMock(return_value=MagicMock(keep_days=10, auto_purge=False)),
        ),
    ):
        await feature.async_calibrate(MAC)
    _, _, start, end = library.read.await_args.args
    assert end - start == timedelta(days=60)
    await _stop(hass)


async def test_the_calibration_entities_stay_available(
    hass: HomeAssistant, hass_storage, coordinator
):
    """#274 review: they read the recorder, not the heater now."""
    from custom_components.nwp500.entity import NWP500Entity

    _register_heater(hass)
    feature = await _started(hass, coordinator)
    runner = feature.runners[MAC]
    sensor = SimulatorCalibrationSensor(feature, runner)
    (button,) = create_simulator_buttons(feature)

    with patch.object(NWP500Entity, "available", property(lambda self: False)):
        assert sensor.available is True
        assert button.available is True
    await _stop(hass)


async def test_the_hourly_check_runs_until_the_simulator_stops(
    hass: HomeAssistant, hass_storage, coordinator, freezer
):
    from pytest_homeassistant_custom_component.common import (
        async_fire_time_changed,
    )

    from custom_components.nwp500.simulator.calibration import CHECK_EVERY

    _register_heater(hass)
    feature = await _started(hass, coordinator)
    with _Library() as library:
        freezer.tick(CHECK_EVERY)
        async_fire_time_changed(hass)
        # A scheduled calibration runs as the entry's background task.
        await hass.async_block_till_done(wait_background_tasks=True)
        library.fit.assert_called_once()
        assert feature.runners[MAC].calibrated_params == _fitted()

        await _stop(hass)
        freezer.tick(CALIBRATE_EVERY + CHECK_EVERY)
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        library.fit.assert_called_once()


async def test_the_action_is_registered_and_answers(
    hass: HomeAssistant, hass_storage, coordinator
):
    """Through Home Assistant's own call: its schema and response."""
    import json

    from custom_components.nwp500 import _async_setup_services

    _register_heater(hass)
    feature = await _started(hass, coordinator)
    await _async_setup_services(hass)

    with (
        patch.object(
            NWP500ServiceHandler,
            "_get_coordinator_and_mac",
            AsyncMock(return_value=(MagicMock(entry=feature.entry), MAC)),
        ),
        _Library() as library,
    ):
        response = await hass.services.async_call(
            DOMAIN,
            "calibrate_simulator",
            {"device_id": "d"},
            blocking=True,
            return_response=True,
        )
        assert library.fit.call_args.kwargs["keep_better"] is False
        # Without asking for the response, too.
        await hass.services.async_call(
            DOMAIN, "calibrate_simulator", {"device_id": "d"}, blocking=True
        )
        assert library.fit.call_args.kwargs["keep_better"] is True
    assert response is not None
    json.dumps(response)
    assert response["params"] == params_to_document(_fitted())
    await _stop(hass)


async def test_the_button_platform_adds_the_button_once_it_starts(
    hass: HomeAssistant, hass_storage, coordinator
):
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.nwp500 import button as button_platform
    from custom_components.nwp500.const import simulator_started_signal

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: {MAC: OPTIONS}},
    )
    entry.add_to_hass(hass)
    hass.config.components.add("recorder")
    added: list[list] = []
    await button_platform.async_setup_entry(
        hass, entry, lambda entities, *a: added.append(list(entities))
    )
    assert added == []

    await async_setup_simulator(hass, entry, coordinator)
    async_dispatcher_send(hass, simulator_started_signal(entry.entry_id))

    assert [type(b) for b in added[-1]] == [SimulatorCalibrateButton]
    await _stop(hass)


async def test_when_a_calibration_is_due(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    calibration = feature.calibration
    started = dt_util.utcnow()

    # Not while Home Assistant starts; then once the simulator has run.
    assert not calibration.due(MAC, started, started)
    assert calibration.due(MAC, started + FIRST_AFTER, started)

    with _Library():
        await feature.async_calibrate(MAC)
    at = dt_util.parse_datetime(calibration.record(MAC)["attempted_at"])
    assert at is not None
    assert not calibration.due(MAC, at + CALIBRATE_EVERY / 2, started)
    assert calibration.due(MAC, at + CALIBRATE_EVERY, started)

    with _Library(error=sim_calibrate.CalibrationInputError("x")):
        with pytest.raises(CalibrationError):
            await feature.async_calibrate(MAC)
    at = dt_util.parse_datetime(calibration.record(MAC)["attempted_at"])
    assert at is not None
    assert not calibration.due(MAC, at + RETRY_AFTER / 2, started)
    assert calibration.due(MAC, at + RETRY_AFTER, started)
    await _stop(hass)


async def test_the_hourly_check_calibrates_what_is_due(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)

    with _Library() as library:
        feature._async_calibrate_due(dt_util.utcnow())  # noqa: SLF001
        await hass.async_block_till_done()
        library.fit.assert_not_called()

        feature._async_calibrate_due(  # noqa: SLF001
            dt_util.utcnow() + FIRST_AFTER
        )
        await hass.async_block_till_done()
        library.fit.assert_called_once()
    assert feature.runners[MAC].calibrated_params == _fitted()

    # A failure is kept, not raised.
    with _Library(error=sim_calibrate.CalibrationInputError("x")):
        feature._async_calibrate_due(  # noqa: SLF001
            dt_util.utcnow() + CALIBRATE_EVERY + FIRST_AFTER
        )
        await hass.async_block_till_done()
    assert feature.calibration.record(MAC)["error"] == "x"
    await _stop(hass)


async def test_the_sensor_says_when_and_what_was_fitted(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    sensor = SimulatorCalibrationSensor(feature, feature.runners[MAC])

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["fitted"] == {}

    with _Library():
        record = await feature.async_calibrate(MAC)
    assert sensor.native_value == dt_util.parse_datetime(
        record["calibrated_at"]
    )
    attributes = sensor.extra_state_attributes
    assert attributes["fitted"] == {"UA": "1.2"}
    # What the latest calibration kept, and why; and what the parameters
    # themselves say is still on the defaults.
    assert attributes["kept"] == {"COP scale": "few"}
    assert attributes["on_defaults"] == [UNFITTED_TERMS[0]]
    assert attributes["notes"] == ["a note"]
    assert attributes["error"] is None
    assert attributes["refined"] is False
    await _stop(hass)


async def test_a_calibration_under_way_keeps_nothing_once_stopped(
    hass: HomeAssistant, hass_storage, coordinator
):
    """#274 review: switched off, or reloaded, while it calibrates."""
    import asyncio

    _register_heater(hass)
    feature = await _started(hass, coordinator)
    reading = asyncio.Event()
    release = asyncio.Event()
    with _Library() as library:
        record = library.record

        async def slow(*_: Any) -> RecordInputs:
            reading.set()
            await release.wait()
            return record

        library.inputs.side_effect = slow
        calibrating = hass.async_create_task(feature.async_calibrate(MAC))
        await reading.wait()
        entry = hass.config_entries.async_get_entry("entry1")
        assert entry is not None
        await async_unload_simulator(hass, entry)
        release.set()
        with pytest.raises(CalibrationError, match="stopped"):
            await calibrating
    await hass.async_block_till_done()

    assert KEY not in hass_storage
    assert feature.runners[MAC].calibrated_params is None
    assert feature.calibration.record(MAC) is None


async def test_the_button_calibrates_and_says_why_it_cant(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    (button,) = create_simulator_buttons(feature)
    assert isinstance(button, SimulatorCalibrateButton)
    assert button.unique_id == f"{MAC}_simulator_calibrate"

    with _Library() as library:
        await button.async_press()
        library.fit.assert_called_once()

    with (
        _Library(error=sim_calibrate.CalibrationInputError("too little")),
        pytest.raises(HomeAssistantError) as caught,
    ):
        await button.async_press()
    assert caught.value.translation_key == "simulator_calibration_failed"
    assert caught.value.translation_placeholders == {"reason": "too little"}
    await _stop(hass)


async def test_the_action_calibrates_and_returns_what_is_kept(
    hass: HomeAssistant, hass_storage, coordinator
):
    _register_heater(hass)
    feature = await _started(hass, coordinator)
    entry = hass.config_entries.async_get_entry("entry1")
    coordinator.entry = entry
    handler = NWP500ServiceHandler(hass)
    call = MagicMock()
    call.data = {"device_id": "d", "from_defaults": True}

    with (
        patch.object(
            handler,
            "_get_coordinator_and_mac",
            AsyncMock(return_value=(coordinator, MAC)),
        ),
        _Library() as library,
    ):
        response = await handler.async_calibrate_simulator(call)
        assert library.fit.call_args.kwargs["keep_better"] is False

    assert response == feature.calibration.record(MAC)
    await _stop(hass)

    with (
        patch.object(
            handler,
            "_get_coordinator_and_mac",
            AsyncMock(return_value=(coordinator, MAC)),
        ),
        pytest.raises(ServiceValidationError) as off,
    ):
        await handler.async_calibrate_simulator(call)
    assert off.value.translation_key == "simulator_not_enabled"
