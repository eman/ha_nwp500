"""Recording a heater's statuses, for the simulator's calibration.

Each status the heater sends is kept as it was sent, as one line of the
library's history format (`nwp500.status_history`): the instant it arrived
and the raw payload, without the device's address. Lines are held in
memory and appended every 15 minutes, in an executor, to one file per UTC
day under Home Assistant's configuration directory, so they go into its
backups:

    <config>/nwp500/history/<mac>/YYYY-MM-DD.jsonl

A day's file is compressed once the day is over (`.jsonl.gz`), and days
older than the retention are deleted. Nothing is deduplicated: a gap in
the file is a time no status arrived.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from nwp500.models import DeviceStatus
from nwp500.status_history import line as history_line

from ..const import simulator_history_dir

if TYPE_CHECKING:
    from ..coordinator import NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

FLUSH_INTERVAL = timedelta(minutes=15)
# The most lines held while the files cannot be written: a day at one
# status every 30 seconds. Older lines go first.
MAX_BUFFERED = 2880

_SUFFIX = ".jsonl"
_GZ_SUFFIX = ".jsonl.gz"


@dataclass(frozen=True)
class Stored:
    """What is on disk for one heater."""

    days: int = 0
    bytes: int = 0
    first_day: date | None = None
    last_day: date | None = None


def _day_of(name: str) -> date | None:
    for suffix in (_GZ_SUFFIX, _SUFFIX):
        if name.endswith(suffix):
            try:
                return date.fromisoformat(name[: -len(suffix)])
            except ValueError:
                return None
    return None


class UnwrittenError(OSError):
    """An append failed; `lines` were not written, the others were."""

    def __init__(self, err: OSError, lines: list[tuple[date, str]]) -> None:
        super().__init__(*err.args)
        self.lines = lines


def append(directory: str, lines: list[tuple[date, str]]) -> None:
    """Append `lines` to their days' files. Runs in an executor.

    Each day's lines go in whole or not at all: a failed append is cut
    back to the file's size before it, and raises `UnwrittenError` with that
    day's lines and the days after it, so a retry writes each line once.
    """
    if not lines:
        return
    by_day: dict[date, list[str]] = {}
    for line_day, text in lines:
        by_day.setdefault(line_day, []).append(text)
    days = sorted(by_day)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as err:
        raise UnwrittenError(err, lines) from err
    for index, line_day in enumerate(days):
        path = os.path.join(directory, line_day.isoformat() + _SUFFIX)
        unwritten = [
            (day, text) for day in days[index:] for text in by_day[day]
        ]
        try:
            size: int | None = os.path.getsize(path)
        except FileNotFoundError:
            size = None
        except OSError as err:
            raise UnwrittenError(err, unwritten) from err
        try:
            with open(path, "a", encoding="utf-8") as out:
                out.write("".join(text + "\n" for text in by_day[line_day]))
        except OSError as err:
            with contextlib.suppress(OSError):
                if size is None:
                    os.remove(path)
                else:
                    os.truncate(path, size)
            raise UnwrittenError(err, unwritten) from err


def maintain(directory: str, today: date, keep_days: int) -> Stored:
    """Compress the finished days and delete the expired ones.

    Returns what is then stored. Runs in an executor. A failure leaves
    each day's lines in one place or the other, never both, so it can be
    run again.
    """
    if not os.path.isdir(directory):
        return Stored()
    oldest = today - timedelta(days=keep_days - 1)
    for name in sorted(os.listdir(directory)):
        day = _day_of(name)
        if day is None:
            continue
        path = os.path.join(directory, name)
        if day < oldest:
            os.remove(path)
        elif name.endswith(_SUFFIX) and day < today:
            _compress(path)
    return stored(directory)


def _compress(path: str) -> None:
    """Move a finished day's lines into its compressed file.

    The compressed file is rebuilt beside it and swapped in whole, then
    the plain file is removed. A day whose compressed file exists already
    (a late line) keeps it, with the lines as one more gzip member, which
    reads back as one stream.
    """
    target = path[: -len(_SUFFIX)] + _GZ_SUFFIX
    partial = target + ".partial"
    try:
        with open(partial, "wb") as out:
            with (
                contextlib.suppress(FileNotFoundError),
                open(target, "rb") as old,
            ):
                shutil.copyfileobj(old, out)
            with open(path, "rb") as src, gzip.open(out, "wb") as member:
                shutil.copyfileobj(src, member)
        os.replace(partial, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(partial)
        raise
    os.remove(path)


def stored(directory: str) -> Stored:
    """What is on disk in `directory`. Runs in an executor."""
    if not os.path.isdir(directory):
        return Stored()
    days: set[date] = set()
    size = 0
    for name in os.listdir(directory):
        day = _day_of(name)
        if day is None:
            continue
        days.add(day)
        size += os.path.getsize(os.path.join(directory, name))
    return Stored(
        days=len(days),
        bytes=size,
        first_day=min(days) if days else None,
        last_day=max(days) if days else None,
    )


def delete(directory: str) -> None:
    """Delete a heater's recorded history. Runs in an executor."""
    shutil.rmtree(directory, ignore_errors=True)


class HistoryRecorder:
    """Records one heater's statuses."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NWP500DataUpdateCoordinator,
        mac_address: str,
        keep_days: int,
        clock: Callable[[], datetime] = dt_util.utcnow,
    ) -> None:
        """Set up; nothing is recorded until `async_start`."""
        self.hass = hass
        self.coordinator = coordinator
        self.mac_address = mac_address
        self.keep_days = keep_days
        self.directory = simulator_history_dir(
            hass.config.config_dir, mac_address
        )
        self._clock = clock
        self._buffer: list[tuple[date, str]] = []
        self._lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        #: Statuses recorded since the start, and dropped (unwritable).
        self.recorded = 0
        self.dropped = 0
        #: The last write's failure, until a write succeeds.
        self.error: str | None = None
        self.last_written: datetime | None = None
        self.stored = Stored()
        #: The last status's instant, and the longest wait between two.
        self.last_status: datetime | None = None
        self.longest_gap = timedelta(0)

    @callback
    def async_start(self) -> None:
        """Take each status the heater sends; write them every 15 minutes."""
        self._unsubs.append(
            self.coordinator.async_add_raw_status_listener(
                self.mac_address, self._on_status
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_interval, FLUSH_INTERVAL
            )
        )
        # Home Assistant does not unload its entries when it stops: write
        # what is held with its own final writes.
        self._unsubs.append(self._listen_for_final_write())
        # Compress and prune what an earlier run left.
        self.hass.async_create_task(self.async_flush())

    async def async_stop(self) -> None:
        """Stop taking statuses and write what is held."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.async_flush()

    def _listen_for_final_write(self) -> Callable[[], None]:
        fired = False

        async def on_final_write(_event: Event) -> None:
            nonlocal fired
            fired = True
            await self.async_flush()

        remove = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_FINAL_WRITE, on_final_write
        )

        def unsub() -> None:
            # A listener that has fired is gone already.
            if not fired:
                remove()

        return unsub

    @callback
    def _on_status(self, payload: dict[str, Any]) -> None:
        try:
            DeviceStatus.model_validate(payload)
        except ValueError as err:
            _LOGGER.debug("Not recording a status that does not parse: %s", err)
            return
        at = self._clock().astimezone(UTC)
        if self.last_status is not None:
            self.longest_gap = max(self.longest_gap, at - self.last_status)
        self.last_status = at
        # The device's address is not the history's business.
        raw = {k: v for k, v in payload.items() if k != "macAddress"}
        self._buffer.append((at.date(), history_line(at, raw)))
        self.recorded += 1
        self._bound()

    def _bound(self) -> None:
        """Drop the oldest lines held beyond `MAX_BUFFERED`, counting them."""
        if len(self._buffer) > MAX_BUFFERED:
            excess = len(self._buffer) - MAX_BUFFERED
            del self._buffer[:excess]
            self.dropped += excess

    @callback
    def _on_interval(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_flush())

    async def async_flush(self) -> None:
        """Write the lines held, then compress and prune."""
        async with self._lock:
            lines, self._buffer = self._buffer, []
            today = self._clock().astimezone(UTC).date()
            try:
                await self.hass.async_add_executor_job(
                    append, self.directory, lines
                )
            except UnwrittenError as err:
                # Kept for the next try, in their order before the lines
                # that arrived meanwhile, within the bound.
                self._buffer = err.lines + self._buffer
                self._bound()
                self._failed(err)
                return
            if lines:
                self.last_written = self._clock()
            try:
                self.stored = await self.hass.async_add_executor_job(
                    maintain, self.directory, today, self.keep_days
                )
            except OSError as err:
                # The lines are written; compressing or pruning is tried
                # again with the next write.
                self._failed(err)
                return
            if self.error is not None:
                _LOGGER.info(
                    "The recorded history of %s is written again",
                    self.mac_address,
                )
            self.error = None

    def _failed(self, err: OSError) -> None:
        if self.error is None:
            _LOGGER.warning(
                "Cannot write the recorded history of %s to %s: %s",
                self.mac_address,
                self.directory,
                err,
            )
        self.error = str(err)

    async def async_delete(self) -> None:
        """Delete the history recorded so far, with the lines held."""
        async with self._lock:
            self._buffer = []
            await self.hass.async_add_executor_job(delete, self.directory)
            self.stored = Stored()

    def as_document(self) -> dict[str, Any]:
        """For diagnostics."""
        stored = self.stored
        return {
            "keep_days": self.keep_days,
            "recorded": self.recorded,
            "dropped": self.dropped,
            "held": len(self._buffer),
            "error": self.error,
            "last_status": None
            if self.last_status is None
            else self.last_status.isoformat(),
            "longest_gap_s": self.longest_gap.total_seconds(),
            "last_written": None
            if self.last_written is None
            else self.last_written.isoformat(),
            "days": stored.days,
            "bytes": stored.bytes,
            "first_day": None
            if stored.first_day is None
            else stored.first_day.isoformat(),
            "last_day": None
            if stored.last_day is None
            else stored.last_day.isoformat(),
        }
