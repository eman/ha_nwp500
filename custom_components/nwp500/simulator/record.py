"""The heater's own record, for the simulator's calibration.

Home Assistant's recorder keeps 10 days by default, and the library's fit
wants weeks: its tank fit reads the last 7 (`sim_calibrate.TANK_WEEKS`),
and the heat pump's fits need recoveries and climbs that come a few a
week. So while the simulator runs, each heater's statuses are kept here
too, for up to `KEEP`, without raising the recorder's retention for every
entity Home Assistant has.

Two series, a file of each per UTC day, gzipped, under
``.storage/nwp500/simulator_record/<entry>/<heater>/``
(`const.simulator_record_directory`):

- ``statuses/``: every status the heater sends, as it sent it, one line
  each in the library's history format (`nwp500.status_history.line`, as
  ``nwp-cli simulate record`` writes it). The library reads them back
  (`status_history.iter_read`) and calibrates on them
  (`sim_calibrate.calibrate`).
- ``inputs/``: each change of the simulator's input entities (its air,
  humidity, demand and circulation pump), as the recorder's history holds
  a row: entity, state, unit and when. The library reads them as it reads
  the recorder's (`sim_home_assistant.record_inputs`).

Lines are kept in memory and written every `FLUSH_EVERY`, and when the
simulator stops, each write a gzip member of its own. A day of statuses
is about 0.3 MB.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import zlib
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, State

from nwp500 import status_history
from nwp500.status_history import TimedStatus

from ..const import simulator_record_directory

_LOGGER = logging.getLogger(__name__)

#: How long the record is kept: the most history the calibration reads,
#: and the day it is in.
KEEP = timedelta(days=61)
#: How often what was recorded is written.
FLUSH_EVERY = timedelta(minutes=15)

_STATUSES = "statuses"
_INPUTS = "inputs"
_SUFFIX = ".jsonl.gz"


def _heater_directory(hass: HomeAssistant, entry_id: str, mac: str) -> Path:
    slug = "".join(ch for ch in mac.lower() if ch.isalnum())
    return simulator_record_directory(hass, entry_id) / slug


def _name(path: Path) -> tuple[date, int] | None:
    """The UTC day a file holds and its part, from its name.

    ``2026-10-01.jsonl.gz`` is the day's first part, ``2026-10-01.1.jsonl.gz``
    the next: one is begun after a damaged write.
    """
    day, _, part = path.name.removesuffix(_SUFFIX).partition(".")
    try:
        return date.fromisoformat(day), int(part) if part else 0
    except ValueError:
        return None


def _files(directory: Path) -> list[tuple[date, Path]]:
    """A series' files, by day and part."""
    if not directory.is_dir():
        return []
    found = [
        (name, path)
        for path in directory.glob(f"*{_SUFFIX}")
        if (name := _name(path)) is not None
    ]
    return [(day, path) for (day, _), path in sorted(found)]


def _sound(path: Path) -> bool:
    """Whether the file reads through to its end."""
    try:
        with gzip.open(path, "rb") as f:
            while f.read(1 << 20):
                pass
    except EOFError, OSError, zlib.error:
        return False
    return True


def _lines(path: Path) -> Iterator[str]:
    """A file's lines, up to any damaged write.

    A member cut short (the disk filled, the host stopped mid-write) ends
    the file there.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield from f
    except (EOFError, OSError, zlib.error, UnicodeDecodeError) as err:
        _LOGGER.debug("%s is read up to a damaged write: %s", path, err)


def _state_row(state: State) -> dict[str, Any]:
    """A state as the recorder's history gives it, as far as it is read.

    Its entity, state, unit and instant: what the library reads of a row.
    """
    row: dict[str, Any] = {
        "entity_id": state.entity_id,
        "state": state.state,
        "last_updated": state.last_updated.isoformat(),
    }
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit is not None:
        row["attributes"] = {ATTR_UNIT_OF_MEASUREMENT: unit}
    return row


class HeaterRecord:
    """One heater's record: what is waiting to be written, and its files.

    The methods that read or write the files run in an executor; the
    others in the event loop.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        mac: str,
        inputs: Iterable[str] = (),
    ) -> None:
        """Set up, for the input entities `inputs`.

        Nothing is read until `async_load`.
        """
        self.hass = hass
        self.directory = _heater_directory(hass, entry_id, mac)
        #: The input entities whose changes are kept.
        self.inputs = tuple(inputs)
        #: When the record begins: its first status kept. None while it
        #: has none.
        self.since: datetime | None = None
        self._statuses: list[tuple[datetime, str]] = []
        self._inputs: list[tuple[datetime, str]] = []
        self._seeded: date | None = None
        self._writing = asyncio.Lock()
        # The files this process found whole, or began: written to without
        # checking again. Only `_write` touches it, one at a time.
        self._checked: set[Path] = set()
        # Once closed, nothing more is written: the simulator turned off
        # removes the record, and a late write would bring it back.
        self._closed = False

    async def async_load(self) -> None:
        """Find where the record begins."""
        self.since = await self.hass.async_add_executor_job(self.first)

    def add_status(self, at: datetime, payload: Mapping[str, Any]) -> None:
        """Keep a status the heater sent at `at`, as it sent it."""
        # The heater's address is the record's directory's business, not
        # each line's (as nwp-cli simulate record has it).
        status = {k: v for k, v in payload.items() if k != "macAddress"}
        self._statuses.append((at, status_history.line(at, status)))
        if self.since is None:
            self.since = at

    def add_current_states(self, now: datetime) -> None:
        """Keep the input entities' states now, written in `now`'s day.

        When the record starts, and as each day's file opens: the value in
        force from then on, until a change, once the days before are
        dropped.
        """
        self._seeded = now.astimezone(UTC).date()
        for entity in self.inputs:
            if (state := self.hass.states.get(entity)) is not None:
                self._inputs.append((now, json.dumps(_state_row(state))))

    def add_state(self, state: State) -> None:
        """Keep a change of an input entity."""
        self._inputs.append((state.last_updated, json.dumps(_state_row(state))))

    async def async_flush(self, now: datetime) -> None:
        """Write what is waiting, and drop the days past `KEEP`.

        Each day's inputs file opens with the input entities' states then:
        the value in force when the record's earliest day begins, once the
        days before it are dropped.
        """
        if self._closed:
            return
        if now.astimezone(UTC).date() != self._seeded:
            self.add_current_states(now)
        statuses, self._statuses = self._statuses, []
        inputs, self._inputs = self._inputs, []
        async with self._writing:
            # Closed while an earlier write held it.
            if self.closed():
                return
            self.since = await self.hass.async_add_executor_job(
                self._write, statuses, inputs, now
            )

    def closed(self) -> bool:
        """Whether nothing more is written."""
        return self._closed

    async def async_close(self, now: datetime) -> None:
        """Write what is waiting, then nothing more."""
        await self.async_flush(now)
        self._closed = True

    def _write(
        self,
        statuses: list[tuple[datetime, str]],
        inputs: list[tuple[datetime, str]],
        now: datetime,
    ) -> datetime | None:
        self._append(self.directory / _STATUSES, statuses)
        self._append(self.directory / _INPUTS, inputs)
        oldest = (now - KEEP).astimezone(UTC).date()
        dropped = False
        for name in (_STATUSES, _INPUTS):
            for day, path in _files(self.directory / name):
                if day < oldest:
                    path.unlink(missing_ok=True)
                    dropped = True
        return self.first() if dropped or self.since is None else self.since

    def _append(
        self, directory: Path, lines: Iterable[tuple[datetime, str]]
    ) -> None:
        """Append `lines` to their days' files, a gzip member to each."""
        by_day: dict[date, list[str]] = {}
        for at, text in lines:
            by_day.setdefault(at.astimezone(UTC).date(), []).append(text)
        for day, texts in by_day.items():
            path = None
            try:
                directory.mkdir(parents=True, exist_ok=True)
                path = self._target(directory, day)
                with gzip.open(path, "at", encoding="utf-8") as f:
                    f.write("".join(f"{text}\n" for text in texts))
                self._checked.add(path)
            except OSError as err:
                # The file may end in a damaged member now: checked again
                # before the next write to it.
                if path is not None:
                    self._checked.discard(path)
                _LOGGER.warning(
                    "The simulator's record of the heater is not written: %s",
                    err,
                )

    def _target(self, directory: Path, day: date) -> Path:
        """The file to append `day`'s lines to.

        Its latest part, unless that ends in a damaged write (a crash, a
        full disk): what is appended behind one can't be read, so a new
        part is begun. A part is checked once, the first time this
        process writes to it.
        """
        parts = [path for d, path in _files(directory) if d == day]
        if not parts:
            return directory / f"{day.isoformat()}{_SUFFIX}"
        latest = parts[-1]
        if latest in self._checked or _sound(latest):
            self._checked.add(latest)
            return latest
        _LOGGER.warning(
            "%s ends in a damaged write; the record goes on in a new file",
            latest,
        )
        named = _name(latest)
        part = 1 if named is None else named[1] + 1
        return directory / f"{day.isoformat()}.{part}{_SUFFIX}"

    def first(self) -> datetime | None:
        """The instant of the earliest status kept."""
        for _, path in _files(self.directory / _STATUSES):
            for at, _ in self._read(path):
                return at
        return None

    def readings(self, start: datetime) -> Iterator[TimedStatus]:
        """The statuses kept from `start` on, as the calibration takes them.

        A file at a time, a status at a time: none is held after it is
        taken.
        """
        first_day = start.astimezone(UTC).date()
        for day, path in _files(self.directory / _STATUSES):
            if day < first_day:
                continue
            for at, status in self._read(path):
                if at >= start:
                    yield at, status

    @staticmethod
    def _read(path: Path) -> Iterator[TimedStatus]:
        for text in _lines(path):
            if not text.strip():
                continue
            try:
                yield from status_history.iter_read((text,))
            except ValueError:
                # A line cut short by a damaged write.
                continue

    def history_document(
        self, entities: Iterable[str], start: datetime, end: datetime
    ) -> list[list[dict[str, Any]]]:
        """The input entities' history from `start` to `end`.

        As the recorder's (`/api/history/period`) gives it: each entity's
        state in force at `start`, dated `start`, then its changes.
        """
        names = set(entities)
        rows: dict[str, dict[str, dict[str, Any]]] = {n: {} for n in names}
        for _, path in _files(self.directory / _INPUTS):
            for text in _lines(path):
                try:
                    row = cast(dict[str, Any], json.loads(text))
                    entity = row["entity_id"]
                    stamp = row["last_updated"]
                except ValueError, KeyError, TypeError:
                    continue
                if entity in rows:
                    rows[entity][stamp] = row
        doc: list[list[dict[str, Any]]] = []
        for entity in sorted(names):
            series = sorted(
                rows[entity].values(),
                key=lambda r: datetime.fromisoformat(r["last_updated"]),
            )
            before = [
                r
                for r in series
                if datetime.fromisoformat(r["last_updated"]) <= start
            ]
            out = (
                [{**before[-1], "last_updated": start.isoformat()}]
                if before
                else []
            )
            out.extend(
                r
                for r in series
                if start < datetime.fromisoformat(r["last_updated"]) <= end
            )
            if out:
                doc.append(out)
        return doc


__all__ = ["FLUSH_EVERY", "KEEP", "HeaterRecord"]
