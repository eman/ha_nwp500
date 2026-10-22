"""Calibrating the simulator from a heater's recorded history."""

from __future__ import annotations

import gzip
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from nwp500 import sim, status_history
from nwp500.sim.location_air import LocationAirModel
from nwp500.sim_calibrate import CalibrationInputError, Judged
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.nwp500.simulator as simulator_module
from custom_components.nwp500.const import (
    CONF_SIMULATOR,
    CONF_SIMULATOR_ENABLED,
    DATA_SIMULATOR,
    DOMAIN,
    simulator_calibration_issue_id,
    simulator_history_dir,
)
from custom_components.nwp500.simulator import (
    SimulatorFeature,
    async_setup_simulator,
    async_unload_simulator,
)
from custom_components.nwp500.simulator import calibration as calibration_module
from custom_components.nwp500.simulator.calibration import (
    CalibrationReport,
    day_files,
    due,
    heater_entities,
    recorded,
    run_calibration,
)
from custom_components.nwp500.simulator.calibration_entities import (
    SimulatorCalibrateButton,
    SimulatorCalibrationSensor,
    create_calibration_buttons,
    create_calibration_sensors,
)
from custom_components.nwp500.simulator.location import (
    KEPT_BASE,
    KEPT_REFIT,
    LocationFit,
)

from .conftest import MAC, status_dict

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _write_day(directory: Path, day: str, n: int, start: datetime, gz: bool):
    directory.mkdir(parents=True, exist_ok=True)
    text = "".join(
        status_history.line(start + timedelta(seconds=30 * i), status_dict())
        + "\n"
        for i in range(n)
    )
    if gz:
        with gzip.open(directory / f"{day}.jsonl.gz", "wt") as f:
            f.write(text)
    else:
        (directory / f"{day}.jsonl").write_text(text)


def test_the_days_are_read_in_order_one_status_at_a_time(tmp_path: Path):
    _write_day(tmp_path, "2026-10-21", 2, T0 + timedelta(days=1), gz=False)
    _write_day(tmp_path, "2026-10-20", 3, T0, gz=True)
    (tmp_path / "notes.txt").write_text("not a day")

    days = day_files(str(tmp_path))

    assert [day.isoformat() for day, _ in days] == ["2026-10-20", "2026-10-21"]
    readings = list(recorded([path for _, path in days]))
    assert len(readings) == 5
    assert [at for at, _ in readings] == sorted(at for at, _ in readings)
    assert day_files(str(tmp_path / "none")) == []


def test_a_damaged_day_is_a_gap_not_a_stop(tmp_path: Path, caplog):
    _write_day(tmp_path, "2026-10-20", 2, T0, gz=False)
    with (tmp_path / "2026-10-20.jsonl").open("a") as f:
        f.write("{not json\n")
    _write_day(tmp_path, "2026-10-21", 2, T0 + timedelta(days=1), gz=False)

    readings = list(recorded([path for _, path in day_files(str(tmp_path))]))

    assert len(readings) == 4
    assert "is not read" in caplog.text


def test_a_short_history_runs_and_fits_nothing(tmp_path: Path):
    """The real library on a few statuses: every term keeps the base's."""
    _write_day(tmp_path, "2026-10-20", 20, T0, gz=False)
    paths = [path for _, path in day_files(str(tmp_path))]

    done = run_calibration(paths, sim.default_params(), None)

    assert done.n_statuses == 20
    assert done.air is None
    assert "compressor" in done.calibration.params.unfitted


def test_a_calibration_is_due_weekly():
    assert due(None, T0)
    last = CalibrationReport(at=T0, duration_s=1.0, fitted=True)
    assert not due(last, T0 + timedelta(days=6))
    # A night's run a few minutes earlier than last week's still runs.
    assert due(last, T0 + timedelta(days=7) - timedelta(minutes=5))


def test_a_report_survives_its_document():
    report = CalibrationReport(
        at=T0,
        duration_s=12.34,
        fitted=True,
        terms=[{"name": "Compressor constant", "fitted": True}],
        notes=["a note"],
        statuses=100,
        gaps=2,
        air="source",
        failures=0,
    )

    assert CalibrationReport.from_document(report.as_document()) == replace(
        report, duration_s=12.3
    )
    assert CalibrationReport.from_document({"at": "never"}) is None
    assert CalibrationReport.from_document(None) is None


# ── the feature ──────────────────────────────────────────────────────


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry1",
        options={
            CONF_SIMULATOR_ENABLED: True,
            CONF_SIMULATOR: {
                MAC: {
                    "air_entity": "sensor.garage",
                    "record_history": True,
                    "history_days": 60,
                }
            },
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def raw_listeners(coordinator: MagicMock) -> list[Any]:
    listeners: list[Any] = []
    coordinator.async_add_raw_status_listener = MagicMock(
        side_effect=lambda mac, listener: (
            listeners.append(listener) or (lambda: listeners.remove(listener))
        )
    )
    return listeners


async def _started(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: MagicMock
) -> SimulatorFeature:
    await async_setup_simulator(hass, entry, coordinator)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id][DATA_SIMULATOR]


def _fake_done(*, fitted: bool) -> tuple[MagicMock, list[MagicMock]]:
    params = replace(
        sim.default_params(),
        loss=replace(sim.default_params().loss, ua_btu_hr_f=6.5),
    )
    done = MagicMock(
        n_statuses=1234, n_gaps=1, air="source", notes=("n",), judged=()
    )
    done.calibration.params = params
    term = MagicMock(
        samples="9 windows",
        error="-",
        fitted=fitted,
        why="" if fitted else "too little",
        note="",
    )
    term.name = "Standby loss"
    term.value = "6.5" if fitted else "-"
    return done, [term]


def _patched(done: Any, terms: list[Any]):
    return (
        patch.object(calibration_module, "run_calibration", return_value=done),
        patch.object(calibration_module, "report", return_value=terms),
    )


@pytest.mark.asyncio
async def test_a_fit_is_applied_reported_and_kept(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    config_dir,
):
    _write_day(
        Path(simulator_history_dir(str(config_dir), MAC)),
        "2026-10-20",
        5,
        T0,
        gz=True,
    )
    feature = await _started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    done, terms = _fake_done(fitted=True)
    record = MagicMock()
    run, rep = _patched(done, terms)
    with (
        run as run_mock,
        rep,
        patch.object(
            calibration_module, "record_inputs", AsyncMock(return_value=record)
        ) as inputs,
    ):
        report = await feature.async_calibrate(MAC)

    assert report is not None and report.fitted and report.applied
    assert report.statuses == 1234
    assert report.terms[0]["name"] == "Standby loss"
    assert runner.params == done.calibration.params
    # The day files, the defaults for the tank's size, and the air's record.
    paths, base, given, _refine = run_mock.call_args.args
    assert [Path(p).name for p in paths] == ["2026-10-20.jsonl.gz"]
    assert base == sim.default_params()
    assert given is record
    (_client, entities, start, _end) = inputs.call_args.args
    assert entities.air == "sensor.garage"
    # From the first status recorded.
    assert start == T0

    sensor = SimulatorCalibrationSensor(feature, MAC, "calibration")
    assert sensor.native_value == report.at
    attributes = sensor.extra_state_attributes
    assert attributes["fitted"] is True
    assert attributes["terms"][0]["value"] == "6.5"
    assert attributes["running"] is False

    # The first fit was over the defaults: not refined.
    assert run_mock.call_args.args[3] is False

    # The next run refines the fit: each group kept unless its refit
    # scores better on its latest evidence. Here the fit scores better.
    kept, kept_terms = _fake_done(fitted=False)
    kept.judged = (
        Judged("standby", "base", 0.4, 0.6, 3, "the fit scores better"),
    )
    run, rep = _patched(kept, kept_terms)
    with run as run_mock, rep:
        report = await feature.async_calibrate(MAC)
    assert run_mock.call_args.args[1] == done.calibration.params
    assert run_mock.call_args.args[3] is True
    assert runner.params == done.calibration.params
    # Judged, and kept: not a run that fitted nothing.
    assert report is not None and not report.applied
    assert report.failures == 0
    assert report.judged == [
        {
            "group": "standby",
            "kept": "base",
            "base_error": 0.4,
            "refit_error": 0.6,
            "held_out": 3,
            "why": "the fit scores better",
        }
    ]
    assert sensor.extra_state_attributes["judged"][0]["kept"] == "base"

    # A refit rejected on another ground, though it scored better (the
    # compressor's, pinned on a bound), is a run that fitted nothing.
    pinned, pinned_terms = _fake_done(fitted=False)
    pinned.judged = (
        Judged("compressor", "base", 40.0, 30.0, 2, "the refit hit a bound"),
    )
    run, rep = _patched(pinned, pinned_terms)
    with run, rep:
        report = await feature.async_calibrate(MAC)
    assert report is not None and report.failures == 1
    assert runner.params == done.calibration.params

    # A refit that scores better replaces the fit.
    better = replace(
        done.calibration.params,
        loss=replace(done.calibration.params.loss, ua_btu_hr_f=9.9),
    )
    refit, refit_terms = _fake_done(fitted=True)
    refit.calibration.params = better
    refit.judged = (Judged("standby", "refit", 0.6, 0.4, 3, "scores better"),)
    run, rep = _patched(refit, refit_terms)
    with run, rep:
        report = await feature.async_calibrate(MAC)
    assert report is not None and report.applied
    assert runner.params == better
    done.calibration.params = better

    await async_unload_simulator(hass, entry)
    feature = await _started(hass, entry, coordinator)
    assert feature.runners[MAC].params == done.calibration.params
    assert feature.reports[MAC].statuses == 1234
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_runs_that_fit_nothing_keep_the_last_and_reach_repairs(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    config_dir,
):
    _write_day(
        Path(simulator_history_dir(str(config_dir), MAC)),
        "2026-10-20",
        5,
        T0,
        gz=True,
    )
    feature = await _started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    issue_id = simulator_calibration_issue_id(entry.entry_id, MAC)
    nothing, nothing_terms = _fake_done(fitted=False)
    run, rep = _patched(nothing, nothing_terms)
    with run, rep:
        for _ in range(2):
            await feature.async_calibrate(MAC)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
        report = await feature.async_calibrate(MAC)

    assert report is not None and not report.fitted
    assert report.failures == 3
    assert runner.params is None
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "device": "Test Water Heater",
        "runs": "3",
        "reason": "no term could be fitted",
    }

    # Repairs issues don't outlive a restart: the saved failures raise it
    # again as the simulator starts.
    await async_unload_simulator(hass, entry)
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    feature = await _started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    done, terms = _fake_done(fitted=True)
    run, rep = _patched(done, terms)
    with run, rep:
        report = await feature.async_calibrate(MAC)
    assert report is not None and report.failures == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_a_run_without_history_or_features_says_why(
    hass: HomeAssistant, hass_storage, entry, coordinator, raw_listeners
):
    feature = await _started(hass, entry, coordinator)

    report = await feature.async_calibrate(MAC)
    assert report is not None
    assert report.error == (
        "no history: the heater's history is not recorded, and Home "
        "Assistant's recorder is not running"
    )
    assert report.failures == 1

    coordinator.device_features = {}
    report = await feature.async_calibrate(MAC)
    assert report is not None
    assert report.error == "the heater's features are not read yet"
    assert report.failures == 2
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_a_history_the_library_refuses_is_reported(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    config_dir,
):
    _write_day(
        Path(simulator_history_dir(str(config_dir), MAC)),
        "2026-10-20",
        1,
        T0,
        gz=True,
    )
    feature = await _started(hass, entry, coordinator)
    with patch.object(
        calibration_module,
        "run_calibration",
        side_effect=CalibrationInputError("the history holds no statuses"),
    ):
        report = await feature.async_calibrate(MAC)

    assert report is not None
    assert report.error == "the history holds no statuses"
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_night_calibrates_only_what_is_due(
    hass: HomeAssistant, hass_storage, entry, coordinator, raw_listeners
):
    feature = await _started(hass, entry, coordinator)
    calibrate = AsyncMock()
    with patch.object(feature, "async_calibrate", calibrate):
        feature._on_night(T0)  # noqa: SLF001
        await hass.async_block_till_done()
        calibrate.assert_awaited_once_with(MAC)

        calibrate.reset_mock()
        feature.reports[MAC] = CalibrationReport(
            at=datetime.now(UTC), duration_s=1.0, fitted=True
        )
        feature._on_night(T0)  # noqa: SLF001
        await hass.async_block_till_done()
        calibrate.assert_not_awaited()
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_button_calibrates_now_and_waits_while_it_runs(
    hass: HomeAssistant, hass_storage, entry, coordinator, raw_listeners
):
    feature = await _started(hass, entry, coordinator)
    button = SimulatorCalibrateButton(feature, MAC, "calibrate")
    button.hass = hass
    calibrate = AsyncMock()

    with patch.object(feature, "async_calibrate", calibrate):
        await button.async_press()
        await hass.async_block_till_done()

    calibrate.assert_awaited_once_with(MAC)
    feature.calibrating = MAC
    assert not button.available
    feature.calibrating = None
    await async_unload_simulator(hass, entry)


def _register(hass: HomeAssistant, *keys: str, disabled: str = "") -> None:
    registry = er.async_get(hass)
    for key in keys:
        platform = "binary_sensor" if key.endswith("_use") else "sensor"
        registry.async_get_or_create(
            platform,
            DOMAIN,
            f"{MAC}_{key}",
            disabled_by=(
                er.RegistryEntryDisabler.INTEGRATION
                if key == disabled
                else None
            ),
        )


def test_the_heaters_entities_are_found_by_unique_id(hass: HomeAssistant):
    _register(
        hass,
        "diagnostic_last_response",
        "tank_upper_temperature",
        "tank_lower_temperature",
        "comp_use",
        "ambient_temperature",
        disabled="ambient_temperature",
    )
    recorder = MagicMock(entity_filter=lambda e: e != "binary_sensor.comp")
    with patch.object(
        calibration_module, "get_instance", return_value=recorder
    ):
        entities, missing = heater_entities(hass, MAC)

    assert missing == []
    assert entities is not None
    assert entities.heard.startswith("sensor.")
    assert entities.upper != entities.lower
    # Recorded, and enabled.
    assert entities.compressor is not None
    assert entities.ambient is None

    recorder.entity_filter = lambda e: e != entities.heard
    with patch.object(
        calibration_module, "get_instance", return_value=recorder
    ):
        assert heater_entities(hass, MAC) == (
            None,
            ["diagnostic_last_response"],
        )


@pytest.fixture
def recorder_running(hass: HomeAssistant):
    """The recorder, keeping the tank's history from ten days ago."""
    hass.config.components.add("recorder")
    recorder = MagicMock(entity_filter=None)
    recorder.first = datetime.now(UTC) - timedelta(days=10)

    recorder.heard_first = None

    async def first_change(
        _hass: Any, entity_id: str, *_: Any
    ) -> datetime | None:
        # The response time from as far back as the tank, unless told.
        if "last_response" in entity_id and recorder.heard_first is not None:
            return recorder.heard_first
        return recorder.first

    with (
        patch.object(calibration_module, "get_instance", return_value=recorder),
        patch.object(calibration_module, "first_change", first_change),
    ):
        yield recorder
    hass.config.components.remove("recorder")


@pytest.mark.asyncio
async def test_a_heater_not_recording_is_fitted_on_its_entities(
    hass: HomeAssistant, hass_storage, coordinator, recorder_running
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry2",
        options={CONF_SIMULATOR_ENABLED: True, CONF_SIMULATOR: {MAC: {}}},
    )
    entry.add_to_hass(hass)
    _register(
        hass,
        "diagnostic_last_response",
        "tank_upper_temperature",
        "tank_lower_temperature",
    )
    feature = await _started(hass, entry, coordinator)
    assert MAC not in feature.recorders
    assert len(create_calibration_sensors(feature)) == 1
    assert len(create_calibration_buttons(feature)) == 1

    done, terms = _fake_done(fitted=True)
    history = MagicMock()
    with (
        patch.object(
            calibration_module,
            "read_heater_history",
            AsyncMock(return_value=history),
        ) as read,
        patch.object(
            calibration_module, "run_change_calibration", return_value=done
        ) as run,
        patch.object(calibration_module, "report", return_value=terms),
    ):
        report = await feature.async_calibrate(MAC)

    assert report is not None and report.fitted
    assert report.source == "entities"
    _client, entities, start, end = read.call_args.args
    assert entities.upper == er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{MAC}_tank_upper_temperature"
    )
    # From the recorder's first tank reading.
    assert start == recorder_running.first
    assert run.call_args.args[:2] == (history, sim.default_params())
    assert feature.runners[MAC].params == done.calibration.params
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_statuses_are_fitted_once_they_reach_as_far_back(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    config_dir,
    recorder_running,
):
    _register(
        hass,
        "diagnostic_last_response",
        "tank_upper_temperature",
        "tank_lower_temperature",
    )
    # Four days recorded, today among them.
    first = datetime.now(UTC) - timedelta(days=3)
    directory = Path(simulator_history_dir(str(config_dir), MAC))
    _write_day(directory, first.date().isoformat(), 5, first, gz=True)
    feature = await _started(hass, entry, coordinator)
    done, terms = _fake_done(fitted=True)

    async def calibrate() -> Any:
        with (
            patch.object(
                calibration_module,
                "read_heater_history",
                AsyncMock(return_value=MagicMock()),
            ),
            patch.object(
                calibration_module, "run_change_calibration", return_value=done
            ),
            patch.object(
                calibration_module, "run_calibration", return_value=done
            ),
            patch.object(calibration_module, "report", return_value=terms),
            patch.object(
                calibration_module,
                "record_inputs",
                AsyncMock(return_value=None),
            ),
        ):
            return await feature.async_calibrate(MAC)

    # The recorder's ten days reach further back.
    report = await calibrate()
    assert report is not None and report.source == "entities"
    # Its history only from the last hour: the statuses do.
    recorder_running.first = datetime.now(UTC) - timedelta(hours=1)
    report = await calibrate()
    assert report is not None and report.source == "statuses"
    # No tank history at all: the statuses.
    recorder_running.first = None
    report = await calibrate()
    assert report is not None and report.source == "statuses"
    # Ten days of the tank, but the response time only from the last hour:
    # the entities' history starts there, and the statuses reach further.
    recorder_running.first = datetime.now(UTC) - timedelta(days=10)
    recorder_running.heard_first = datetime.now(UTC) - timedelta(hours=1)
    report = await calibrate()
    assert report is not None and report.source == "statuses"
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_a_source_that_fails_gives_way_to_the_other(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    config_dir,
    recorder_running,
):
    _register(
        hass,
        "diagnostic_last_response",
        "tank_upper_temperature",
        "tank_lower_temperature",
    )
    first = datetime.now(UTC) - timedelta(days=3)
    directory = Path(simulator_history_dir(str(config_dir), MAC))
    _write_day(directory, first.date().isoformat(), 5, first, gz=True)
    feature = await _started(hass, entry, coordinator)
    done, terms = _fake_done(fitted=True)
    with (
        patch.object(
            calibration_module,
            "read_heater_history",
            AsyncMock(side_effect=HomeAssistantError("no probe history")),
        ),
        patch.object(calibration_module, "run_calibration", return_value=done),
        patch.object(calibration_module, "report", return_value=terms),
        patch.object(
            calibration_module, "record_inputs", AsyncMock(return_value=None)
        ),
    ):
        report = await feature.async_calibrate(MAC)

    # The entities reach further back, fail, and the statuses are fitted.
    assert report is not None and report.source == "statuses"
    assert report.fitted
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_an_unreadable_history_is_a_failed_run(
    hass: HomeAssistant, hass_storage, entry, coordinator, raw_listeners
):
    feature = await _started(hass, entry, coordinator)
    with patch.object(
        calibration_module, "day_files", side_effect=PermissionError("denied")
    ):
        report = await feature.async_calibrate(MAC)

    assert report is not None
    assert report.error == "the recorded history is not read: denied"
    assert report.failures == 1
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_an_account_with_several_heaters_fits_only_statuses(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    recorder_running,
):
    _register(
        hass,
        "diagnostic_last_response",
        "tank_upper_temperature",
        "tank_lower_temperature",
    )
    coordinator.data["11:22:33:44:55:66"] = dict(coordinator.data[MAC])
    feature = await _started(hass, entry, coordinator)

    report = await feature.async_calibrate(MAC)

    # No statuses recorded, and the entities can't tell the heaters apart.
    assert report is not None
    assert report.error is not None
    assert "several" in report.error
    await async_unload_simulator(hass, entry)


def test_a_damaged_compressed_day_is_a_gap(tmp_path: Path, caplog):
    _write_day(tmp_path, "2026-10-20", 3, T0, gz=True)
    raw = (tmp_path / "2026-10-20.jsonl.gz").read_bytes()
    # The DEFLATE stream broken past its header.
    (tmp_path / "2026-10-20.jsonl.gz").write_bytes(
        raw[:12] + bytes(b ^ 0xFF for b in raw[12:40]) + raw[40:]
    )
    _write_day(tmp_path, "2026-10-21", 2, T0 + timedelta(days=1), gz=False)

    readings = list(recorded([path for _, path in day_files(str(tmp_path))]))

    assert len(readings) >= 2
    assert "is not read" in caplog.text


@pytest.mark.asyncio
async def test_a_recorder_that_fails_leaves_the_statuses(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    config_dir,
    recorder_running,
):
    _register(
        hass,
        "diagnostic_last_response",
        "tank_upper_temperature",
        "tank_lower_temperature",
    )
    first = datetime.now(UTC) - timedelta(days=1)
    directory = Path(simulator_history_dir(str(config_dir), MAC))
    _write_day(directory, first.date().isoformat(), 5, first, gz=True)
    feature = await _started(hass, entry, coordinator)
    done, terms = _fake_done(fitted=True)

    async def broken(*_: Any) -> None:
        raise RuntimeError("database is locked")

    with (
        patch.object(calibration_module, "first_change", broken),
        patch.object(calibration_module, "run_calibration", return_value=done),
        patch.object(calibration_module, "report", return_value=terms),
        patch.object(
            calibration_module, "record_inputs", AsyncMock(return_value=None)
        ),
    ):
        report = await feature.async_calibrate(MAC)
        assert report is not None and report.source == "statuses"

        # And with no statuses either, a failed run that says why.
        for path in directory.iterdir():
            path.unlink()
        report = await feature.async_calibrate(MAC)
    assert report is not None and report.failures == 1
    assert report.error is not None and "database is locked" in report.error
    await async_unload_simulator(hass, entry)


@pytest.mark.asyncio
async def test_the_airs_model_is_fitted_with_the_heater_and_kept(
    hass: HomeAssistant,
    hass_storage,
    entry,
    coordinator,
    raw_listeners,
    config_dir,
):
    """The forecast's model is fitted in the same run, kept, and restored."""
    _write_day(
        Path(simulator_history_dir(str(config_dir), MAC)),
        "2026-10-20",
        5,
        T0,
        gz=True,
    )
    feature = await _started(hass, entry, coordinator)
    runner = feature.runners[MAC]
    model = LocationAirModel(0.5, timedelta(hours=2), 30.0)
    done, terms = _fake_done(fitted=True)
    run, rep = _patched(done, terms)
    fitted = AsyncMock(
        return_value=LocationFit(model, KEPT_REFIT, r=0.93, n=200, error=0.8)
    )
    with run, rep, patch.object(simulator_module, "async_fit_location", fitted):
        report = await feature.async_calibrate(MAC)

    assert fitted.await_args.args[1] is runner
    assert runner.location_air == model
    assert report is not None and report.location_air is not None
    assert report.location_air["kept"] == KEPT_REFIT
    assert report.location_air["lag_hours"] == 2
    sensor = SimulatorCalibrationSensor(feature, MAC, "calibration")
    assert sensor.extra_state_attributes["location_air"]["r"] == 0.93

    # A run that keeps it leaves it in force.
    run, rep = _patched(done, terms)
    kept = AsyncMock(
        return_value=LocationFit(model, KEPT_BASE, why="no better")
    )
    with run, rep, patch.object(simulator_module, "async_fit_location", kept):
        await feature.async_calibrate(MAC)
    assert runner.location_air == model

    await async_unload_simulator(hass, entry)
    feature = await _started(hass, entry, coordinator)
    assert feature.runners[MAC].location_air == model
    assert feature.reports[MAC].location_air is not None
    await async_unload_simulator(hass, entry)

    # PR #238 review: fitted to other inputs, it is not restored.
    for changed in ({"weather_entity": "weather.other"}, {"air_entity": None}):
        options = {**entry.options[CONF_SIMULATOR][MAC], **changed}
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_SIMULATOR: {
                    MAC: {k: v for k, v in options.items() if v is not None}
                },
            },
        )
        feature = await _started(hass, entry, coordinator)
        assert feature.runners[MAC].location_air is None
        await async_unload_simulator(hass, entry)
