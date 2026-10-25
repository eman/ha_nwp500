"""One heater's simulation, kept up to date.

A runner turns the heater's latest status, its tank size, its schedules
and the air beside it into a scenario, walks it for 24 hours with
`nwp500.sim.simulate`, asks `nwp500.sim.time_to_ready` about its hot
water, and keeps the answers: whether the tank is heating or when it next
will, until when, when the compressor next starts, the tank's two
temperatures along the day, the electricity over the day, and the hot
water it can deliver, for showers and a bath.

The scenario is built in the event loop, where the library's unit
preference is stable; the walk runs in an executor.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from homeassistant.const import STATE_ON, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from nwp500 import sim
from nwp500 import sim_adapters as sa
from nwp500.models import ReservationSchedule, TOUPeriod
from nwp500.sim import codec
from nwp500.sim.book import Booked
from nwp500.sim.inputs import POWER_OFF, VACATION, Scenario, Schedules
from nwp500.sim.location_air import FittedLocationAir
from nwp500.sim_home_assistant import (
    Entities,
    HeaterEntities,
    HomeAssistantBook,
    HomeAssistantEnvironment,
    fit_location_air,
)
from nwp500.sim_live import (
    Built,
    Composed,
    Given,
    compose,
    heater_reading,
    overlay,
)

from ..const import (
    CONF_SIMULATOR_AIR_ENTITY,
    CONF_SIMULATOR_BATH_GAL,
    CONF_SIMULATOR_CIRCULATION_ENTITY,
    CONF_SIMULATOR_DEMAND_ENTITY,
    CONF_SIMULATOR_DRAW_FLOW_ENTITY,
    CONF_SIMULATOR_DRAW_FLOW_VOLUME,
    CONF_SIMULATOR_HUMIDITY_ENTITY,
    CONF_SIMULATOR_INLET_ENTITY,
    CONF_SIMULATOR_MIXING_VALVE_F,
    CONF_SIMULATOR_SHOWER_GAL,
    CONF_SIMULATOR_WEATHER_ENTITY,
    DOMAIN,
    DRAW_FLOW_DELIVERED,
)
from .ha_client import InProcessClient

if TYPE_CHECKING:
    from nwp500.models import DeviceStatus
    from nwp500.sim import Readiness
    from nwp500.sim.engine import HeatingInterval
    from nwp500.sim.result import SimulationResult
    from nwp500.sim_home_assistant import HomeAssistantClient

    from ..coordinator import NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# How far ahead the entities look.
HORIZON = timedelta(hours=24)
# At most one walk a minute however often the heater reports, and one every
# five minutes however quiet it is, so the answer keeps up with the clock.
COOLDOWN_S = 60.0
REFRESH_INTERVAL = timedelta(minutes=5)
# The mains' temperature, from the last 72 hours' long draws, moves slowly:
# read at most hourly, not with every walk.
INLET_REFRESH = timedelta(hours=1)
# The library fits how the air beside the heater follows the outdoor air
# on the last 3 days: fitted at most hourly, and the model kept between.
AIR_FIT_REFRESH = timedelta(hours=1)

# The heating states the entities report.
STATE_HEATING = "heating"
STATE_SCHEDULED = "scheduled"
STATE_IDLE = "idle"
STATE_DISABLED = "disabled"
HEATING_STATES = (STATE_HEATING, STATE_SCHEDULED, STATE_IDLE, STATE_DISABLED)
#: A heating's `stop` when the walk's horizon cut it, not its own end.
STOP_HORIZON = "horizon"
#: A heating's `stop` when the tank reached its target: at temperature.
STOP_TARGET = "target"

# What heats in a heating interval, as the library names the heat pump.
SOURCE_COMPRESSOR = "compressor"
# Heating intervals one starting within this of another's end are one
# heating: Electric's lower element after its upper, which the library
# ends at its target. A compressor can't restart that soon (its lockout).
CHAINED = timedelta(minutes=1)

# The modes in which nothing heats.
_HEATING_DISABLED_MODES = frozenset({VACATION, POWER_OFF})

# Why an answer is missing, when it is.
WAIT_STATUS = "no_status"
FAILED = "simulation_failed"

# A reason the integration adds to the library's, about its own inputs.
REASON_RESERVATIONS_UNKNOWN = "reservations_unknown"


@dataclass(frozen=True)
class Inputs:
    """A scenario and the parameters to walk it with."""

    scenario: Scenario
    params: Any
    #: Reasons about the inputs, beside the library's own.
    reasons: tuple[str, ...] = ()
    #: What the environment source rests on, and what the library
    #: assumed, in their words.
    notes: tuple[str, ...] = ()
    #: The heater's composed scenario, for a document to be laid over,
    #: and what its environment rests on.
    live: Composed | None = None
    live_notes: tuple[str, ...] = ()
    #: Whether the household draws hot water now, as its demand entity
    #: says; False without one.
    drawing: bool = False
    #: The tank's heat, booked from the heater's history and the draw
    #: flow's (`HomeAssistantBook`); None without a draw flow, and whether
    #: its gallons convert at the mains (`inlet_measured`).
    book: Booked | None = None
    inlet_measured: bool = False


@dataclass(frozen=True)
class PathSample:
    """The tank at one point of the walk, as the library modelled it."""

    at: datetime
    #: The upper and lower probes' temperatures, °F.
    upper_f: float
    lower_f: float
    #: The setpoint in force, °F.
    setpoint_f: float
    #: Whether anything heats over the interval ending here.
    heating: bool


#: The path's temperatures, to a tenth of a degree.
_PATH_DIGITS = 1


def path_from(result: SimulationResult) -> tuple[PathSample, ...]:
    """The tank's two temperatures along the walk, at the library's points."""
    return tuple(
        PathSample(
            at=p.at,
            upper_f=round(p.upper_f, _PATH_DIGITS),
            lower_f=round(p.lower_f, _PATH_DIGITS),
            setpoint_f=round(p.setpoint_f, _PATH_DIGITS),
            heating=p.heating,
        )
        for p in result.points
    )


@dataclass(frozen=True)
class Answer:
    """What a walk found, or why there is none."""

    computed_at: datetime
    #: One of HEATING_STATES, or None with `waiting` or `error` set.
    state: str | None = None
    #: The next (or current) heating, and why it ends: its intervals one
    #: after another (`CHAINED`), from the first's start to the last's
    #: end. The source, cause and trigger are the first's.
    start: datetime | None = None
    end: datetime | None = None
    stop: str | None = None
    source: str | None = None
    cause: str | None = None
    trigger: str | None = None
    energy_kwh: float | None = None
    capped: bool | None = None
    confidence: float | None = None
    #: Electricity over the horizon, both sources.
    energy_total_kwh: float | None = None
    #: Whether the heater reports its compressor running now.
    compressor_running: bool | None = None
    #: When the compressor next starts within the walk, and why: None
    #: while it runs, and when no start falls within the walk.
    compressor_start: datetime | None = None
    compressor_cause: str | None = None
    compressor_trigger: str | None = None
    #: The tank's two temperatures along the walk.
    path: tuple[PathSample, ...] = ()
    reasons: tuple[str, ...] = ()
    defaults_used: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: The hot water now and ahead, as `nwp500.sim.time_to_ready` gives it.
    readiness: Readiness | None = None
    #: Why there is no answer yet (WAIT_*), or that the walk failed.
    waiting: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether this is an answer, not a wait or a failure."""
        return self.state is not None

    def as_document(self) -> dict[str, Any]:
        """For diagnostics."""
        doc = dataclasses.asdict(self)
        for key in ("computed_at", "start", "end", "compressor_start"):
            doc[key] = _iso(doc[key])
        # Long: how many points, and the first and last.
        path = self.path
        doc["path"] = {
            "points": len(path),
            "first": None if not path else _iso(path[0].at),
            "last": None if not path else _iso(path[-1].at),
        }
        for key in ("reasons", "defaults_used", "notes"):
            doc[key] = list(doc[key])
        doc["readiness"] = (
            None
            if self.readiness is None
            else codec.to_document(self.readiness)
        )
        return doc

    def path_document(self, temperature_unit: str) -> dict[str, Any]:
        """The modelled path for a chart, in `temperature_unit`.

        When the current or next heating ends (none when the walk's
        horizon cuts it) and why; when the tank comes to temperature,
        only for a heating that ends at its target, not one a TOU cap, a
        mode change or a write stops short; when the compressor next
        starts; and the two probes along the walk. Why there is none,
        when there isn't.
        """

        def temperature(f: float) -> float:
            return round(
                TemperatureConverter.convert(
                    f, UnitOfTemperature.FAHRENHEIT, temperature_unit
                ),
                _PATH_DIGITS,
            )

        end = None if self.stop == STOP_HORIZON else self.end
        ready = end if self.stop == STOP_TARGET else None
        return {
            "computed_at": _iso(self.computed_at),
            "state": self.state,
            "waiting": self.waiting,
            "error": self.error,
            "unit": temperature_unit,
            "compressor_running": self.compressor_running,
            "heating_start": _iso(self.start),
            "heating_end": _iso(end),
            "ready_at": _iso(ready),
            "stop": self.stop,
            "source": self.source,
            "compressor_start": _iso(self.compressor_start),
            "compressor_trigger": self.compressor_trigger,
            "points": [
                {
                    "at": _iso(p.at),
                    "upper": temperature(p.upper_f),
                    "lower": temperature(p.lower_f),
                    "setpoint": temperature(p.setpoint_f),
                    "heating": p.heating,
                }
                for p in self.path
            ],
        }


def _iso(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat()


def heater_schedules(
    status: DeviceStatus,
    reservations: dict[str, Any] | None,
    tou: dict[str, Any] | None,
) -> tuple[Schedules, tuple[str, ...]]:
    """The heater's schedules from the coordinator's stored copies.

    A schedule not read yet does not hold the walk up. A reservation
    schedule not read is not an empty one: the answer says so. Nor are
    TOU periods not read (not `nwp500.sim_live.live_schedules`, which
    refuses them): with TOU on, the library's answer says
    `tou_schedule_unknown`.
    """
    reasons = () if reservations is not None else (REASON_RESERVATIONS_UNKNOWN,)
    schedules = sa.schedules(
        reservations=None
        if reservations is None
        else ReservationSchedule.model_validate(reservations),
        tou=None
        if tou is None
        else [
            TOUPeriod.model_validate(p) for p in tou.get("reservation") or []
        ],
        tou_enabled=bool(status.tou_status),
    )
    return schedules, reasons


def answer_from(
    result: SimulationResult,
    inputs: Inputs,
    readiness: Readiness | None = None,
) -> Answer:
    """The entities' answer from a walk, and its hot water's."""
    scenario = inputs.scenario
    state = scenario.state
    first = result.heating[0] if result.heating else None
    # The heating it is in, or the next, to its last interval.
    chain: list[HeatingInterval] = []
    for interval in result.heating:
        if chain and interval.start.at > chain[-1].end + CHAINED:
            break
        chain.append(interval)
    last = chain[-1] if chain else None
    chain_wh = (
        None
        if not chain or any(h.input_wh is None for h in chain)
        else sum(h.input_wh or 0.0 for h in chain)
    )
    heating_now = state.compressor_running or state.element_running
    if heating_now:
        heating_state = STATE_HEATING
    elif scenario.settings.mode in _HEATING_DISABLED_MODES and first is None:
        heating_state = STATE_DISABLED
    elif first is not None:
        heating_state = STATE_SCHEDULED
    else:
        heating_state = STATE_IDLE
    total = result.input_wh
    # The compressor's next start: while it runs, its run is the heating
    # the walk starts in, and the ready sensor says when that ends.
    compressor = (
        None
        if state.compressor_running
        else next(
            (h for h in result.heating if h.source == SOURCE_COMPRESSOR),
            None,
        )
    )
    return Answer(
        computed_at=scenario.start,
        state=heating_state,
        start=None if first is None else first.start.at,
        end=None if last is None else last.end,
        stop=None if last is None else last.stop,
        source=None if first is None else first.source,
        cause=None if first is None else first.start.cause,
        trigger=None if first is None else first.start.trigger,
        energy_kwh=None if chain_wh is None else round(chain_wh / 1000.0, 3),
        capped=None if last is None else last.capped,
        confidence=None if first is None else first.confidence,
        energy_total_kwh=None if total is None else round(total / 1000.0, 3),
        compressor_running=state.compressor_running,
        compressor_start=None if compressor is None else compressor.start.at,
        compressor_cause=None if compressor is None else compressor.start.cause,
        compressor_trigger=(
            None if compressor is None else compressor.start.trigger
        ),
        path=path_from(result),
        reasons=tuple(dict.fromkeys((*inputs.reasons, *result.reasons))),
        defaults_used=result.defaults_used,
        notes=inputs.notes,
        readiness=readiness,
    )


def walk(
    inputs: Inputs, *, bath_gal: float | None, shower_gal: float | None
) -> tuple[SimulationResult, Readiness]:
    """The day ahead and the hot water now. Runs in an executor."""
    return (
        sim.simulate(
            inputs.scenario,
            inputs.params,
            book=inputs.book,
            inlet_measured=inputs.inlet_measured,
        ),
        sim.time_to_ready(
            inputs.scenario,
            inputs.params,
            book=inputs.book,
            bath_gal=bath_gal,
            shower_gal=shower_gal,
            drawing=inputs.drawing,
            inlet_measured=inputs.inlet_measured,
        ),
    )


class SimulatorRunner:
    """One heater's simulation, rerun as the heater reports."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        options: dict[str, Any],
    ) -> None:
        """Set up; nothing runs until `async_start`."""
        self.hass = hass
        self.coordinator = coordinator
        self.mac_address = mac_address
        self.options = options
        self.answer: Answer | None = None
        self.last_duration_s: float | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._seen_update: float | None = None
        self._closed = False
        self._failure: str | None = None
        # When the inlet was last read, what it read, and its notes.
        self._inlet: tuple[datetime, float | None, tuple[str, ...]] | None = (
            None
        )
        # When the air model was last fitted, the fit (None for none), and
        # the library's notes on it.
        self.air_fit: (
            tuple[datetime, FittedLocationAir | None, tuple[str, ...]] | None
        ) = None
        # The walks' and the actions' reads of the environment, one at a
        # time: two callers finding a fit or the inlet due read it once.
        self._reading = asyncio.Lock()
        # The book's keeper and the history it holds; made on first use.
        self._book: HomeAssistantBook | None = None
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=COOLDOWN_S,
            immediate=True,
            function=self._async_run,
        )

    @property
    def entities(self) -> Entities:
        """The entities the library reads, each optional."""
        return Entities(
            air=self.options.get(CONF_SIMULATOR_AIR_ENTITY),
            humidity=self.options.get(CONF_SIMULATOR_HUMIDITY_ENTITY),
            inlet=self.options.get(CONF_SIMULATOR_INLET_ENTITY),
            demand=self.options.get(CONF_SIMULATOR_DEMAND_ENTITY),
            circulation_speed=self.options.get(
                CONF_SIMULATOR_CIRCULATION_ENTITY
            ),
            weather=self.options.get(CONF_SIMULATOR_WEATHER_ENTITY),
        )

    def _heater_entities(self) -> HeaterEntities | None:
        """The heater's own entities, as named here, for its history.

        Where no air sensor is chosen, the library fits the air on the
        heater's own reading while the compressor runs; the book reads
        them all. None when the heater's entities aren't registered yet.
        """
        registry = er.async_get(self.hass)

        def entity(domain: str, key: str) -> str | None:
            # A disabled entity has no state or history to read.
            unique_id = f"{self.mac_address}_{key}"
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            entry = None if entity_id is None else registry.async_get(entity_id)
            return None if entry is None or entry.disabled else entity_id

        # This heater's own: the last MQTT response is any heater's.
        heard = entity("sensor", "last_status")
        upper = entity("sensor", "tank_upper_temperature")
        lower = entity("sensor", "tank_lower_temperature")
        if heard is None or upper is None or lower is None:
            return None
        return HeaterEntities(
            heard=heard,
            upper=upper,
            lower=lower,
            compressor=entity("binary_sensor", "comp_use"),
            upper_element=entity("binary_sensor", "heat_upper_use"),
            lower_element=entity("binary_sensor", "heat_lower_use"),
            heat_source=entity("sensor", "current_heat_use"),
            dhw_in_use=entity("binary_sensor", "dhw_use"),
            target=entity("sensor", "hp_lower_off_temp_setting"),
            operation_mode=entity("sensor", "operation_mode"),
            anti_legionella=entity(
                "binary_sensor", "anti_legionella_operation_busy"
            ),
            error_code=entity("sensor", "error_code"),
            power=entity("sensor", "current_inst_power"),
            energy_to_setpoint=entity("sensor", "energy_to_setpoint"),
            ambient=entity("sensor", "ambient_temperature"),
        )

    def _keeper(self) -> HomeAssistantBook | None:
        """The book's keeper, made once the heater's entities are known.

        None without a draw flow: there is no book, and the library
        answers from the tank's probes. A heater entity renamed or
        disabled makes a new keeper, which reads its history afresh. The
        flow's gallons count delivered water, or tank water when chosen
        (the library's `flow_volume`).
        """
        flow = self.options.get(CONF_SIMULATOR_DRAW_FLOW_ENTITY)
        if flow is None:
            return None
        heater = self._heater_entities()
        if heater is None:
            self._book = None
            return None
        volume = self.options.get(
            CONF_SIMULATOR_DRAW_FLOW_VOLUME, DRAW_FLOW_DELIVERED
        )
        if (
            self._book is None
            or self._book.heater != heater
            or self._book.flow_volume != volume
        ):
            e = self.entities
            self._book = HomeAssistantBook(
                cast("HomeAssistantClient", InProcessClient(self.hass)),
                heater,
                flow,
                Entities(
                    circulation_speed=e.circulation_speed,
                    inlet=e.inlet,
                    demand=e.demand,
                    air=e.air,
                ),
                flow_volume=volume,
            )
        return self._book

    async def _async_book(
        self, at: datetime, params: Any
    ) -> tuple[Booked | None, bool, tuple[str, ...]]:
        """The tank's heat at `at`, and whether it converts at the mains.

        The keeper reads only the history since its last read; booking
        from what it holds is CPU work, run in the executor.
        """
        keeper = self._keeper()
        if keeper is None:
            if self.options.get(CONF_SIMULATOR_DRAW_FLOW_ENTITY) is None:
                return None, False, ()
            return (
                None,
                False,
                ("book: the heater's entities aren't registered",),
            )
        await keeper.read(at)
        answer = await self.hass.async_add_executor_job(keeper.book, at, params)
        return answer.booked, answer.inlet_measured, answer.notes

    def _environment(
        self, start: datetime, until: datetime
    ) -> HomeAssistantEnvironment | None:
        """The air and humidity's source for one walk; None for neither.

        The inlet is read apart, at most hourly (`_async_inlet`), and the
        air model is fitted apart (`_async_fit_air`): the walk is given
        the kept fit. Without one, the weather is left out.
        """
        entities = dataclasses.replace(self.entities, inlet=None)
        fit = self.air_fit
        fitted = None if fit is None else fit[1]
        if fitted is None:
            entities = dataclasses.replace(entities, weather=None)
        if not any((entities.air, entities.humidity, entities.weather)):
            return None
        return HomeAssistantEnvironment(
            cast("HomeAssistantClient", InProcessClient(self.hass)),
            entities,
            start=start,
            until=until,
            now=start,
            fitted_air=fitted,
            heater=self._heater_entities() if fitted else None,
            run=self.hass.async_add_executor_job,
        )

    async def _async_fit_air(self, now: datetime) -> tuple[str, ...]:
        """Fit the air to the weather when due, at most hourly.

        The library reads the last 3 days of the air's history and the
        weather's, and its arithmetic runs in the executor. Its notes on
        a fit that gave none, for the walks until the next.
        """
        weather = self.entities.weather
        if weather is None:
            return ()
        fit = self.air_fit
        if fit is None or now - fit[0] >= AIR_FIT_REFRESH:
            fitted, notes = await fit_location_air(
                cast("HomeAssistantClient", InProcessClient(self.hass)),
                Entities(air=self.entities.air, weather=weather),
                now=now,
                heater=self._heater_entities(),
                run=self.hass.async_add_executor_job,
            )
            fit = self.air_fit = (now, fitted, tuple(notes))
        # A model's notes the environment repeats on every walk.
        return () if fit[1] is not None else fit[2]

    def _drawing(self) -> bool:
        """Whether the demand entity says the household draws hot water."""
        demand = self.options.get(CONF_SIMULATOR_DEMAND_ENTITY)
        state = None if demand is None else self.hass.states.get(demand)
        return state is not None and state.state == STATE_ON

    @callback
    def async_start(self) -> None:
        """Follow the heater's reports, and the clock."""
        self._unsubs.append(
            self.coordinator.async_add_listener(self._on_coordinator_update)
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_interval, REFRESH_INTERVAL
            )
        )
        self._on_coordinator_update()

    @callback
    def async_stop(self) -> None:
        """Stop; a walk still in the executor is dropped when it ends."""
        self._closed = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._debouncer.async_shutdown()

    def _stopped(self) -> bool:
        return self._closed

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `listener` with each new answer."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _device_data(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        entry = data.get(self.mac_address)
        return entry if isinstance(entry, dict) else None

    @callback
    def _on_coordinator_update(self) -> None:
        # The coordinator also tells its listeners about features,
        # schedules and energy; only a new status moves the tank.
        device_data = self._device_data()
        if device_data is None or device_data.get("status") is None:
            return
        last_update = device_data.get("last_update")
        if last_update == self._seen_update:
            return
        self._seen_update = last_update
        self.hass.async_create_task(self._debouncer.async_call())

    @callback
    def _on_interval(self, _now: datetime) -> None:
        self.hass.async_create_task(self._debouncer.async_call())

    async def async_inputs(
        self, *, start: datetime | None = None
    ) -> Inputs | str:
        """The scenario now, or why there is none (a WAIT_* reason).

        Runs in the event loop: the library converts units with the
        process-wide preference, which the coordinator changes there.
        """
        device_data = self._device_data()
        status = None if device_data is None else device_data.get("status")
        if status is None:
            return WAIT_STATUS
        # Without the feature info the tank size is unknown: the library
        # takes its defaults, and its notes say so.
        feature = self.coordinator.device_features.get(self.mac_address)
        reading = heater_reading(status, feature)
        schedules, reasons = heater_schedules(
            status,
            self.coordinator.reservation_schedules.get(self.mac_address),
            self.coordinator.tou_schedules.get(self.mac_address),
        )
        start = start or dt_util.utcnow().replace(microsecond=0)
        until = start + HORIZON
        async with self._reading:
            fit_notes = await self._async_fit_air(start)
            environment = self._environment(start, until)
            # A one-shot read knows no history: `compose` leaves it unset
            # and the result names the defaults it used.
            composed = await compose(
                start=start,
                horizon=HORIZON,
                tz=str(self.hass.config.time_zone),
                heater=Given(reading),
                schedules=Given(schedules),
                environment=environment,
            )
            inlet_f, inlet_notes = await self._async_inlet(start)
            # The inlet laid on as the CLI lays a given one, and the
            # parameters the heater's tank size gives, or the library's
            # defaults without it.
            built = overlay(
                composed,
                now=start,
                over=None
                if inlet_f is None
                else {"environment": {"inlet_f": inlet_f}},
            )
            built = self.with_valve(built)
            book, inlet_measured, book_notes = await self._async_book(
                start, built.params
            )
        live_notes = (
            *(environment.notes if environment else ()),
            *fit_notes,
            *inlet_notes,
            *book_notes,
        )
        return Inputs(
            scenario=built.scenario,
            params=built.params,
            reasons=reasons,
            notes=(*live_notes, *built.notes),
            live=dataclasses.replace(composed, scenario=built.scenario),
            live_notes=live_notes,
            drawing=self._drawing(),
            book=book,
            inlet_measured=inlet_measured,
        )

    def with_valve(self, built: Built) -> Built:
        """`built` with the mixing valve chosen, if any: the heater's.

        The library's line is intercept + slope x the tank's upper zone,
        never above the tank: a valve set to deliver T is T flat.
        """
        valve = self.options.get(CONF_SIMULATOR_MIXING_VALVE_F)
        if valve is None:
            return built
        params = built.params
        return dataclasses.replace(
            built,
            params=dataclasses.replace(
                params,
                draw=dataclasses.replace(
                    params.draw,
                    delivery_intercept_f=float(valve),
                    delivery_slope=0.0,
                ),
            ),
        )

    async def _async_inlet(
        self, now: datetime
    ) -> tuple[float | None, tuple[str, ...]]:
        """The mains' temperature, and its notes; read at most hourly.

        The library reads it from the supply probe over the last 72 hours'
        long draws, which the demand entity marks and the pump's runs
        leave out: a read of that history every walk would be most of the
        walk's work.
        """
        entities = self.entities
        if entities.inlet is None:
            return None, ()
        cached = self._inlet
        if cached is not None and now - cached[0] < INLET_REFRESH:
            return cached[1], cached[2]
        source = HomeAssistantEnvironment(
            cast("HomeAssistantClient", InProcessClient(self.hass)),
            Entities(
                inlet=entities.inlet,
                demand=entities.demand,
                circulation_speed=entities.circulation_speed,
            ),
            start=now,
            until=now + HORIZON,
            now=now,
            run=self.hass.async_add_executor_job,
        )
        environment = await source.read()
        self._inlet = (now, environment.inlet_f, tuple(source.notes))
        return self._inlet[1], self._inlet[2]

    async def _async_run(self) -> None:
        if self._closed:
            return
        now = dt_util.utcnow()
        try:
            inputs = await self.async_inputs()
        except Exception as err:  # noqa: BLE001
            # Such as the library refusing a schedule entry it cannot read.
            self._fail(now, err, ())
            return
        if isinstance(inputs, str):
            self._publish(Answer(computed_at=now, waiting=inputs))
            return
        started = dt_util.utcnow()
        try:
            result, readiness = await self.hass.async_add_executor_job(
                partial(
                    walk,
                    inputs,
                    bath_gal=self.options.get(CONF_SIMULATOR_BATH_GAL),
                    shower_gal=self.options.get(CONF_SIMULATOR_SHOWER_GAL),
                )
            )
        except Exception as err:  # noqa: BLE001
            # Whatever the library raised, the last answer is no longer
            # current.
            self._fail(now, err, inputs.notes)
            return
        if self._stopped():
            # Stopped while the walk ran: the answer is no one's.
            return
        if self._failure is not None:
            _LOGGER.info(
                "The hot water simulation for %s runs again", self.mac_address
            )
            self._failure = None
        self.last_duration_s = (dt_util.utcnow() - started).total_seconds()
        self._publish(answer_from(result, inputs, readiness))

    @callback
    def _fail(
        self, now: datetime, err: Exception, notes: tuple[str, ...]
    ) -> None:
        """Publish a failed answer; log a failure once, not every walk."""
        if self._failure != str(err):
            _LOGGER.warning(
                "The hot water simulation for %s failed: %s",
                self.mac_address,
                err,
            )
        self._failure = str(err)
        self._publish(Answer(computed_at=now, error=FAILED, notes=notes))

    @callback
    def _publish(self, answer: Answer) -> None:
        if self._closed:
            return
        self.answer = answer
        for listener in list(self._listeners):
            listener()
