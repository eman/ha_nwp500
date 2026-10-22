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
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from nwp500 import sim
from nwp500 import sim_adapters as sa
from nwp500.enums import DhwOperationSetting
from nwp500.models import ReservationSchedule, TOUPeriod
from nwp500.sim.engine import SimulationError
from nwp500.sim.inputs import POWER_OFF, VACATION, Commands, Scenario, Schedules
from nwp500.sim_home_assistant import Entities, HomeAssistantEnvironment
from nwp500.sim_live import Given, HeaterReading, compose

from ..const import CONF_SIMULATOR_AIR_ENTITY, CONF_SIMULATOR_HUMIDITY_ENTITY
from .ha_client import InProcessClient

if TYPE_CHECKING:
    from nwp500.models import DeviceFeature, DeviceStatus
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

# The modes in which nothing heats.
_HEATING_DISABLED_MODES = frozenset({VACATION, POWER_OFF})

# Why an answer is missing, when it is.
WAIT_STATUS = "no_status"
WAIT_FEATURE = "no_feature_info"
WAIT_STANDBY = "standby"
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
    #: What the environment source rests on, in its words.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Answer:
    """What a walk found, or why there is none."""

    computed_at: datetime
    #: One of HEATING_STATES, or None with `waiting` or `error` set.
    state: str | None = None
    #: The next (or current) heating interval, and why it ends.
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
    reasons: tuple[str, ...] = ()
    defaults_used: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
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
        for key in ("computed_at", "start", "end"):
            doc[key] = _iso(doc[key])
        for key in ("reasons", "defaults_used", "notes"):
            doc[key] = list(doc[key])
        return doc


def _iso(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat()


def heater_reading(
    status: DeviceStatus, feature: DeviceFeature
) -> HeaterReading:
    """The heater as `compose` takes it.

    Read as the library's live source reads it
    (`nwp500.sim_live.LiveHeaterSource`).
    """
    return HeaterReading(
        state=sa.heater_state(status),
        settings=sa.settings(status),
        environment=sa.environment(status),
        params=sa.params(feature),
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
    if tou is not None:
        periods = [
            TOUPeriod.model_validate(p) for p in tou.get("reservation") or []
        ]
    return (
        sa.schedules(
            reservations=schedule, tou=periods, tou_enabled=tou_enabled
        ),
        tuple(reasons),
    )


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
    return Answer(
        computed_at=scenario.start,
        state=heating_state,
        start=None if first is None else first.start.at,
        end=None if first is None else first.end,
        stop=None if first is None else first.stop,
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
        )

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
            # The library does not model Standby.
            return WAIT_STANDBY
        reading = heater_reading(status, feature)
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
                cast("HomeAssistantClient", InProcessClient(self.hass)),
                entities,
                start=start,
                until=until,
                now=start,
            )
        # A one-shot read knows no history: `compose` leaves it unset and
        # the result names the defaults it used.
        composed = await compose(
            start=start,
            horizon=horizon,
            tz=str(self.hass.config.time_zone),
            heater=Given(reading),
            schedules=Given(schedules),
            environment=environment,
            commands=commands,
        )
        return Inputs(
            scenario=composed.scenario,
            params=composed.params,
            reasons=reasons,
            notes=tuple(environment.notes) if environment else (),
        )

    async def _async_run(self) -> None:
        if self._closed:
            return
        now = dt_util.utcnow()
        try:
            inputs = await self.async_inputs()
        except ValueError as err:
            # The library refused what the heater reported, such as a
            # schedule entry it cannot read.
            self._fail(now, err, ())
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
        self.last_duration_s = (dt_util.utcnow() - started).total_seconds()
        self._publish(answer_from(result, inputs))

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
