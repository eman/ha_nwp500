"""The simulator's calibration: nwp500-python's, on the heater's history.

The library fits. This reads what it fits from, the heater's history and
the simulator's inputs over the same span, from one of two sources:

- the heater's own record (`record.py`), once it reaches back further than
  the recorder's history: its statuses for `sim_calibrate.calibrate`, its
  inputs through `ha_client.RecordClient`;
- until then, the recorder: `sim_home_assistant.read_heater_history` for
  `sim_calibrate.calibrate_changes`, and `record_inputs`, through the
  in-process client.

It runs the library in an executor, keeps the parameters the library
returns, per heater, and gives them to the heater's runner in place of the
defaults for its tank's size.

A heater is calibrated once the simulator has run a while, then once a
week, and when asked (the Calibrate simulator button, the
`nwp500.calibrate_simulator` action). Each run after the first hands the
library the kept parameters with `keep_better`: it refits on the history
read, and each group of terms takes the refit only where it predicts the
latest of that history better than the kept values do. A fit made on
earlier history is kept until a later one beats it.

One heater is fitted at a time, across config entries: a fit holds the
history in memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.recorder import get_instance
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from nwp500 import sim_adapters as sa
from nwp500 import sim_calibrate
from nwp500.sim.params import (
    HeaterParams,
    params_from_document,
    params_to_document,
)
from nwp500.sim_home_assistant import (
    Entities,
    HomeAssistantClient,
    HomeAssistantError,
    read_heater_history,
    record_inputs,
)

from ..const import (
    SIMULATOR_CALIBRATION_STORAGE_KEY,
    SIMULATOR_CALIBRATION_STORAGE_VERSION,
)
from .ha_client import InProcessClient, RecordClient

if TYPE_CHECKING:
    from .record import HeaterRecord
    from .runner import SimulatorRunner

_LOGGER = logging.getLogger(__name__)

#: How often a heater is calibrated again, and how long after a failed
#: attempt the next is made.
CALIBRATE_EVERY = timedelta(days=7)
RETRY_AFTER = timedelta(days=1)
#: How long the simulator runs before a heater's first calibration, so it
#: is not made while Home Assistant starts.
FIRST_AFTER = timedelta(minutes=10)
#: How often whether one is due is checked.
CHECK_EVERY = timedelta(hours=1)
#: The most history read: the recorder's retention, up to this. The
#: library's own guide puts 60 days at about 180 MB.
MAX_HISTORY = timedelta(days=60)

# One fit at a time, whichever heater or config entry it is for.
_FITTING = asyncio.Lock()


class CalibrationError(Exception):
    """Why a heater could not be calibrated, in words."""


def _when(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


class SimulatorCalibration:
    """The calibration of each heater one config entry's simulator runs for.

    Its store holds, per heater, the parameters of its last calibration
    and the library's report on them, and the last attempt, with why it
    failed if it did.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Set up; nothing is read until `async_load`."""
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass,
            SIMULATOR_CALIBRATION_STORAGE_VERSION,
            f"{SIMULATOR_CALIBRATION_STORAGE_KEY}.{entry_id}",
        )
        self._records: dict[str, dict[str, Any]] = {}
        self._params: dict[str, HeaterParams] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        # Once closed, nothing more is kept: a calibration still running
        # when the simulator stops would otherwise write after its store
        # is removed, or into a reloaded simulator's.
        self._closed = False
        self._saving = asyncio.Lock()

    async def async_load(self) -> None:
        """Read what was kept. Parameters the library can't read are dropped."""
        data = await self._store.async_load() or {}
        records = data.get("heaters")
        self._records = records if isinstance(records, dict) else {}
        for mac_address, record in self._records.items():
            doc = record.get("params")
            if doc is None:
                continue
            try:
                self._params[mac_address] = params_from_document(doc)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "The simulator's calibration of %s is not readable "
                    "and is dropped; it is calibrated again: %s",
                    mac_address,
                    err,
                )
                # Due again as if never calibrated, not a week after it.
                record["params"] = None
                record["attempted_at"] = None
                record["error"] = None

    def params(self, mac_address: str) -> HeaterParams | None:
        """The heater's calibrated parameters; None for the defaults."""
        return self._params.get(mac_address)

    def usable(
        self, runner: SimulatorRunner, *, from_defaults: bool = False
    ) -> HeaterParams | None:
        """The heater's kept parameters while they are for its tank's size.

        None without any, or for another size: the runner then walks on the
        defaults, and the next calibration starts from them.
        """
        kept = None if from_defaults else self._params.get(runner.mac_address)
        if kept is None:
            return None
        feature = runner.coordinator.device_features.get(runner.mac_address)
        if feature is not None and not same_tank(kept, sa.params(feature)):
            return None
        return kept

    def record(self, mac_address: str) -> dict[str, Any] | None:
        """What is kept of the heater's calibration and its last attempt."""
        return self._records.get(mac_address)

    def running(self, mac_address: str) -> bool:
        """Whether the heater is being calibrated now."""
        lock = self._locks.get(mac_address)
        return lock is not None and lock.locked()

    def due(self, mac_address: str, now: datetime, started: datetime) -> bool:
        """Whether the heater is due a calibration at `now`."""
        if self.running(mac_address):
            return False
        record = self._records.get(mac_address) or {}
        attempted = _when(record.get("attempted_at"))
        if attempted is None:
            return now - started >= FIRST_AFTER
        wait = RETRY_AFTER if record.get("error") else CALIBRATE_EVERY
        return now - attempted >= wait

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `listener` whenever a calibration is kept or fails."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    async def async_close(self) -> None:
        """Keep nothing more, once a save under way has finished."""
        self._closed = True
        async with self._saving:
            pass

    async def async_calibrate(
        self,
        runner: SimulatorRunner,
        *,
        heater_record: HeaterRecord | None = None,
        from_defaults: bool = False,
    ) -> dict[str, Any]:
        """Calibrate the heater, keep the result, and return what is kept.

        From the kept parameters with the library's `keep_better`, or from
        the defaults for the tank's size with `from_defaults` or when none
        are kept. On the heater's own record (`heater_record`) once it
        reaches back further than the recorder's history, else on the
        recorder's. Raises `CalibrationError` saying why it could not; the
        parameters kept before stay.
        """
        mac_address = runner.mac_address
        lock = self._locks.setdefault(mac_address, asyncio.Lock())
        if lock.locked():
            raise CalibrationError("a calibration is already running")
        async with lock:
            now = dt_util.utcnow()
            try:
                async with _FITTING:
                    record = await self._async_run(
                        runner, now, from_defaults, heater_record
                    )
                self._check_open()
            except CalibrationError as err:
                self._failed(mac_address, now, str(err))
                raise
            except Exception as err:
                _LOGGER.exception(
                    "The simulator's calibration of %s failed", mac_address
                )
                self._failed(mac_address, now, type(err).__name__)
                raise CalibrationError(
                    f"the calibration failed ({type(err).__name__}); "
                    "the log says why"
                ) from err
            self._records[mac_address] = record
            self._params[mac_address] = params_from_document(record["params"])
            await self._async_save()
            self._notify()
            return record

    async def _async_run(
        self,
        runner: SimulatorRunner,
        now: datetime,
        from_defaults: bool,
        heater_record: HeaterRecord | None,
    ) -> dict[str, Any]:
        heater = runner.heater_entities()
        if heater is None:
            raise CalibrationError(
                "the heater's Last status, Tank upper temperature and Tank "
                "lower temperature entities are needed, enabled"
            )
        self._check_open()
        kept = self.usable(runner, from_defaults=from_defaults)
        if kept is not None:
            base = kept
        else:
            feature = runner.coordinator.device_features.get(runner.mac_address)
            if feature is None:
                raise CalibrationError(
                    "the heater's tank size isn't known yet: it hasn't sent "
                    "its device information"
                )
            base = sa.params(feature)
        start = self._recorder_start(now)
        if heater_record is not None:
            # What arrived since the last write is read too.
            await heater_record.async_flush(now)
        since = None if heater_record is None else heater_record.since
        # The record, where it reaches back further than the recorder.
        record_from = (
            max(since, now - MAX_HISTORY)
            if since is not None and (start is None or since < start)
            else None
        )
        entities = runner.entities
        inputs = Entities(
            air=entities.air,
            humidity=entities.humidity,
            demand=entities.demand,
            circulation_speed=entities.circulation_speed,
        )
        calibrate: Callable[[], sim_calibrate.Calibrated]
        try:
            if heater_record is not None and record_from is not None:
                start = record_from
                client = cast(
                    "HomeAssistantClient",
                    RecordClient(self.hass, heater_record),
                )
                record = await record_inputs(client, inputs, start, now)
                calibrate = partial(
                    sim_calibrate.calibrate,
                    heater_record.readings(start),
                    record=record,
                    params=base,
                    keep_better=kept is not None,
                )
            elif start is None:
                raise CalibrationError(
                    "Home Assistant's recorder isn't running"
                )
            else:
                client = cast("HomeAssistantClient", InProcessClient(self.hass))
                history = await read_heater_history(client, heater, start, now)
                record = await record_inputs(client, inputs, start, now)
                calibrate = partial(
                    sim_calibrate.calibrate_changes,
                    history.rows,
                    history.observed,
                    record=record,
                    notes=history.notes,
                    params=base,
                    keep_better=kept is not None,
                )
        except HomeAssistantError as err:
            # The library's reader says which entity, and why.
            raise CalibrationError(str(err)) from err
        # Stopped while it read: no fit for a simulator that is gone.
        self._check_open()
        try:
            future = self.hass.async_add_executor_job(calibrate)
            try:
                done = await asyncio.shield(future)
            except asyncio.CancelledError:
                # The thread fits on whatever happens here: hold the
                # one-fit lock until it ends, then drop its result.
                with contextlib.suppress(Exception):
                    await future
                raise
        except sim_calibrate.CalibrationInputError as err:
            raise CalibrationError(str(err)) from err
        return {
            "params": params_to_document(done.calibration.params),
            "calibrated_at": now.isoformat(),
            "attempted_at": now.isoformat(),
            "error": None,
            "source": "recorder" if record_from is None else "record",
            "history_from": start.isoformat(),
            "history_to": now.isoformat(),
            "refined": kept is not None,
            "terms": [
                dataclasses.asdict(term) for term in sim_calibrate.report(done)
            ],
            "judged": [dataclasses.asdict(judged) for judged in done.judged],
            "notes": list(done.notes),
        }

    def _recorder_start(self, now: datetime) -> datetime | None:
        """Where the recorder's history begins; None without a recorder.

        Its retention, up to `MAX_HISTORY`, or that with no purge.
        """
        if "recorder" not in self.hass.config.components:
            return None
        recorder = get_instance(self.hass)
        keep_days = getattr(recorder, "keep_days", None)
        span = (
            MAX_HISTORY
            if not getattr(recorder, "auto_purge", True) or not keep_days
            else min(MAX_HISTORY, timedelta(days=keep_days))
        )
        return now - span

    def _check_open(self) -> None:
        if self._closed:
            raise CalibrationError("the simulator stopped while it calibrated")

    def _failed(self, mac_address: str, now: datetime, error: str) -> None:
        if self._closed:
            return
        record = dict(self._records.get(mac_address) or {})
        record["attempted_at"] = now.isoformat()
        record["error"] = error
        self._records[mac_address] = record
        self.hass.async_create_task(self._async_save())
        self._notify()

    async def _async_save(self) -> None:
        async with self._saving:
            if not self._closed:
                await self._store.async_save({"heaters": self._records})

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


def same_tank(params: HeaterParams, defaults: HeaterParams) -> bool:
    """Whether `params` are for the tank the defaults are: its volume."""
    return params.tank.volume_gal == defaults.tank.volume_gal


__all__ = [
    "CALIBRATE_EVERY",
    "CHECK_EVERY",
    "FIRST_AFTER",
    "CalibrationError",
    "SimulatorCalibration",
]
