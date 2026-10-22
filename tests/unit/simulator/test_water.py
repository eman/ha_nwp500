"""Hot water use: the heat book, and the hot water the tank holds."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from nwp500 import sim
from nwp500 import sim_adapters as sa
from nwp500.sim.cold_supply import ColdSupply
from nwp500.sim.defaults import SITE_CIRCULATION
from nwp500.sim_calibrate import CirculationFit
from nwp500.sim_home_assistant import HeaterHistory, WaterUse

from custom_components.nwp500.simulator import water as water_module
from custom_components.nwp500.simulator.binary_sensor import (
    SimulatorBathReadyBinarySensor,
    create_simulator_binary_sensors,
)
from custom_components.nwp500.simulator.runner import Inputs
from custom_components.nwp500.simulator.sensor import (
    SimulatorHotWaterAvailableSensor,
    SimulatorShowersReadySensor,
    create_simulator_sensors,
)
from custom_components.nwp500.simulator.water import (
    CIRCULATION_HISTORY,
    REFRESH,
    WaterBook,
    async_fit_circulation,
    async_read_book,
    circulation_from_document,
    circulation_to_document,
)

from .conftest import MAC, make_status
from .test_runner import runner

METERED = {
    "meter_volume_entity": "sensor.meter",
    "inlet_entity": "sensor.inlet",
    "circulation_speed_entity": "sensor.pump",
}


def _book() -> Any:
    """A full tank's book, as the status fixture's tank stands."""
    return sa.full_tank_book(make_status(), sim.default_params())


def _use(**changes: Any) -> WaterUse:
    now = dt_util.utcnow()
    fields: dict[str, Any] = {
        "flow": [(now - timedelta(minutes=1), 0.0), (now, 2.0)],
        "runs": [(now - timedelta(hours=1), now - timedelta(minutes=50))],
        "probe": [(now, 61.0)],
        "busy": [],
        "cold": ColdSupply(t_cold_f=58.0, draws=3, newest_at=now),
        "notes": ("the plateau of 3 long draws",),
    }
    fields.update(changes)
    return WaterUse(**fields)


def _reads(use: WaterUse, book: Any) -> Any:
    history = HeaterHistory(rows=MagicMock(), observed=[dt_util.utcnow()])
    heater = MagicMock()
    return (
        patch.object(
            water_module, "read_water_use", AsyncMock(return_value=use)
        ),
        patch.object(
            water_module,
            "read_heater_history",
            AsyncMock(return_value=history),
        ),
        patch.object(
            water_module, "heater_entities", return_value=(heater, [])
        ),
        patch.object(water_module.sa, "book_from_changes", return_value=book),
    )


@pytest.mark.asyncio
async def test_the_tank_is_booked_from_the_meter_the_pump_and_the_probe(
    hass: HomeAssistant, coordinator
):
    walker = runner(hass, coordinator, **METERED)
    use = _use()
    reads, history, heaters, booking = _reads(use, _book())
    now = dt_util.utcnow()
    with reads as read_use, history, heaters, booking as book_mock:
        got = await async_read_book(hass, walker, now)

    entities = read_use.await_args.args[1]
    assert entities.meter_volume == "sensor.meter"
    assert entities.circulation_speed == "sensor.pump"
    kwargs = book_mock.call_args.kwargs
    assert kwargs["at"] == now
    assert kwargs["flow"] == use.flow
    assert kwargs["circulation"] == use.runs
    assert kwargs["inlet"] == use.probe
    assert got.book is not None
    # Counted at the mains the probe's long draws read.
    assert got.inlet_measured and got.inlet_f == 58.0
    assert got.book.inlet_f == 58.0
    assert "water: the plateau of 3 long draws" in got.notes


@pytest.mark.asyncio
async def test_the_book_is_given_the_simulators_units(
    hass: HomeAssistant, coordinator
):
    """PR #239 review: the book is given the readers' units, unconverted."""
    from nwp500.unit_system import get_unit_system, set_unit_system

    walker = runner(hass, coordinator, **METERED)
    use = _use(runs=[])
    before = get_unit_system()
    set_unit_system("metric")
    try:
        reads, history, heaters, booking = _reads(use, _book())
        with reads, history, heaters, booking as book_mock:
            await async_read_book(hass, walker, dt_util.utcnow())
    finally:
        set_unit_system(before)

    kwargs = book_mock.call_args.kwargs
    assert kwargs["flow"] == use.flow
    assert kwargs["inlet"] == use.probe


@pytest.mark.asyncio
async def test_a_pump_run_since_the_full_tank_needs_the_loops_terms(
    hass: HomeAssistant, coordinator
):
    """PR #239 review: unpriced, a run is booked as draws, its loss not."""
    walker = runner(hass, coordinator, **METERED)
    walker.params = sim.default_params()
    assert walker.params.circulation is None
    now = dt_util.utcnow()
    book = _book()
    anchored = now - timedelta(hours=book.anchor_age_h)
    since = [(anchored + timedelta(minutes=1), anchored + timedelta(minutes=5))]
    before = [(anchored - timedelta(hours=2), anchored - timedelta(hours=1))]

    for runs, booked in ((since, False), (before, True), ([], True)):
        reads, history, heaters, booking = _reads(_use(runs=runs), book)
        with reads, history, heaters, booking:
            got = await async_read_book(hass, walker, now)
        assert (got.book is not None) is booked, runs
        assert booked or any("not fitted" in n for n in got.notes)

    # With the loop's terms, a run is booked at its own flow: the meter's
    # excess over it is a draw.
    walker.circulation = SITE_CIRCULATION
    reads, history, heaters, booking = _reads(_use(runs=since), book)
    with reads, history, heaters, booking as book_mock:
        got = await async_read_book(hass, walker, now)
    assert got.book is not None
    args, kwargs = book_mock.call_args
    assert args[2].circulation == SITE_CIRCULATION
    assert kwargs["circulation_flow"] == sa.run_flow(
        since, SITE_CIRCULATION.flow_gpm
    )


def _fit(**changes: Any) -> CirculationFit:
    fields: dict[str, Any] = {
        "params": SITE_CIRCULATION,
        "kept": "refit",
        "runs": 14,
        "pairs": 9,
        "error_btu": 40.0,
        "why": "fitted",
    }
    fields.update(changes)
    return CirculationFit(**fields)


@pytest.mark.asyncio
async def test_the_loops_terms_are_fitted_from_the_meter_and_the_pump(
    hass: HomeAssistant, coordinator
):
    walker = runner(hass, coordinator, **METERED)
    use = _use()
    now = dt_util.utcnow()
    reads, history, heaters, _ = _reads(use, None)
    fit = patch.object(water_module, "fit_circulation", return_value=_fit())
    with reads as read_use, history as read_history, heaters, fit as fitting:
        got = await async_fit_circulation(hass, walker, now)

    assert got is not None and got.params == SITE_CIRCULATION
    assert read_use.await_args.kwargs["lookback"] == CIRCULATION_HISTORY
    assert read_history.await_args.args[2] == now - CIRCULATION_HISTORY
    args, kwargs = fitting.call_args
    # Fitted with the heater's walk: its terms are the base.
    assert args[2] == walker.walk_params(coordinator.device_features[MAC])
    assert kwargs["flow"] == use.flow and kwargs["runs"] == use.runs
    assert kwargs["inlet"] == use.probe


@pytest.mark.asyncio
async def test_without_a_pump_or_its_runs_nothing_is_fitted(
    hass: HomeAssistant, coordinator
):
    meter_only = runner(hass, coordinator, meter_volume_entity="sensor.meter")
    assert (
        await async_fit_circulation(hass, meter_only, dt_util.utcnow()) is None
    )

    walker = runner(hass, coordinator, **METERED)
    walker.circulation = SITE_CIRCULATION
    reads, history, heaters, _ = _reads(_use(runs=None), None)
    with reads, history, heaters:
        got = await async_fit_circulation(hass, walker, dt_util.utcnow())
    # The terms in force are kept.
    assert got is not None and got.kept == "base"
    assert got.params == SITE_CIRCULATION and "unknown" in got.why

    reads, history, heaters, _ = _reads(_use(), None)
    refused = patch.object(
        water_module, "fit_circulation", side_effect=ValueError("no runs")
    )
    with reads, history, heaters, refused:
        got = await async_fit_circulation(hass, walker, dt_util.utcnow())
    assert got is not None and got.why == "no runs"


def test_the_loops_terms_survive_storage():
    doc = circulation_to_document(SITE_CIRCULATION)
    assert circulation_from_document(doc) == SITE_CIRCULATION
    assert circulation_from_document(None) is None
    assert circulation_from_document({"flow_gpm": 1.0}) is None
    assert circulation_from_document({**doc, "flow_gpm": "nan"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use", "book", "heaters", "why"),
    [
        (_use(flow=[]), None, (MagicMock(), []), "flow is not read"),
        (_use(), None, (None, ["tank_upper_temperature"]), "does not keep"),
        (_use(), None, (MagicMock(), []), "no full tank"),
        (
            _use(),
            ValueError("the meter was silent"),
            (MagicMock(), []),
            "silent",
        ),
        # PR #239 review: unknown runs could be counted as draws.
        (_use(runs=None), None, (MagicMock(), []), "runs are unknown"),
    ],
    ids=[
        "no-flow",
        "not-recorded",
        "no-anchor",
        "meter-silent",
        "pump-unknown",
    ],
)
async def test_without_a_book_the_notes_say_why(
    hass: HomeAssistant, coordinator, use, book, heaters, why
):
    walker = runner(hass, coordinator, **METERED)
    reads, history, _, _ = _reads(use, None)
    booking = patch.object(
        water_module.sa,
        "book_from_changes",
        side_effect=book if isinstance(book, Exception) else None,
        return_value=book,
    )
    with (
        reads,
        history,
        patch.object(water_module, "heater_entities", return_value=heaters),
        booking,
    ):
        got = await async_read_book(hass, walker, dt_util.utcnow())

    assert got.book is None
    assert any(why in note for note in got.notes), got.notes


def _booked() -> Any:
    read = AsyncMock(
        side_effect=lambda _hass, _runner, now: WaterBook(
            now, _book(), inlet_f=58.0, inlet_measured=True
        )
    )
    return patch(
        "custom_components.nwp500.simulator.runner.async_read_book", read
    )


@pytest.mark.asyncio
async def test_a_metered_heater_says_how_much_hot_water_it_holds(
    hass: HomeAssistant, coordinator, freezer: Any
):
    walker = runner(hass, coordinator, **METERED, bath_gal=20.0)
    with _booked() as read:
        walker.async_start()
        await hass.async_block_till_done()
        answer = walker.answer
        assert answer is not None and answer.ok
        ready = answer.readiness
        assert ready is not None
        assert ready.deliverable_gal > 0
        assert ready.bath_gal == 20.0
        assert ready.showers_whole == int(ready.showers)
        assert "no_heat_book" not in ready.reasons
        assert read.await_count == 1

        # The scenario walks at the mains the probe read.
        inputs = await walker.async_inputs()
        assert isinstance(inputs, Inputs)
        assert inputs.scenario.environment.inlet_f == 58.0

        # Read again only after REFRESH.
        await walker._async_run()
        assert read.await_count == 1
        freezer.tick(REFRESH)
        await walker._async_run()
        assert read.await_count == 2
    walker.async_stop()


@pytest.mark.asyncio
async def test_a_draw_going_on_is_walked_as_one(
    hass: HomeAssistant, coordinator
):
    """PR #239 review: the heater's own draw flag reaches readiness."""
    # The device's booleans: 2 is on.
    coordinator.data[next(iter(coordinator.data))]["status"] = make_status(
        dhwUse=2
    )
    walker = runner(hass, coordinator, **METERED)
    with (
        _booked(),
        patch(
            "custom_components.nwp500.simulator.runner.sim.time_to_ready",
            wraps=sim.time_to_ready,
        ) as ready,
    ):
        walker.async_start()
        await hass.async_block_till_done()
    walker.async_stop()

    assert ready.call_args.kwargs["drawing"] is True
    assert walker.answer.readiness is not None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_book_that_cannot_be_read_leaves_the_walk_going(
    hass: HomeAssistant, coordinator
):
    walker = runner(hass, coordinator, **METERED)
    with patch(
        "custom_components.nwp500.simulator.runner.async_read_book",
        AsyncMock(side_effect=RuntimeError("recorder gone")),
    ):
        walker.async_start()
        await hass.async_block_till_done()

    answer = walker.answer
    assert answer is not None and answer.ok and answer.readiness is None
    assert walker.water is not None and walker.water.book is None
    assert "recorder gone" in walker.water.notes[0]
    walker.async_stop()


@pytest.mark.asyncio
async def test_an_unmetered_heater_books_nothing(
    hass: HomeAssistant, coordinator
):
    walker = runner(hass, coordinator)
    with patch(
        "custom_components.nwp500.simulator.runner.async_read_book",
        AsyncMock(),
    ) as read:
        walker.async_start()
        await hass.async_block_till_done()

    read.assert_not_awaited()
    assert walker.answer is not None and walker.answer.readiness is None
    walker.async_stop()


def _feature(*walkers: Any) -> Any:
    feature = MagicMock()
    feature.runners = {str(i): w for i, w in enumerate(walkers)}
    feature.calibrations = {}
    feature.reports = {}
    feature.calibrating = None
    return feature


@pytest.mark.asyncio
async def test_the_hot_water_entities_are_for_metered_heaters(
    hass: HomeAssistant, coordinator
):
    metered = runner(hass, coordinator, **METERED)
    plain = runner(hass, coordinator)
    feature = _feature(metered, plain)

    sensors = [
        s
        for s in create_simulator_sensors(feature)
        if isinstance(
            s, SimulatorHotWaterAvailableSensor | SimulatorShowersReadySensor
        )
    ]
    binaries = create_simulator_binary_sensors(feature)

    assert {s.runner for s in sensors} == {metered}
    assert [b.runner for b in binaries] == [metered]


@pytest.mark.asyncio
async def test_the_hot_water_entities_report_the_book(
    hass: HomeAssistant, coordinator
):
    walker = runner(hass, coordinator, **METERED, bath_gal=500.0)
    available = SimulatorHotWaterAvailableSensor(walker, "hot_water_available")
    showers = SimulatorShowersReadySensor(walker, "showers_ready")
    bath = SimulatorBathReadyBinarySensor(walker, "bath_ready")
    available.hass = showers.hass = bath.hass = hass
    assert not available.available and not bath.available

    with _booked():
        walker.async_start()
        await hass.async_block_till_done()
    ready = walker.answer.readiness  # type: ignore[union-attr]

    assert available.available
    assert available.native_value == ready.deliverable_gal
    assert available.extra_state_attributes["inlet_measured"] is True
    assert showers.native_value == ready.showers_whole
    assert showers.extra_state_attributes["shower_gal"] == ready.shower_gal
    # No tank holds 500 gallons of bath.
    assert bath.is_on is False
    attributes = bath.extra_state_attributes
    assert attributes["bath_gal"] == 500.0
    assert attributes["margin_gal"] < 0
    walker.async_stop()
