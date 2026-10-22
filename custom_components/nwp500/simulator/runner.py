"""One heater's simulation, kept up to date.

A runner turns the heater's latest status, its tank size, its schedules
and the air beside it into a scenario, walks it for 24 hours with
`nwp500.sim.simulate`, and keeps the answer: whether the tank is heating
or when it next will, until when, and the electricity over the day.

The scenario is built in the event loop, where the library's unit
preference is stable; the walk runs in an executor.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.json import json_loads

from nwp500 import sim
from nwp500 import sim_adapters as sa
from nwp500.enums import DhwOperationSetting
from nwp500.models import ReservationSchedule, TOUPeriod
from nwp500.sim.delivery import booked_deliverable
from nwp500.sim.engine import SimulationError
from nwp500.sim.inputs import (
    Commands,
    Environment,
    History,
    Scenario,
    Schedules,
)
from nwp500.sim.ready import showers_ready_for, whole_showers
from nwp500.sim_home_assistant import (
    Entities,
    HomeAssistantEnvironment,
    fresh,
    parse_state,
    to_f,
)
from nwp500.sim_live import Given, HeaterReading, compose

from ..const import (
    CONF_SIMULATOR_AIR_ENTITY,
    CONF_SIMULATOR_BATH_GAL,
    CONF_SIMULATOR_CIRCULATION_ENTITY,
    CONF_SIMULATOR_HUMIDITY_ENTITY,
    CONF_SIMULATOR_INLET_ENTITY,
    CONF_SIMULATOR_METER_FLOW_ENTITY,
    CONF_SIMULATOR_METER_VOLUME_ENTITY,
    CONF_SIMULATOR_SHOWER_GAL,
    CONF_SIMULATOR_WEATHER_ENTITY,
)
from .ha_client import Forecasts, InProcessClient
from .water import REFRESH, WaterBook, async_read_book

if TYPE_CHECKING:
    from nwp500.models import DeviceFeature, DeviceStatus
    from nwp500.sim.engine import HeatingInterval
    from nwp500.sim.location_air import LocationAirModel
    from nwp500.sim.params import BookParams, CirculationParams, HeaterParams
    from nwp500.sim.ready import Readiness
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

# The heating states the entities report.
STATE_HEATING = "heating"
STATE_SCHEDULED = "scheduled"
STATE_IDLE = "idle"
STATE_DISABLED = "disabled"
HEATING_STATES = (STATE_HEATING, STATE_SCHEDULED, STATE_IDLE, STATE_DISABLED)

# The DHW operation settings in which nothing heats: Vacation, power off.
_HEATING_DISABLED_MODES = frozenset({5, 6})

# Why an answer is missing, when it is.
WAIT_STATUS = "no_status"
WAIT_FEATURE = "no_feature_info"
WAIT_STANDBY = "standby"
FAILED = "simulation_failed"

# Reasons the integration adds to the library's, about its own inputs.
REASON_RESERVATIONS_UNKNOWN = "reservations_unknown"
REASON_TOU_UNKNOWN = "tou_unknown"


@dataclass(frozen=True)
class Inputs:
    """A scenario and the parameters to walk it with."""

    scenario: Scenario
    params: Any
    #: Reasons about the inputs, beside the library's own.
    reasons: tuple[str, ...] = ()
    #: What the environment source rests on, in its words.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Answer:
    """What a walk found, or why there is none."""

    computed_at: datetime
    #: One of HEATING_STATES, or None with `waiting` or `error` set.
    state: str | None = None
    #: The next (or current) heating interval.
    start: datetime | None = None
    end: datetime | None = None
    source: str | None = None
    cause: str | None = None
    trigger: str | None = None
    energy_kwh: float | None = None
    capped: bool | None = None
    confidence: float | None = None
    #: Electricity over the horizon, both sources.
    energy_total_kwh: float | None = None
    reasons: tuple[str, ...] = ()
    defaults_used: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: Why there is no answer yet (WAIT_*), or that the walk failed.
    waiting: str | None = None
    error: str | None = None
    #: The hot water the tank holds, from the heat book; None without one.
    readiness: Ready | None = None

    @property
    def ok(self) -> bool:
        """Whether this is an answer, not a wait or a failure."""
        return self.state is not None

    def as_document(self) -> dict[str, Any]:
        """For diagnostics."""
        doc = dataclasses.asdict(self)
        for key in ("computed_at", "start", "end"):
            doc[key] = _iso(doc[key])
        if self.readiness is not None:
            doc["readiness"] = self.readiness.as_document()
        for key in ("reasons", "defaults_used", "notes"):
            doc[key] = list(doc[key])
        return doc


@dataclass(frozen=True)
class Ready:
    """The hot water the tank holds now, and when a bath is ready.

    Gallons, from the heat book (`sim.time_to_ready`).
    """

    deliverable_gal: float
    showers: float
    showers_whole: int
    shower_gal: float
    bath_gal: float
    bath_ready: bool
    bath_margin_gal: float
    #: When the heat pump has the bath ready, if not now; None when it
    #: won't within the walk.
    bath_ready_at: datetime | None
    reasons: tuple[str, ...] = ()
    defaults_used: tuple[str, ...] = ()

    @classmethod
    def from_readiness(cls, readiness: Readiness) -> Ready:
        """The numbers the entities show."""
        bath, showers = readiness.bath, readiness.showers
        return cls(
            deliverable_gal=round(readiness.deliverable_gal, 1),
            showers=showers.ready_for,
            showers_whole=showers.ready_for_whole,
            shower_gal=showers.volume_gal,
            bath_gal=bath.volume_gal,
            bath_ready=bath.ready,
            bath_margin_gal=round(bath.margin_gal, 1),
            bath_ready_at=None if bath.ready else bath.ready_at,
            reasons=tuple(readiness.reasons),
            defaults_used=tuple(readiness.defaults_used),
        )

    def as_document(self) -> dict[str, Any]:
        """For diagnostics."""
        doc = dataclasses.asdict(self)
        doc["bath_ready_at"] = _iso(self.bath_ready_at)
        doc["reasons"] = list(self.reasons)
        doc["defaults_used"] = list(self.defaults_used)
        return doc


def _instant(epoch: float | None) -> datetime | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC)


@dataclass
class HeatingHistory:
    """When the compressor last stopped and the tank last heated.

    As the integration saw it from the heater's reports.
    """

    compressor_stopped_at: datetime | None = None
    heated_at: datetime | None = None
    _compressor_was_on: bool | None = field(default=None, repr=False)

    def observe(self, status: DeviceStatus, at: datetime) -> bool:
        """Take one report; return whether anything changed."""
        compressor_on = bool(status.comp_use)
        heating = (
            compressor_on
            or bool(status.heat_upper_use)
            or bool(status.heat_lower_use)
        )
        changed = False
        if self._compressor_was_on and not compressor_on:
            self.compressor_stopped_at = at
            changed = True
        self._compressor_was_on = compressor_on
        if heating:
            self.heated_at = at
            changed = True
        return changed

    def as_history(self) -> History:
        """The simulator's history."""
        return History(
            compressor_stopped_at=self.compressor_stopped_at,
            heated_at=self.heated_at,
        )

    def as_document(self) -> dict[str, str | None]:
        """For storage."""
        return {
            "compressor_stopped_at": _iso(self.compressor_stopped_at),
            "heated_at": _iso(self.heated_at),
        }

    @classmethod
    def from_document(cls, doc: Any) -> HeatingHistory:
        """The inverse of `as_document`; anything unreadable is unknown."""
        if not isinstance(doc, dict):
            return cls()
        return cls(
            compressor_stopped_at=_parse(doc.get("compressor_stopped_at")),
            heated_at=_parse(doc.get("heated_at")),
        )


def _iso(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat()


def _parse(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def heater_reading(
    status: DeviceStatus,
    feature: DeviceFeature,
    params: HeaterParams | None = None,
) -> HeaterReading:
    """The heater as `compose` takes it.

    Its air is its own sensor's reading only while the compressor runs,
    when the fan draws the room's air past it; idle, the sensor reads the
    cabinet, several degrees warm, so the air is left to the simulator's
    default, which the answer names. Its parameters are `params`, its
    calibration, or the defaults for its tank's size.
    """
    reading = sa.location_air_reading(status)
    air = math.nan if reading is None else sa.to_sim_temperature(reading)
    return HeaterReading(
        state=sa.heater_state(status),
        settings=sa.settings(status),
        environment=Environment(ambient_f=air),
        params=sa.params(feature) if params is None else params,
        status=status,
        feature=feature,
    )


def heater_schedules(
    status: DeviceStatus,
    reservations: dict[str, Any] | None,
    tou: dict[str, Any] | None,
) -> tuple[Schedules, tuple[str, ...]]:
    """The heater's schedules from the coordinator's stored copies.

    A schedule not read yet is not an empty one: the answer says so.
    """
    reasons: list[str] = []
    schedule = None
    if reservations is None:
        reasons.append(REASON_RESERVATIONS_UNKNOWN)
    else:
        schedule = ReservationSchedule.model_validate(reservations)
    periods: list[TOUPeriod] | None = None
    tou_enabled = bool(status.tou_status)
    if tou is None:
        if tou_enabled:
            reasons.append(REASON_TOU_UNKNOWN)
    else:
        periods = [
            TOUPeriod.model_validate(p) for p in tou.get("reservation") or []
        ]
    return (
        sa.schedules(
            reservations=schedule, tou=periods, tou_enabled=tou_enabled
        ),
        tuple(reasons),
    )


# Heating that starts within this of the last one's end goes on from it:
# the elements' upper zone then their lower, or a mode change handing the
# compressor's heating to the elements.
_HANDOFF = timedelta(minutes=1)
# Why a heating stops short of done: the walk ended, or the mode changed.
_UNFINISHED = frozenset({"horizon", "mode"})


def _ready_at(heating: Sequence[HeatingInterval]) -> datetime | None:
    """When the next heating is done.

    The end of the last interval in an unbroken run from the first, unless
    that one stopped short.
    """
    if not heating:
        return None
    last = heating[0]
    for interval in heating[1:]:
        if interval.start.at > last.end + _HANDOFF:
            break
        last = interval
    return None if last.stop in _UNFINISHED else last.end


def answer_from(result: SimulationResult, inputs: Inputs) -> Answer:
    """The entities' answer from a walk."""
    scenario = inputs.scenario
    state = scenario.state
    first = result.heating[0] if result.heating else None
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
    ready = _ready_at(result.heating)
    return Answer(
        computed_at=scenario.start,
        state=heating_state,
        start=None if first is None else first.start.at,
        end=ready,
        source=None if first is None else first.source,
        cause=None if first is None else first.start.cause,
        trigger=None if first is None else first.start.trigger,
        energy_kwh=(
            None
            if first is None or first.input_wh is None
            else round(first.input_wh / 1000.0, 3)
        ),
        capped=None if first is None else first.capped,
        confidence=None if first is None else first.confidence,
        energy_total_kwh=None if total is None else round(total / 1000.0, 3),
        reasons=tuple(dict.fromkeys((*inputs.reasons, *result.reasons))),
        defaults_used=result.defaults_used,
        notes=inputs.notes,
    )


class SimulatorRunner:
    """One heater's simulation, rerun as the heater reports."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        options: dict[str, Any],
        history: HeatingHistory,
        save_history: Callable[[], None],
    ) -> None:
        """Set up; nothing runs until `async_start`."""
        self.hass = hass
        self.coordinator = coordinator
        self.mac_address = mac_address
        self.options = options
        self.history = history
        self._save_history = save_history
        self.answer: Answer | None = None
        #: The heater's calibrated parameters; None for the defaults.
        self.params: HeaterParams | None = None
        #: How the air beside the heater follows the outdoor air, fitted
        #: by calibration; None: the air is held at its reading.
        self.location_air: LocationAirModel | None = None
        self._forecasts: Forecasts = {}
        #: The heat book as last read, with a meter; None until then.
        self.water: WaterBook | None = None
        #: The book's terms and the circulation loop's, fitted from the
        #: meter apart from the heater's own (`water.async_fit_book`);
        #: None until they are.
        self.book: BookParams | None = None
        self.circulation: CirculationParams | None = None
        # Counts the terms set: a read begun under others is dropped.
        self._water_terms_set = 0
        self.last_duration_s: float | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._seen_update: float | None = None
        self._closed = False
        self._failure: str | None = None
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=COOLDOWN_S,
            immediate=True,
            function=self._async_run,
        )

    @property
    def entities(self) -> Entities:
        """The entities the environment is read from."""
        return Entities(
            air=self.options.get(CONF_SIMULATOR_AIR_ENTITY),
            humidity=self.options.get(CONF_SIMULATOR_HUMIDITY_ENTITY),
            weather=self.options.get(CONF_SIMULATOR_WEATHER_ENTITY),
        )

    @property
    def water_entities(self) -> Entities:
        """The entities the water use is read from."""
        return Entities(
            meter_volume=self.options.get(CONF_SIMULATOR_METER_VOLUME_ENTITY),
            meter_flow=self.options.get(CONF_SIMULATOR_METER_FLOW_ENTITY),
            inlet=self.options.get(CONF_SIMULATOR_INLET_ENTITY),
            circulation_speed=self.options.get(
                CONF_SIMULATOR_CIRCULATION_ENTITY
            ),
        )

    @property
    def has_meter(self) -> bool:
        """Whether the heater's water is metered: it can be booked."""
        return self.water_entities.meter_volume is not None

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
        at = _instant(last_update) or dt_util.utcnow()
        if self.history.observe(device_data["status"], at):
            self._save_history()
        self.hass.async_create_task(self._debouncer.async_call())

    @callback
    def _on_interval(self, _now: datetime) -> None:
        self.hass.async_create_task(self._debouncer.async_call())

    @callback
    def async_set_params(self, params: HeaterParams | None) -> None:
        """Walk with `params` from the next run on (None: the defaults)."""
        self.params = params
        self.hass.async_create_task(self._debouncer.async_call())

    def _anchorable(self, entity: str | None, now: datetime) -> bool:
        """Whether `entity` has a fresh temperature to anchor the air to."""
        if entity is None or (state := self.hass.states.get(entity)) is None:
            return False
        reading = parse_state(json_loads(state.as_dict_json))
        if reading.value is None or not fresh(reading, now):
            return False
        try:
            to_f(reading.value, reading.unit)
        except ValueError:
            return False
        return True

    def walk_params(self, feature: DeviceFeature) -> HeaterParams:
        """The heater's parameters, with the meter's terms where fitted.

        The heater's are its calibration's, or the defaults for its tank;
        the book's and the loop's terms are the meter's fit.
        """
        params = sa.params(feature) if self.params is None else self.params
        if self.book is None:
            return params
        return dataclasses.replace(
            params, book=self.book, circulation=self.circulation
        )

    @callback
    def async_set_water_terms(
        self, book: BookParams | None, circulation: CirculationParams | None
    ) -> None:
        """Book and walk by these terms from the next read on."""
        self.book = book
        self.circulation = circulation
        self._water_terms_set += 1
        # Booked again with them, not in REFRESH.
        self.water = None
        self.hass.async_create_task(self._debouncer.async_call())

    @callback
    def async_set_location_air(self, model: LocationAirModel | None) -> None:
        """Project the air by `model` from the next run on (None: don't)."""
        self.location_air = model
        self.hass.async_create_task(self._debouncer.async_call())

    async def async_inputs(
        self,
        *,
        start: datetime | None = None,
        horizon: timedelta = HORIZON,
        commands: Commands | None = None,
    ) -> Inputs | str:
        """The scenario now, or why there is none (a WAIT_* reason).

        Runs in the event loop: the library converts units with the
        process-wide preference, which the coordinator changes there.
        """
        device_data = self._device_data()
        status = None if device_data is None else device_data.get("status")
        if status is None:
            return WAIT_STATUS
        feature = self.coordinator.device_features.get(self.mac_address)
        if feature is None:
            return WAIT_FEATURE
        if status.dhw_operation_setting == DhwOperationSetting.STANDBY:
            # The library has no settings for Standby: nothing heats.
            return WAIT_STANDBY
        reading = heater_reading(status, feature, self.walk_params(feature))
        schedules, reasons = heater_schedules(
            status,
            self.coordinator.reservation_schedules.get(self.mac_address),
            self.coordinator.tou_schedules.get(self.mac_address),
        )
        start = start or dt_util.utcnow().replace(microsecond=0)
        until = start + horizon
        environment = None
        entities = self.entities
        if entities.air is not None or entities.humidity is not None:
            environment = HomeAssistantEnvironment(
                cast(
                    "HomeAssistantClient",
                    InProcessClient(self.hass, self._forecasts),
                ),
                entities,
                start=start,
                until=until,
                now=start,
                # Without a reading to anchor to, a projection is only as
                # good as the model's offset, which calibration doesn't
                # judge: the air is then left as with no forecast.
                location_air=(
                    self.location_air
                    if self._anchorable(entities.air, start)
                    else None
                ),
            )
        composed = await compose(
            start=start,
            horizon=horizon,
            tz=str(self.hass.config.time_zone),
            heater=Given(reading),
            schedules=Given(schedules),
            environment=environment,
            commands=commands,
        )
        update: dict[str, Any] = {"history": self.history.as_history()}
        water = self.water
        if water is not None and water.inlet_f is not None:
            # The mains, from the probe's long draws.
            update["environment"] = composed.scenario.environment.model_copy(
                update={"inlet_f": water.inlet_f}
            )
        scenario = composed.scenario.model_copy(update=update)
        return Inputs(
            scenario=scenario,
            params=composed.params,
            reasons=reasons,
            notes=tuple(environment.notes) if environment else (),
        )

    async def _async_refresh_water(self, now: datetime) -> None:
        """Read the heat book again, every `REFRESH`."""
        if not self.has_meter:
            self.water = None
            return
        if self.water is not None and now - self.water.at < REFRESH:
            return
        terms_set = self._water_terms_set
        try:
            water = await async_read_book(self.hass, self, now)
        except Exception as err:
            # Anything the recorder or the library raises leaves the
            # tank unbooked until the next read, not the runner stopped.
            _LOGGER.warning(
                "The hot water for %s is not booked: %s", self.mac_address, err
            )
            water = WaterBook(now, None, notes=(f"book: not read ({err})",))
        if terms_set == self._water_terms_set:
            self.water = water
        # Else new terms came while it read: it is read again with them.

    @callback
    def _standby_readiness(self) -> Ready | None:
        """The hot water the tank holds in Standby, from the book alone.

        Nothing heats: what it holds now is all there is, and a bath it
        can't run now has no time it will be ready.
        """
        water = self.water
        device_data = self._device_data()
        status = None if device_data is None else device_data.get("status")
        feature = self.coordinator.device_features.get(self.mac_address)
        if water is None or water.book is None or not status or not feature:
            return None
        params = self.walk_params(feature)
        state = sa.heater_state(status)
        deliverable = booked_deliverable(
            water.book,
            state.upper_f,
            state.lower_f,
            params.readiness,
            params.book_terms(),
        )
        defaults: list[str] = []
        bath_gal = self.options.get(CONF_SIMULATOR_BATH_GAL)
        if bath_gal is None:
            bath_gal = params.readiness.bath_gal
            defaults.append(f"bath_gal={bath_gal}")
        shower_gal = self.options.get(CONF_SIMULATOR_SHOWER_GAL)
        if shower_gal is None:
            shower_gal = params.readiness.shower_gal
            defaults.append(f"shower_gal={shower_gal}")
        showers = showers_ready_for(deliverable.gal, shower_gal)
        return Ready(
            deliverable_gal=round(deliverable.gal, 1),
            showers=showers,
            showers_whole=whole_showers(showers),
            shower_gal=round(shower_gal, 1),
            bath_gal=round(bath_gal, 1),
            bath_ready=deliverable.gal >= bath_gal,
            bath_margin_gal=round(deliverable.gal - bath_gal, 1),
            bath_ready_at=None,
            reasons=(WAIT_STANDBY,),
            defaults_used=tuple(defaults),
        )

    async def _async_readiness(self, inputs: Inputs) -> Ready | None:
        """The hot water the tank holds, with a book; None without one."""
        water = self.water
        if water is None or water.book is None:
            return None
        options = self.options
        device_data = self._device_data()
        status = None if device_data is None else device_data.get("status")
        # A draw going on keeps the heat in the cold water it leaves
        # withheld, as the heater says it is drawing.
        drawing = bool(getattr(status, "dhw_use", False))
        try:
            readiness = await self.hass.async_add_executor_job(
                functools.partial(
                    sim.time_to_ready,
                    inputs.scenario,
                    inputs.params,
                    book=water.book,
                    bath_gal=options.get(CONF_SIMULATOR_BATH_GAL),
                    shower_gal=options.get(CONF_SIMULATOR_SHOWER_GAL),
                    inlet_measured=water.inlet_measured,
                    drawing=drawing,
                )
            )
        except (SimulationError, ValueError) as err:
            _LOGGER.debug("No readiness for %s: %s", self.mac_address, err)
            return None
        return Ready.from_readiness(readiness)

    async def _async_run(self) -> None:
        if self._closed:
            return
        now = dt_util.utcnow()
        await self._async_refresh_water(now)
        if self._stopped():
            return
        try:
            inputs = await self.async_inputs()
        except ValueError as err:
            # The library refused what the heater reported, such as a
            # schedule entry it cannot read.
            self._fail(now, err, ())
            return
        if inputs == WAIT_STANDBY:
            # Nothing heats in Standby: an answer, not a wait.
            self._publish(
                Answer(
                    computed_at=now,
                    state=STATE_DISABLED,
                    energy_total_kwh=0.0,
                    reasons=(WAIT_STANDBY,),
                    readiness=self._standby_readiness(),
                )
            )
            return
        if isinstance(inputs, str):
            self._publish(Answer(computed_at=now, waiting=inputs))
            return
        started = dt_util.utcnow()
        try:
            result = await self.hass.async_add_executor_job(
                sim.simulate, inputs.scenario, inputs.params
            )
        except (SimulationError, ValueError) as err:
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
        ready = await self._async_readiness(inputs)
        if self._stopped():
            return
        self.last_duration_s = (dt_util.utcnow() - started).total_seconds()
        answer = answer_from(result, inputs)
        if ready is not None:
            answer = dataclasses.replace(answer, readiness=ready)
        self._publish(answer)

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
