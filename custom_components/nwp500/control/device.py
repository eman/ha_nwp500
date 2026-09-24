"""The controller for one heater: intake, the planner's clock, and storage.

Spec sections 2, 3, 5 and 6 of issue #158. The planning is in `engine.py`;
this module feeds it what the device reports, keeps time for it, commits the
writes it asks for, and persists what it must remember across a restart.

Only shadow execution exists so far: each write the planner asks for is
committed as simulated and never sent. Live writes are delivery step 5 of
the specification, after the device tests in its section 8.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_CONTROL_INTENT_ENTITY,
    CONF_CONTROL_MODE,
    CONF_CONTROL_SURPLUS_ENTITY,
    CONF_CONTROL_SURPLUS_THRESHOLD_KW,
    CONF_SCAN_INTERVAL,
    CONTROL_MODE_DISABLED,
    CONTROL_MODE_LIVE,
    CONTROL_MODE_SHADOW,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_SURPLUS_THRESHOLD_KW,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .capabilities import Capabilities, build_capabilities
from .engine import Planner, RaiseState, Report, State, Write
from .entries import OWNER_LABELS, schedule_hash
from .evaluate import Ack, check_plan, rejected_ack
from .intent import (
    REASON_SUPERSEDED,
    IntentRejected,
    Plan,
    document_from_attributes,
    parse_plan,
)
from .observed import Observed, observe
from .owner import OwnerProgram

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from nwp500 import Device  # type: ignore[attr-defined]

    from ..coordinator import NWP500ConfigEntry, NWP500DataUpdateCoordinator
    from .store import ControlStore

_LOGGER = logging.getLogger(__name__)

# The heartbeat entity must move at least every 15 minutes (spec section
# 5.12). Ticking at a third of that leaves room for a missed tick.
HEARTBEAT_INTERVAL = timedelta(minutes=5)

# Existing entities a consumer reads for this heater, by the unique id
# suffix the sensor platforms give them.
_TELEMETRY_UNIQUE_IDS: tuple[tuple[str, str, str], ...] = (
    ("delivery_temperature", "sensor", "tank_upper_temperature"),
    ("compressor_running", "binary_sensor", "comp_use"),
    ("power", "sensor", "current_inst_power"),
)


class DeviceControl:
    """External control of one heater."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NWP500ConfigEntry,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        device: Device,
        store: ControlStore,
    ) -> None:
        """Bind to a device. Nothing listens until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.mac_address = mac_address
        self.device = device
        self.store = store

        options = entry.options
        mode = str(options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE))
        if mode == CONTROL_MODE_LIVE:
            # Nothing can select it yet; an options file edited by hand is
            # run in shadow rather than trusted to write.
            _LOGGER.warning(
                "External control: live mode is not available yet; running "
                "in shadow"
            )
            mode = CONTROL_MODE_SHADOW
        self.mode = mode
        self.intent_entity_id: str | None = options.get(
            CONF_CONTROL_INTENT_ENTITY
        )
        self.surplus_entity_id: str | None = options.get(
            CONF_CONTROL_SURPLUS_ENTITY
        )
        self.surplus_threshold_kw = float(
            options.get(
                CONF_CONTROL_SURPLUS_THRESHOLD_KW,
                DEFAULT_CONTROL_SURPLUS_THRESHOLD_KW,
            )
        )
        poll = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

        self.plan: Plan | None = None
        self.received_at: datetime | None = None
        self.heartbeat: datetime | None = None
        self._rejected: Ack | None = None
        self._device_hash: str | None = None
        self._device_hash_seen_at: datetime | None = None
        self._feature_version = "unknown"

        self.planner = Planner(
            build_capabilities(
                self._effective_options(),
                features=coordinator.device_features.get(mac_address),
                feature_version=self._feature_version,
                telemetry={},
            ),
            dt_util.get_default_time_zone(),
            shadow=True,
            explain_window=timedelta(seconds=poll + 60),
        )

        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._cancel_event: CALLBACK_TYPE | None = None

    # -- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Restore state, adopt the plan, and start listening."""
        self._feature_version = await self._async_feature_version()
        now = dt_util.utcnow()
        self.heartbeat = now
        if engine_state := self.store.stored_engine(self.mac_address):
            try:
                self.planner.load_document(engine_state)
            except KeyError, TypeError, ValueError:
                # State from an earlier version of the feature. Nothing it
                # describes was ever written to the device, so it is dropped.
                _LOGGER.info(
                    "Discarding unreadable stored control state for %s",
                    self.mac_address,
                )
                self.planner = Planner(
                    self.planner.capabilities,
                    self.planner.tz,
                    shadow=True,
                    explain_window=self.planner.explain_window,
                )
        if owner_state := self.store.stored_owner(self.mac_address):
            self.planner.owner = OwnerProgram.from_document(owner_state)
        observed = self.observe()
        await self._async_ensure_owner(observed)

        if self.mode == CONTROL_MODE_DISABLED:
            await self._async_disable(now)
        else:
            await self.store.async_set_disabled_done(self.mac_address, False)
            if self.intent_entity_id:
                self._unsubscribe.append(
                    async_track_state_change_event(
                        self.hass,
                        [self.intent_entity_id],
                        self._on_intent_event,
                    )
                )
            if self.surplus_entity_id:
                self._unsubscribe.append(
                    async_track_state_change_event(
                        self.hass,
                        [self.surplus_entity_id],
                        self._on_surplus_event,
                    )
                )
            await self._async_adopt_initial(now, observed)

        self._unsubscribe.append(
            async_track_time_interval(
                self.hass, self._on_heartbeat, HEARTBEAT_INTERVAL
            )
        )
        self._unsubscribe.append(
            self.coordinator.async_add_listener(self._on_coordinator_update)
        )
        await self._async_evaluate(now)

    async def async_stop(self) -> None:
        """Stop listening. Writes nothing (spec section 6.4)."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        if self._cancel_event is not None:
            self._cancel_event()
            self._cancel_event = None

    async def _async_feature_version(self) -> str:
        try:
            integration = await async_get_integration(self.hass, DOMAIN)
        except Exception:  # noqa: BLE001 - the version is informational
            return "unknown"
        return str(integration.version or "unknown")

    async def _async_disable(self, now: datetime) -> None:
        """Section 6.6, once per entry into `disabled`."""
        await self.store.async_clear_intent(self.mac_address)
        if self.store.disabled_done(self.mac_address):
            return
        write = self.planner.disable(now)
        _LOGGER.info(
            "External control disabled for %s: %d entr%s removed%s",
            self.mac_address,
            len(write.removed),
            "y" if len(write.removed) == 1 else "ies",
            " (simulated)" if write.simulated else "",
        )
        await self.store.async_set_disabled_done(self.mac_address, True)
        await self._async_persist()

    # -- what the entities read -------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        """The current declaration."""
        capabilities = self._build_capabilities()
        self.planner.capabilities = capabilities
        return capabilities

    def _effective_options(self) -> dict[str, Any]:
        """The options, with the mode that is actually running.

        A hand-edited `live` runs as shadow, and the declaration must say
        so: a consumer reading `live` would believe writes reach the heater.
        """
        return {**self.entry.options, CONF_CONTROL_MODE: self.mode}

    def _build_capabilities(self) -> Capabilities:
        owner = self.planner.owner
        capabilities = build_capabilities(
            self._effective_options(),
            features=self.coordinator.device_features.get(self.mac_address),
            feature_version=self._feature_version,
            telemetry=self._telemetry_entity_ids(),
            owner_program=owner.as_attributes() if owner else None,
        )
        observed = self.observe()
        if observed.reservations is None:
            return capabilities
        in_use = len(self.planner.program(observed)["reservation"])
        return replace(
            capabilities,
            entries_available=max(
                capabilities.entry_limit - in_use - capabilities.entry_reserve,
                0,
            ),
        )

    def _telemetry_entity_ids(self) -> dict[str, str | None]:
        registry = er.async_get(self.hass)
        return {
            name: registry.async_get_entity_id(
                platform, DOMAIN, f"{self.mac_address}_{suffix}"
            )
            for name, platform, suffix in _TELEMETRY_UNIQUE_IDS
        }

    @property
    def ack(self) -> Ack:
        """The ack of the most recent document: rejected, or the plan's."""
        if self._rejected is not None:
            return self._rejected
        return self.planner.ack(self.plan.intent_id if self.plan else None)

    @property
    def wanted(self) -> State | None:
        """The state the plan puts the heater in now."""
        return self.planner.wanted_state(dt_util.utcnow())

    @property
    def raise_state(self) -> RaiseState | None:
        """The surplus raise in force, if any."""
        return self.planner.raise_state

    @property
    def last_write(self) -> Write | None:
        """The last list write, sent or simulated."""
        return self.planner.last_write

    @property
    def reports(self) -> dict[str, Report]:
        """People's changes being reported."""
        return self.planner.reports

    def program_details(self) -> dict[str, Any]:
        """The program list, its hash, and the device's hash."""
        observed = self.observe()
        program = self.planner.program(observed)
        entries: list[dict[str, Any]] = []
        for entry, is_owner in self.planner.others(observed):
            # While live the owner's entries are switched off by their own
            # flag (section 5.1); anyone else's are kept as read.
            entries.append(
                {
                    **entry,
                    "enable": 1 if is_owner else entry["enable"],
                    "owner": "owner" if is_owner else "foreign",
                }
            )
        for owned in self.planner.owned:
            entries.append(
                {
                    **owned.as_entry(),
                    "owner": OWNER_LABELS.get(owned.kind, owned.kind),
                    "serves": owned.serves,
                    "fires_at": owned.fires_at.isoformat(),
                    "mode_name": owned.mode,
                }
            )
        return {
            "hash": schedule_hash(program),
            "entry_count": len(program["reservation"]),
            "entries": entries,
            "device_hash": self._device_hash,
            "read_at": self._device_hash_seen_at,
        }

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `listener` whenever the reported state changes."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- observation -------------------------------------------------------

    def observe(self) -> Observed:
        """A snapshot of what the device reports now."""
        device_data = self.coordinator.data.get(self.mac_address) or {}
        return observe(
            device_data.get("status"),
            self.coordinator.reservation_schedules.get(self.mac_address),
            self.coordinator.tou_schedules.get(self.mac_address),
            surplus_on=self._surplus_on(),
        )

    def _surplus_on(self) -> bool | None:
        """Whether the surplus entity says there is surplus (section 5.7)."""
        if not self.surplus_entity_id:
            return None
        state = self.hass.states.get(self.surplus_entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        if self.surplus_entity_id.startswith("binary_sensor."):
            return state.state == STATE_ON
        try:
            return float(state.state) >= self.surplus_threshold_kw
        except ValueError:
            return None

    async def _async_ensure_owner(self, observed: Observed) -> None:
        """Take the provisional owner's program once the device is known."""
        if self.planner.owner is not None:
            return
        owner = OwnerProgram.from_observed(observed)
        if owner is None:
            return
        self.planner.owner = owner
        await self.store.async_set_owner(self.mac_address, owner.as_document())
        _LOGGER.info(
            "Provisional owner's program for %s: %s at %d half-degrees, "
            "%d entr%s, reservations %s",
            self.mac_address,
            owner.mode,
            owner.setpoint_raw,
            len(owner.entries),
            "y" if len(owner.entries) == 1 else "ies",
            "on" if owner.reservations_enabled else "off",
        )

    def _track_device_hash(self, observed: Observed, now: datetime) -> None:
        schedule = observed.schedule
        device_hash = schedule_hash(schedule) if schedule is not None else None
        if device_hash != self._device_hash:
            self._device_hash = device_hash
            self._device_hash_seen_at = now

    # -- evaluation --------------------------------------------------------

    async def _async_evaluate(self, now: datetime) -> None:
        """One planning pass, the write it needs, and persistence."""
        observed = self.observe()
        self._track_device_hash(observed, now)
        await self._async_ensure_owner(observed)
        self.planner.capabilities = self._build_capabilities()
        if self.mode != CONTROL_MODE_DISABLED:
            write = self.planner.step(now, observed)
            if write is not None:
                self._commit(write)
        self._schedule_next_event()
        await self._async_persist()
        self._notify()

    def _commit(self, write: Write) -> None:
        """Commit a write. In shadow it is simulated, never sent."""
        self.planner.commit(write)
        _LOGGER.debug(
            "%s write for %s (%s): +%d -%d entries",
            "Simulated" if write.simulated else "Confirmed",
            self.mac_address,
            write.reason,
            len(write.added),
            len(write.removed),
        )

    async def _async_persist(self) -> None:
        await self.store.async_set_engine(
            self.mac_address, self.planner.as_document()
        )

    @callback
    def _on_coordinator_update(self) -> None:
        self.hass.async_create_task(self._async_evaluate(dt_util.utcnow()))

    @callback
    def _on_surplus_event(self, event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self._async_evaluate(dt_util.utcnow()))

    def _schedule_next_event(self) -> None:
        """Wake at the next moment the plan changes on its own."""
        if self._cancel_event is not None:
            self._cancel_event()
            self._cancel_event = None
        when = self.planner.next_event_at
        if when is None or self.mode == CONTROL_MODE_DISABLED:
            return
        self._cancel_event = async_track_point_in_utc_time(
            self.hass, self._on_planned_event, when + timedelta(seconds=1)
        )

    async def _on_planned_event(self, now: datetime) -> None:
        self._cancel_event = None
        await self._async_evaluate(now)

    async def _on_heartbeat(self, now: datetime) -> None:
        self.heartbeat = now
        await self._async_evaluate(now)

    # -- intake ------------------------------------------------------------

    def _parse(self, document: Mapping[str, Any]) -> Plan:
        """Parse and check a document; raises `IntentRejected`."""
        plan = parse_plan(document)
        check_plan(plan, self.capabilities)
        return plan

    async def _async_adopt_initial(
        self, now: datetime, observed: Observed
    ) -> None:
        """Take the newer of the stored plan and the entity's document."""
        stored = self.store.stored_intent(self.mac_address)
        stored_plan: Plan | None = None
        if stored is not None:
            try:
                stored_plan = self._parse(stored["document"])
            except IntentRejected as err:
                _LOGGER.info(
                    "Stored plan for %s is no longer usable (%s); dropping it",
                    self.mac_address,
                    err.reason,
                )
                await self.store.async_clear_intent(self.mac_address)

        entity_document: dict[str, Any] | None = None
        state = self.hass.states.get(self.intent_entity_id or "")
        if state is not None and state.state not in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            entity_document = document_from_attributes(state.attributes)

        if stored_plan is not None and stored is not None:
            received_at = dt_util.parse_datetime(stored["received_at"]) or now
            self._adopt(stored_plan, received_at, now, observed, restoring=True)
        if entity_document is not None and (
            stored_plan is None
            or entity_document.get("intent_id") != stored_plan.intent_id
        ):
            await self.async_receive(entity_document, now)

    @callback
    def _on_intent_event(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            # The source went away. The plan in force stays in force.
            return
        old_state = event.data["old_state"]
        if old_state is not None and old_state.state == new_state.state:
            # The protocol requires the state to change on every new plan;
            # an attribute-only change is not a new plan.
            return
        document = document_from_attributes(new_state.attributes)
        self.hass.async_create_task(
            self.async_receive(document, dt_util.utcnow())
        )

    async def async_receive(
        self, document: Mapping[str, Any], now: datetime
    ) -> bool:
        """Validate a document and, if accepted, make it the plan."""
        raw_id = document.get("intent_id")
        intent_id = raw_id if isinstance(raw_id, str) else None
        try:
            plan = self._parse(document)
            if self.plan is not None and plan.issued_at < self.plan.issued_at:
                raise IntentRejected(
                    REASON_SUPERSEDED,
                    f"issued_at {plan.issued_at.isoformat()} is earlier than "
                    f"the plan in force ({self.plan.intent_id})",
                )
        except IntentRejected as err:
            _LOGGER.warning(
                "Plan %s for %s rejected: %s",
                intent_id or "<no id>",
                self.mac_address,
                err,
            )
            self._rejected = rejected_ack(intent_id, err.reason, err.detail)
            self._notify()
            return False

        self._adopt(plan, now, now, self.observe())
        await self.store.async_set_intent(
            self.mac_address, plan.as_document(), now.isoformat()
        )
        await self._async_evaluate(now)
        return True

    def _adopt(
        self,
        plan: Plan,
        received_at: datetime,
        now: datetime,
        observed: Observed,
        *,
        restoring: bool = False,
    ) -> None:
        self.plan = plan
        self.received_at = received_at
        self._rejected = None
        self.planner.capabilities = self._build_capabilities()
        self.planner.set_plan(plan, now, observed, restoring=restoring)
        _LOGGER.debug(
            "Plan %s for %s adopted: %d segment(s), %d grant(s)",
            plan.intent_id,
            self.mac_address,
            len(plan.segments),
            len(plan.grants),
        )
