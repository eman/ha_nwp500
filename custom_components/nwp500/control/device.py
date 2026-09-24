"""The controller for one heater: intake, storage, staleness, heartbeat.

Spec sections 2.1, 3, 5 and 5.12 of issue #158. The planning is in
`engine.py`; this module feeds it what the device reports, keeps time for
it, and persists what it must remember across a restart. In shadow mode
the engine's wanted state is reported and nothing is written.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
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
    CONF_CONTROL_BASELINE,
    CONF_CONTROL_INTENT_ENTITY,
    CONF_CONTROL_MODE,
    CONF_CONTROL_SURPLUS_ENTITY,
    CONF_CONTROL_SURPLUS_THRESHOLD_KW,
    CONTROL_MODE_DISABLED,
    CONTROL_MODE_SHADOW,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CONTROL_SURPLUS_THRESHOLD_KW,
    DOMAIN,
)
from .baseline import Baseline
from .capabilities import Capabilities, build_capabilities
from .engine import (
    RESTORE_DISABLED,
    RESTORE_STALE_INTENT,
    RESTORE_STARTUP,
    ControlEngine,
    Override,
    Restore,
    Wanted,
)
from .evaluate import NO_ACK, Ack, evaluate_intent, rejected_ack
from .intent import (
    Intent,
    IntentRejected,
    document_from_attributes,
    parse_intent,
)
from .observed import Observed, observe

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
        self.mode: str = str(
            options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        )
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

        self.intent: Intent | None = None
        self.received_at: datetime | None = None
        self.heartbeat: datetime | None = None
        self._document_ack: Ack = NO_ACK

        self._feature_version = "unknown"
        self._declared_baseline = Baseline.from_document(
            options.get(CONF_CONTROL_BASELINE) or {}
        )
        self.engine = ControlEngine(
            self._build_capabilities(None),
            dt_util.get_default_time_zone(),
            shadow=self.mode == CONTROL_MODE_SHADOW,
        )
        self.engine.baseline = self._declared_baseline

        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._cancel_stale: CALLBACK_TYPE | None = None
        self._cancel_event: CALLBACK_TYPE | None = None

    # -- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Adopt the current intent and start listening."""
        self._feature_version = await self._async_feature_version()
        now = dt_util.utcnow()
        self.heartbeat = now
        self.engine.capabilities = self._build_capabilities(None)
        if engine_state := self.store.stored_engine(self.mac_address):
            self.engine.load_document(engine_state)
        observed = self.observe()
        self._ensure_baseline(observed)

        if self.mode == CONTROL_MODE_DISABLED or not self.intent_entity_id:
            # Disabled: a one-off, unconditional revert to the baseline,
            # then nothing is read and nothing will be written.
            await self.store.async_clear_intent(self.mac_address)
            self.engine.clear_intent(
                RESTORE_DISABLED, now, observed, unconditional=True
            )
        else:
            self._unsubscribe.append(
                async_track_state_change_event(
                    self.hass, [self.intent_entity_id], self._on_intent_event
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
            adopted = await self._async_adopt_initial(now, observed)
            if not adopted:
                self.engine.clear_intent(RESTORE_STARTUP, now, observed)

        self._unsubscribe.append(
            async_track_time_interval(
                self.hass, self._on_heartbeat, HEARTBEAT_INTERVAL
            )
        )
        self._unsubscribe.append(
            self.coordinator.async_add_listener(self._on_coordinator_update)
        )
        self._schedule_next_event()
        await self._async_persist_engine()
        self._notify()

    async def async_stop(self) -> None:
        """Stop listening. Writes nothing: a restore is itself a write."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        for cancel in (self._cancel_stale, self._cancel_event):
            if cancel is not None:
                cancel()
        self._cancel_stale = None
        self._cancel_event = None

    async def _async_feature_version(self) -> str:
        try:
            integration = await async_get_integration(self.hass, DOMAIN)
        except Exception:  # noqa: BLE001 - the version is informational
            return "unknown"
        return str(integration.version or "unknown")

    # -- what the entities read -------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        """The current declaration."""
        capabilities = self._build_capabilities(self.baseline)
        self.engine.capabilities = capabilities
        return capabilities

    @property
    def baseline(self) -> Baseline | None:
        """The declared baseline, else the provisional one, else None."""
        return self.engine.baseline

    @property
    def ack(self) -> Ack:
        """The most recent document's acknowledgement."""
        if self._document_ack.state == "rejected":
            return self._document_ack
        return self.engine.ack

    @property
    def wanted(self) -> Wanted:
        """What the feature wants now."""
        return self.engine.wanted

    @property
    def last_restore(self) -> Restore | None:
        """The last restore to the baseline."""
        return self.engine.last_restore

    @property
    def overrides(self) -> dict[str, Override]:
        """The overrides being honoured, by field."""
        return self.engine.overrides

    def _build_capabilities(self, baseline: Baseline | None) -> Capabilities:
        return build_capabilities(
            self.entry.options,
            features=self.coordinator.device_features.get(self.mac_address),
            feature_version=self._feature_version,
            telemetry=self._telemetry_entity_ids(),
            baseline=baseline.as_attributes() if baseline else None,
        )

    def _telemetry_entity_ids(self) -> dict[str, str | None]:
        registry = er.async_get(self.hass)
        return {
            name: registry.async_get_entity_id(
                platform, DOMAIN, f"{self.mac_address}_{suffix}"
            )
            for name, platform, suffix in _TELEMETRY_UNIQUE_IDS
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
            surplus_on=self._surplus_on(),
        )

    def _surplus_on(self) -> bool | None:
        """Whether the surplus entity says there is surplus (section 5.8)."""
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

    def _ensure_baseline(self, observed: Observed) -> None:
        """Take a provisional baseline once the device has been seen."""
        if self.engine.baseline is not None:
            return
        baseline = Baseline.from_observed(observed)
        if baseline is not None:
            self.engine.set_baseline(baseline, dt_util.utcnow(), observed)
            _LOGGER.info(
                "Provisional baseline for %s: %s at %d half-degrees, TOU %s",
                self.mac_address,
                baseline.mode,
                baseline.setpoint_raw,
                "on" if baseline.tou_enabled else "off",
            )

    @callback
    def _on_coordinator_update(self) -> None:
        observed = self.observe()
        self._ensure_baseline(observed)
        self.engine.evaluate(dt_util.utcnow(), observed)
        self._schedule_next_event()
        self.hass.async_create_task(self._async_persist_engine())
        self._notify()

    @callback
    def _on_surplus_event(self, event: Event[EventStateChangedData]) -> None:
        self._on_coordinator_update()

    async def _async_persist_engine(self) -> None:
        await self.store.async_set_engine(
            self.mac_address, self.engine.as_document()
        )

    # -- intake ------------------------------------------------------------

    async def _async_adopt_initial(
        self, now: datetime, observed: Observed
    ) -> bool:
        """Take the intent entity's current document, else the stored one.

        The entity is preferred: a retained MQTT topic or a template holds
        the scheduler's latest word. The stored document is the fallback
        for a source that is unavailable after a restart. Returns whether
        an intent was adopted.
        """
        stored = self.store.stored_intent(self.mac_address)
        state = self.hass.states.get(self.intent_entity_id or "")
        if state is not None and state.state not in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            document = document_from_attributes(state.attributes)
            received_at = None
            if stored and stored["document"].get("intent_id") == document.get(
                "intent_id"
            ):
                received_at = dt_util.parse_datetime(stored["received_at"])
            return await self.async_receive(
                document, now, received_at=received_at
            )

        if stored is None:
            return False
        try:
            intent = parse_intent(stored["document"], now=now)
        except IntentRejected as err:
            _LOGGER.info(
                "Stored intent for %s is no longer usable (%s); dropping it",
                self.mac_address,
                err.reason,
            )
            await self.store.async_clear_intent(self.mac_address)
            return False
        self._adopt(
            intent,
            dt_util.parse_datetime(stored["received_at"]) or now,
            now,
            observed,
        )
        return True

    @callback
    def _on_intent_event(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            # The source went away. The intent in force stays in force
            # until its own valid_until.
            return
        old_state = event.data["old_state"]
        if old_state is not None and old_state.state == new_state.state:
            # The protocol requires the state to change on every new
            # intent; an attribute-only change is not a new intent.
            return
        document = document_from_attributes(new_state.attributes)
        self.hass.async_create_task(
            self.async_receive(document, dt_util.utcnow())
        )

    async def async_receive(
        self,
        document: Mapping[str, Any],
        now: datetime,
        *,
        received_at: datetime | None = None,
    ) -> bool:
        """Validate a document and, if accepted, make it the intent."""
        raw_id = document.get("intent_id")
        intent_id = raw_id if isinstance(raw_id, str) else None
        try:
            intent = parse_intent(document, now=now)
        except IntentRejected as err:
            _LOGGER.warning(
                "Intent %s for %s rejected: %s",
                intent_id or "<no id>",
                self.mac_address,
                err,
            )
            self._document_ack = rejected_ack(intent_id, err.reason, err.detail)
            self._notify()
            return False

        self._adopt(intent, received_at or now, now, self.observe())
        await self.store.async_set_intent(
            self.mac_address,
            intent.as_document(),
            (self.received_at or now).isoformat(),
        )
        await self._async_persist_engine()
        return True

    def _adopt(
        self,
        intent: Intent,
        received_at: datetime,
        now: datetime,
        observed: Observed,
    ) -> None:
        self.intent = intent
        self.received_at = received_at
        self._document_ack = evaluate_intent(intent, self.capabilities, now=now)
        self.engine.set_intent(intent, self._document_ack, now, observed)
        _LOGGER.debug(
            "Intent %s for %s accepted: %s (%d directive(s))",
            intent.intent_id,
            self.mac_address,
            self.engine.ack.state,
            len(intent.directives),
        )
        self._schedule_stale(intent.valid_until)
        self._schedule_next_event()
        self._notify()

    # -- timing ------------------------------------------------------------

    def _schedule_stale(self, when: datetime) -> None:
        if self._cancel_stale is not None:
            self._cancel_stale()
        self._cancel_stale = async_track_point_in_utc_time(
            self.hass, self._on_stale, when
        )

    async def _on_stale(self, now: datetime) -> None:
        self._cancel_stale = None
        if self.intent is None or not self.intent.is_stale(now):
            return
        _LOGGER.info(
            "Intent %s for %s is stale; the baseline applies",
            self.intent.intent_id,
            self.mac_address,
        )
        self.intent = None
        self.received_at = None
        self._document_ack = NO_ACK
        self.engine.clear_intent(RESTORE_STALE_INTENT, now, self.observe())
        await self.store.async_clear_intent(self.mac_address)
        await self._async_persist_engine()
        self._schedule_next_event()
        self._notify()

    def _schedule_next_event(self) -> None:
        """Wake at the next moment the plan changes on its own."""
        if self._cancel_event is not None:
            self._cancel_event()
            self._cancel_event = None
        when = self.engine.next_event_at
        if when is None or self.mode == CONTROL_MODE_DISABLED:
            return
        self._cancel_event = async_track_point_in_utc_time(
            self.hass, self._on_planned_event, when + timedelta(seconds=1)
        )

    async def _on_planned_event(self, now: datetime) -> None:
        self._cancel_event = None
        self.engine.evaluate(now, self.observe())
        self._schedule_next_event()
        await self._async_persist_engine()
        self._notify()

    async def _on_heartbeat(self, now: datetime) -> None:
        self.heartbeat = now
        if self.intent is not None and self.intent.is_stale(now):
            await self._on_stale(now)
        else:
            self.engine.evaluate(now, self.observe())
            self._schedule_next_event()
            await self._async_persist_engine()
        self._notify()
