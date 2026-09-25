"""The controller for one heater: intake, the planner's clock, and storage.

Spec sections 2, 3, 5 and 6 of issue #158. The planning is in `engine.py`;
this module feeds it what the device reports, keeps time for it, commits the
writes it asks for, and persists what it must remember across a restart.

In shadow each write the planner asks for is committed as simulated and
never sent. In live (delivery step 5) every write goes through one
`ListWriter`: the list is read first, written whole, and committed only once
the device holds it (section 5.4). Live is gated by `CONTROL_LIVE_AVAILABLE`
and needs a declared owner's program; without either it runs as shadow.
"""

from __future__ import annotations

import asyncio
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
    CONF_CONTROL_LIVE_SEGMENTS,
    CONF_CONTROL_MODE,
    CONF_CONTROL_OWNER_PROGRAM,
    CONF_CONTROL_SURPLUS_ENTITY,
    CONF_CONTROL_SURPLUS_THRESHOLD_KW,
    CONF_SCAN_INTERVAL,
    CONTROL_LIVE_AVAILABLE,
    CONTROL_MODE_DISABLED,
    CONTROL_MODE_LIVE,
    CONTROL_MODE_SHADOW,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_SURPLUS_THRESHOLD_KW,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .capabilities import Capabilities, build_capabilities
from .engine import WRITE_DISABLE, Planner, RaiseState, Report, State, Write
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
from .writer import CoordinatorWriter, ListWriter

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from nwp500 import Device  # type: ignore[attr-defined]

    from ..coordinator import NWP500ConfigEntry, NWP500DataUpdateCoordinator
    from .store import ControlStore

_LOGGER = logging.getLogger(__name__)

# The heartbeat entity must move at least every 15 minutes (spec section
# 5.12). Ticking at a third of that leaves room for a missed tick.
HEARTBEAT_INTERVAL = timedelta(minutes=5)

_PRECEDENCE_MODES = ("vacation", "power_off")

# An unconfirmed live write is retried once after this (section 5.4).
WRITE_RETRY = timedelta(seconds=60)
# After the retry fails too, writing waits this long before trying again.
WRITE_PAUSE = timedelta(minutes=15)
# How long disabling waits for the heater to report the owner's state.
STATE_CONFIRM_TIMEOUT = 60.0
STATE_CONFIRM_POLL = 2.0


def live_writes(options: Mapping[str, Any], mac_address: str) -> bool:
    """Whether these options write the list to this heater.

    Live, with the gate open, the owner's program declared, and segments
    live. Anything else writes nothing (grants follow segments).
    """
    return (
        CONTROL_LIVE_AVAILABLE
        and options.get(CONF_CONTROL_MODE) == CONTROL_MODE_LIVE
        and options.get(CONF_CONTROL_LIVE_SEGMENTS, False) is True
        and declared_owner(options, mac_address) is not None
    )


def declared_owner(
    options: Mapping[str, Any], mac_address: str
) -> OwnerProgram | None:
    """The owner's program declared in the options for going live (6.3)."""
    declared = options.get(CONF_CONTROL_OWNER_PROGRAM)
    if not isinstance(declared, Mapping):
        return None
    document = declared.get(mac_address)
    if not isinstance(document, Mapping):
        return None
    program = OwnerProgram.from_document(document)
    return replace(program, declared=True) if program is not None else None


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
        writer: ListWriter | None = None,
    ) -> None:
        """Bind to a device. Nothing listens until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.mac_address = mac_address
        self.device = device
        self.store = store
        self.writer: ListWriter = writer or CoordinatorWriter(
            coordinator, mac_address
        )

        options = entry.options
        self.declared_owner = declared_owner(options, mac_address)
        mode = str(options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE))
        if mode == CONTROL_MODE_LIVE and not CONTROL_LIVE_AVAILABLE:
            # Nothing can select it yet; an options file edited by hand is
            # run in shadow rather than trusted to write.
            _LOGGER.warning(
                "External control: live mode is switched off in this build "
                "(CONTROL_LIVE_AVAILABLE); running in shadow"
            )
            mode = CONTROL_MODE_SHADOW
        elif mode == CONTROL_MODE_LIVE and self.declared_owner is None:
            _LOGGER.warning(
                "External control: live mode needs the owner's program "
                "declared in the options (going live); running in shadow"
            )
            mode = CONTROL_MODE_SHADOW
        self.mode = mode
        # Live for segments; grants follow their own switch in the planner.
        self.writes = live_writes(options, mac_address)
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
            shadow=not self.writes,
            explain_window=timedelta(seconds=poll + 60),
        )

        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._cancel_event: CALLBACK_TYPE | None = None
        # One evaluation at a time: a live write awaits the device, and
        # changes arriving meanwhile are folded into the next pass.
        self._lock = asyncio.Lock()
        self._again = False
        self._failures = 0
        self._paused_until: datetime | None = None
        self._cancel_retry: CALLBACK_TYPE | None = None
        self._release_attempts = 0
        # Stopped: no pass runs and no timer is armed. Released: the heater
        # was handed back ahead of a reload; nothing is written after it.
        self._stopped = False
        self._released = False

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
                # State from an earlier version of the feature.
                if self.store.took_over(self.mac_address):
                    _LOGGER.error(
                        "Unreadable stored control state for %s while the "
                        "heater may hold the feature's entries. Disabling "
                        "keeps any it can no longer recognise; check the "
                        "reservation list afterwards",
                        self.mac_address,
                    )
                else:
                    _LOGGER.info(
                        "Discarding unreadable stored control state for %s",
                        self.mac_address,
                    )
                self.planner = Planner(
                    self.planner.capabilities,
                    self.planner.tz,
                    shadow=not self.writes,
                    explain_window=self.planner.explain_window,
                )
        if self.declared_owner is not None:
            self.planner.owner = self.declared_owner
            await self.store.async_set_owner(
                self.mac_address, self.declared_owner.as_document()
            )
        elif owner_state := self.store.stored_owner(self.mac_address):
            self.planner.owner = OwnerProgram.from_document(owner_state)
        observed = self.observe()
        await self._async_ensure_owner(observed)

        if self.mode == CONTROL_MODE_DISABLED:
            await self._async_disable(now)
        else:
            if self.holds_device and not self.writes:
                _LOGGER.warning(
                    "The heater %s still holds the feature's reservation "
                    "list, but live writes are off. Press Disable to hand it "
                    "back to the owner's program",
                    self.mac_address,
                )
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
        """Stop listening. Writes nothing (spec section 6.4).

        A pass already writing is waited for, so its outcome is recorded
        before a new controller reads the same store, and no timer is armed
        after the stop.
        """
        self._stopped = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        self._cancel_timers()
        async with self._lock:
            pass
        self._cancel_timers()

    def _cancel_timers(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event()
            self._cancel_event = None
        self._cancel_retry_timer()

    def _cancel_retry_timer(self) -> None:
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None

    async def _async_feature_version(self) -> str:
        try:
            integration = await async_get_integration(self.hass, DOMAIN)
        except Exception:  # noqa: BLE001 - the version is informational
            return "unknown"
        return str(integration.version or "unknown")

    @property
    def holds_device(self) -> bool:
        """Whether the heater may hold the feature's list.

        The store's flag is set before any live write is sent, since a write
        can land without being confirmed; the planner's once one is.
        Handing back is idempotent, so a flag set for a write that never
        landed costs one list read and the owner's state written again.

        Handing back is not gated by `CONTROL_LIVE_AVAILABLE`: a heater left
        holding the feature's list (after a trial with the gate open) must
        always be returnable to the owner's program.
        """
        return self.planner.took_over or self.store.took_over(self.mac_address)

    async def _async_disable(self, now: datetime) -> None:
        """Section 6.6, once per entry into `disabled`."""
        await self.store.async_clear_intent(self.mac_address)
        if self.store.disabled_done(self.mac_address):
            return
        async with self._lock:
            holds = self.holds_device
            released = await self._async_release(now) if holds else True
        if holds:
            if not released:
                self._retry_release(now)
                await self._async_persist()
                return
            await self.store.async_set_disabled_done(self.mac_address, True)
            await self._async_persist()
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

    async def async_release(self, now: datetime) -> bool:
        """Hand the heater back ahead of a reload or switch-off (6.6).

        Nothing is written after it: plans that arrive meanwhile are not
        adopted, and passes no longer plan.
        """
        async with self._lock:
            self._released = True
            if not self.holds_device:
                return True
            return await self._async_release(now)

    async def _async_release(self, now: datetime) -> bool:
        """Section 6.6 in live: the owner's list, then the owner's state.

        True once the device confirmed the owner's list. The caller holds
        the lock.
        """
        owner = self.planner.owner
        if owner is None:
            _LOGGER.error(
                "Cannot restore the owner's program on %s: none is known",
                self.mac_address,
            )
            return False
        async with self.writer.locked():
            if await self._async_fresh_read() is None:
                self._record_failed_disable(now)
                return False
            observed = self.observe()
            # A write that landed unconfirmed left entries that are the
            # feature's; they must be removed, not kept as someone else's.
            self.planner.reconcile_unconfirmed(observed)
            schedule = owner.restore(self.planner.others(observed))
            if not await self._async_write_list(schedule, observed):
                self._record_failed_disable(now)
                return False
        mode, setpoint_raw = owner.state_now(now, self.planner.tz)
        # Vacation and power-off take precedence (section 6.6), whether the
        # heater is in one now or the owner's own entry set it. An
        # Anti-Legionella cycle does not: the owner's state is written.
        state_written = observed.mode not in _PRECEDENCE_MODES and (
            mode not in _PRECEDENCE_MODES
        )
        confirmed = True
        if state_written:
            try:
                sent = await self.writer.async_restore_state(mode, setpoint_raw)
                confirmed = sent and await self._async_confirm_state(
                    mode, setpoint_raw
                )
            except Exception as err:  # noqa: BLE001 - reported, not raised
                _LOGGER.warning(
                    "Restoring the owner's state on %s failed: %s",
                    self.mac_address,
                    err,
                )
                confirmed = False
        else:
            _LOGGER.info(
                "The heater %s is in, or its owner's program sets, vacation "
                "or power-off; the owner's state is not written",
                self.mac_address,
            )
        write = self.planner.disable(
            now,
            simulated=False,
            confirmed=confirmed,
            state_written=state_written,
        )
        await self.store.async_set_took_over(self.mac_address, False)
        _LOGGER.info(
            "External control handed %s back to the owner's program: %d "
            "entr%s removed%s",
            self.mac_address,
            len(write.removed),
            "y" if len(write.removed) == 1 else "ies",
            "" if confirmed else "; the owner's state was not confirmed",
        )
        return True

    async def _async_confirm_state(self, mode: str, setpoint_raw: int) -> bool:
        """Read back disabling's direct write (section 6.6, step 4).

        Asks the heater for its status and waits for it to report the
        owner's mode and setpoint.
        """
        deadline = asyncio.get_running_loop().time() + STATE_CONFIRM_TIMEOUT
        await self.writer.async_request_status()
        while True:
            observed = self.observe()
            if observed.mode == mode and observed.setpoint_raw == setpoint_raw:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                _LOGGER.warning(
                    "The heater %s reports %s at %s half-degrees, not the "
                    "owner's %s at %d",
                    self.mac_address,
                    observed.mode,
                    observed.setpoint_raw,
                    mode,
                    setpoint_raw,
                )
                return False
            await asyncio.sleep(STATE_CONFIRM_POLL)

    def _record_failed_disable(self, now: datetime) -> None:
        self.planner.last_write = Write(
            reason=WRITE_DISABLE,
            at=now,
            added=(),
            removed=tuple(self.planner.owned),
            result=tuple(self.planner.owned),
            simulated=False,
            confirmed=False,
        )
        _LOGGER.error(
            "Could not restore the owner's reservation list on %s; the "
            "feature's entries are still on the heater",
            self.mac_address,
        )

    def _retry_release(self, now: datetime) -> None:
        """Retry a failed disabling once; after that, on the next start."""
        self._release_attempts += 1
        if self._release_attempts > 1 or self._stopped:
            return
        self._cancel_retry_timer()

        async def _retry(when: datetime) -> None:
            self._cancel_retry = None
            await self._async_disable(when)

        self._cancel_retry = async_track_point_in_utc_time(
            self.hass, _retry, now + WRITE_RETRY
        )

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
        """One planning pass, the write it needs, and persistence.

        Passes do not overlap. One asked for while another runs is folded
        into a further pass right after it (section 5.4, coalesced).
        """
        if self._stopped:
            return
        if self._lock.locked():
            self._again = True
            return
        async with self._lock:
            while not self._stopped:
                self._again = False
                await self._async_evaluate_once(now)
                if not self._again:
                    break
                now = dt_util.utcnow()

    async def _async_evaluate_once(self, now: datetime) -> None:
        observed = self.observe()
        self._track_device_hash(observed, now)
        await self._async_ensure_owner(observed)
        self.planner.capabilities = self._build_capabilities()
        if self.mode != CONTROL_MODE_DISABLED and not self._released:
            write = self.planner.step(now, observed)
            if write is not None and write.simulated:
                if self.holds_device:
                    # The heater holds the feature's real entries: a
                    # simulated write must not replace the record of them,
                    # or disabling could not recognise and remove them.
                    _LOGGER.debug(
                        "Not simulating over the live entries on %s",
                        self.mac_address,
                    )
                else:
                    self._commit(write)
            elif write is not None:
                await self._async_write_live(now)
                self._track_device_hash(self.observe(), now)
        if self.planner.took_over:
            await self.store.async_set_took_over(self.mac_address, True)
        if not self._stopped:
            self._schedule_next_event()
        await self._async_persist()
        self._notify()

    async def _async_fresh_read(self) -> dict[str, Any] | None:
        try:
            return await self.writer.async_read()
        except Exception as err:  # noqa: BLE001 - a failed read is reported
            _LOGGER.warning(
                "Reading the reservation list of %s failed: %s",
                self.mac_address,
                err,
            )
            return None

    async def _async_write_list(
        self, schedule: dict[str, Any], observed: Observed
    ) -> bool:
        """Write the whole list; True once the device holds it."""
        current = observed.schedule
        if current is not None and schedule_hash(current) == schedule_hash(
            schedule
        ):
            return True
        try:
            read = await self.writer.async_write(schedule)
        except Exception as err:  # noqa: BLE001 - an unconfirmed write
            _LOGGER.warning(
                "Writing the reservation list of %s failed: %s",
                self.mac_address,
                err,
            )
            return False
        return read is not None and schedule_hash(read) == schedule_hash(
            schedule
        )

    async def _async_write_live(self, now: datetime) -> None:
        """Read first, plan on what was read, write, and confirm (5.4).

        The heater's list is held from the read through the write, so a
        reservation service cannot change it in between.
        """
        if self._paused_until is not None and now < self._paused_until:
            return
        async with self.writer.locked():
            fresh = await self._async_fresh_read()
            observed = self.observe()
            write = self.planner.step(now, observed)
            if write is None:
                return
            schedule = self.planner.program(observed, write.result)
            confirmed = False
            if fresh is not None:
                current = observed.schedule
                if current is None or schedule_hash(current) != schedule_hash(
                    schedule
                ):
                    # From here the heater may hold the feature's list,
                    # confirmed or not: disabling must hand it back.
                    await self.store.async_set_took_over(self.mac_address, True)
                confirmed = await self._async_write_list(schedule, observed)
        if confirmed:
            self._failures = 0
            self._paused_until = None
            self.planner.hold_until = None
            self._cancel_retry_timer()
            self._commit(replace(write, simulated=False, confirmed=True))
            return
        self._failures += 1
        final = self._failures >= 2
        retry_at = now + (WRITE_PAUSE if final else WRITE_RETRY)
        if final:
            self._failures = 0
        self.planner.reject(
            write,
            now,
            retry_at=retry_at,
            final=final,
            written_hash=schedule_hash(schedule) if fresh is not None else None,
        )
        self._paused_until = retry_at
        self.planner.hold_until = retry_at
        _LOGGER.warning(
            "The %s write to %s was not confirmed; %s at %s",
            write.reason,
            self.mac_address,
            "writing pauses until" if final else "retrying",
            retry_at.isoformat(),
        )
        self._cancel_retry_timer()
        if self._stopped:
            return
        self._cancel_retry = async_track_point_in_utc_time(
            self.hass, self._on_retry, retry_at
        )

    async def _on_retry(self, now: datetime) -> None:
        self._cancel_retry = None
        await self._async_evaluate(now)

    def _commit(self, write: Write) -> None:
        """Commit a write: simulated in shadow, confirmed in live."""
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

        # Not while a pass is writing: that write was planned on the plan
        # before, and is committed or rejected against it.
        async with self._lock:
            if self._stopped or self._released:
                return False
            if self.plan is not None and plan.issued_at < self.plan.issued_at:
                # Another document was adopted while this one waited.
                _LOGGER.warning(
                    "Plan %s for %s rejected: superseded while waiting",
                    intent_id or "<no id>",
                    self.mac_address,
                )
                self._rejected = rejected_ack(
                    intent_id, REASON_SUPERSEDED, "superseded while waiting"
                )
                self._notify()
                return False
            now = max(now, dt_util.utcnow())
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
