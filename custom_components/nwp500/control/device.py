"""The controller for one heater: intake, storage, staleness, heartbeat.

Spec sections 2.1, 3, 5.4 (staleness only, for now) and 5.12 of issue
#158. Execution of an accepted intent, the restore that follows its end,
and the override tracking arrive with shadow execution (delivery step 3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
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
    CONTROL_MODE_DISABLED,
    DEFAULT_CONTROL_MODE,
    DOMAIN,
)
from .capabilities import Capabilities, build_capabilities
from .evaluate import NO_ACK, Ack, evaluate_intent, rejected_ack
from .intent import (
    Intent,
    IntentRejected,
    document_from_attributes,
    parse_intent,
)

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

        self.intent: Intent | None = None
        self.received_at: datetime | None = None
        self.ack: Ack = NO_ACK
        self.heartbeat: datetime | None = None

        self._feature_version = "unknown"
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._cancel_stale: CALLBACK_TYPE | None = None

    # -- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Adopt the current intent and start listening."""
        self._feature_version = await self._async_feature_version()
        now = dt_util.utcnow()
        self.heartbeat = now

        if self.mode != CONTROL_MODE_DISABLED and self.intent_entity_id:
            self._unsubscribe.append(
                async_track_state_change_event(
                    self.hass, [self.intent_entity_id], self._on_intent_event
                )
            )
            await self._async_adopt_initial(now)
        else:
            # Disabled: nothing is read and nothing will be written. The
            # one-off revert on entering this mode arrives with step 3.
            await self.store.async_clear_intent(self.mac_address)

        self._unsubscribe.append(
            async_track_time_interval(
                self.hass, self._on_heartbeat, HEARTBEAT_INTERVAL
            )
        )
        # Feature data arriving after set-up changes the declared bounds,
        # and so the capability version.
        self._unsubscribe.append(
            self.coordinator.async_add_listener(self._notify)
        )
        self._notify()

    async def async_stop(self) -> None:
        """Stop listening. Writes nothing: a restore is itself a write."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        if self._cancel_stale is not None:
            self._cancel_stale()
            self._cancel_stale = None

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
        return build_capabilities(
            self.entry.options,
            features=self.coordinator.device_features.get(self.mac_address),
            feature_version=self._feature_version,
            telemetry=self._telemetry_entity_ids(),
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

    # -- intake ------------------------------------------------------------

    async def _async_adopt_initial(self, now: datetime) -> None:
        """Take the intent entity's current document, else the stored one.

        The entity is preferred: a retained MQTT topic or a template holds
        the scheduler's latest word. The stored document is the fallback
        for a source that is unavailable after a restart.
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
            await self.async_receive(document, now, received_at=received_at)
            return

        if stored is None:
            return
        try:
            intent = parse_intent(stored["document"], now=now)
        except IntentRejected as err:
            _LOGGER.info(
                "Stored intent for %s is no longer usable (%s); dropping it",
                self.mac_address,
                err.reason,
            )
            await self.store.async_clear_intent(self.mac_address)
            return
        self._adopt(
            intent,
            dt_util.parse_datetime(stored["received_at"]) or now,
            now,
        )

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
    ) -> None:
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
            self.ack = rejected_ack(intent_id, err.reason, err.detail)
            self._notify()
            return

        self._adopt(intent, received_at or now, now)
        await self.store.async_set_intent(
            self.mac_address,
            intent.as_document(),
            (self.received_at or now).isoformat(),
        )

    def _adopt(
        self, intent: Intent, received_at: datetime, now: datetime
    ) -> None:
        self.intent = intent
        self.received_at = received_at
        self.ack = evaluate_intent(intent, self.capabilities, now=now)
        _LOGGER.debug(
            "Intent %s for %s accepted: %s (%d directive(s))",
            intent.intent_id,
            self.mac_address,
            self.ack.state,
            len(intent.directives),
        )
        self._schedule_stale(intent.valid_until)
        self._notify()

    # -- staleness and heartbeat -------------------------------------------

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
        self.ack = NO_ACK
        await self.store.async_clear_intent(self.mac_address)
        self._notify()

    async def _on_heartbeat(self, now: datetime) -> None:
        self.heartbeat = now
        if self.intent is not None and self.intent.is_stale(now):
            await self._on_stale(now)
        self._notify()


ListenerFactory = Callable[[], CALLBACK_TYPE]
