"""Fitting the simulator to a heater, from its recorded history.

A heater whose history is recorded (`recorder.py`) is calibrated once a
week, at night, and when the owner asks. The day files are read one
status at a time (`status_history.iter_read`), and the air beside the
heater and its humidity come from Home Assistant's recorder over the same
span (`sim_home_assistant.record_inputs`). The library refits the
defaults for the heater's tank size (`sim_calibrate.calibrate`), in an
executor: on a small machine, weeks of history take minutes.

The fitted parameters replace the heater's last only when the run fitted
something; a run that fitted nothing, or failed, is reported and the
heater keeps what it had.
"""

from __future__ import annotations

import gzip
import logging
import os
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any, TextIO, cast

from homeassistant.util import dt as dt_util

from nwp500 import sim_adapters as sa
from nwp500 import status_history
from nwp500.sim_calibrate import (
    Calibrated,
    CalibrationInputError,
    calibrate,
    report,
)
from nwp500.sim_home_assistant import HomeAssistantError, record_inputs

from .ha_client import InProcessClient

if TYPE_CHECKING:
    from nwp500.models import DeviceStatus
    from nwp500.sim.params import HeaterParams
    from nwp500.sim_home_assistant import HomeAssistantClient, RecordInputs

    from .recorder import HistoryRecorder
    from .runner import SimulatorRunner

_LOGGER = logging.getLogger(__name__)

#: How often a heater is refitted, and when in the night it may be.
CALIBRATE_EVERY = timedelta(days=7)
CALIBRATE_AT = dt_time(3, 30)
#: Runs in a row that fit nothing, or failed, before Repairs says so.
FAILURES_REPORTED = 3

_SUFFIX = ".jsonl"
_GZ_SUFFIX = ".jsonl.gz"


def day_files(directory: str) -> list[tuple[date, str]]:
    """The recorded days in `directory`, oldest first. Runs in an executor.

    A day both compressed and plain (a late line, not yet compressed in)
    has both files, the compressed one first.
    """
    if not os.path.isdir(directory):
        return []
    out: list[tuple[date, int, str]] = []
    for name in os.listdir(directory):
        for order, suffix in enumerate((_GZ_SUFFIX, _SUFFIX)):
            if name.endswith(suffix):
                try:
                    day = date.fromisoformat(name[: -len(suffix)])
                except ValueError:
                    break
                out.append((day, order, os.path.join(directory, name)))
                break
    return [(day, path) for day, _, path in sorted(out)]


def _open(path: str) -> TextIO:
    if path.endswith(_GZ_SUFFIX):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")  # noqa: SIM115


def recorded(paths: Sequence[str]) -> Iterator[tuple[datetime, DeviceStatus]]:
    """The statuses in `paths`, one at a time, a file at a time.

    A file that cannot be read whole ends at its last good line, and the
    reading goes on with the next: a damaged day is a gap, not a stop.
    """
    for path in paths:
        try:
            with _open(path) as f:
                yield from status_history.iter_read(f)
        except (OSError, EOFError, ValueError) as err:
            _LOGGER.warning("Part of %s is not read: %s", path, err)


def run_calibration(
    paths: Sequence[str], base: HeaterParams, record: RecordInputs | None
) -> Calibrated:
    """The library's calibration of `base` on `paths`. Runs in an executor."""
    return calibrate(recorded(paths), params=base, record=record)


@dataclass
class CalibrationReport:
    """What the last run of a heater's calibration found, as stored."""

    #: When it ran, how long it took, and whether it fitted anything.
    at: datetime
    duration_s: float
    fitted: bool
    #: Why it failed; None when it ran.
    error: str | None = None
    #: The report's lines: each term, its value, and why it kept the
    #: base's value when it did.
    terms: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    statuses: int = 0
    gaps: int = 0
    #: How the air beside the heater was given (`source`), or None.
    air: str | None = None
    #: Runs in a row, up to this one, that fitted nothing or failed.
    failures: int = 0

    def as_document(self) -> dict[str, Any]:
        """For the store, diagnostics and the sensor's attributes."""
        return {
            "at": self.at.isoformat(),
            "duration_s": round(self.duration_s, 1),
            "fitted": self.fitted,
            "error": self.error,
            "terms": self.terms,
            "notes": self.notes,
            "statuses": self.statuses,
            "gaps": self.gaps,
            "air": self.air,
            "failures": self.failures,
        }

    @classmethod
    def from_document(cls, doc: Any) -> CalibrationReport | None:
        """The inverse of `as_document`; None for anything unreadable."""
        if not isinstance(doc, dict):
            return None
        try:
            at = datetime.fromisoformat(doc["at"])
            return cls(
                at=at if at.tzinfo else at.replace(tzinfo=UTC),
                duration_s=float(doc.get("duration_s") or 0.0),
                fitted=bool(doc.get("fitted")),
                error=doc.get("error"),
                terms=list(doc.get("terms") or []),
                notes=list(doc.get("notes") or []),
                statuses=int(doc.get("statuses") or 0),
                gaps=int(doc.get("gaps") or 0),
                air=doc.get("air"),
                failures=int(doc.get("failures") or 0),
            )
        except KeyError, TypeError, ValueError:
            return None


def _terms(done: Calibrated) -> list[dict[str, Any]]:
    return [
        {
            "name": term.name,
            "value": term.value,
            "from": term.samples,
            "error": term.error,
            "fitted": term.fitted,
            "why": term.why or None,
            "note": term.note or None,
        }
        for term in report(done)
    ]


def due(last: CalibrationReport | None, now: datetime) -> bool:
    """Whether a heater last calibrated as `last` is due again."""
    return last is None or now - last.at >= CALIBRATE_EVERY - timedelta(hours=1)


async def async_calibrate(
    runner: SimulatorRunner,
    recorder: HistoryRecorder,
    last: CalibrationReport | None,
    apply: Callable[[HeaterParams, datetime], None],
    clock: Callable[[], datetime] = dt_util.utcnow,
) -> CalibrationReport:
    """Refit one heater from its recorded history; report what was found.

    `apply` takes the fitted parameters when the run fitted anything.
    """
    hass = runner.hass
    started = clock()
    t0 = time.monotonic()
    failures = 0 if last is None else last.failures

    def failed(error: str) -> CalibrationReport:
        _LOGGER.warning(
            "The simulator could not be calibrated for %s: %s",
            runner.mac_address,
            error,
        )
        return CalibrationReport(
            at=started,
            duration_s=time.monotonic() - t0,
            fitted=False,
            error=error,
            failures=failures + 1,
        )

    feature = runner.coordinator.device_features.get(runner.mac_address)
    if feature is None:
        return failed("the heater's features are not read yet")
    # Today's statuses too.
    await recorder.async_flush()
    days = await hass.async_add_executor_job(day_files, recorder.directory)
    if not days:
        return failed("no history is recorded yet")
    start = datetime.combine(days[0][0], dt_time(0), tzinfo=UTC)
    record = None
    entities = runner.entities
    if entities.air is not None or entities.humidity is not None:
        try:
            record = await record_inputs(
                # The in-process client answers the calls the library's
                # REST client would.
                cast("HomeAssistantClient", InProcessClient(hass)),
                entities,
                start,
                started,
            )
        except HomeAssistantError as err:
            # record_inputs reports what it could not read in its notes;
            # anything else leaves the air out, as with no sensor.
            _LOGGER.debug("The air's history is not read: %s", err)
    base = sa.params(feature)
    try:
        done = await hass.async_add_executor_job(
            run_calibration, [path for _, path in days], base, record
        )
    except CalibrationInputError as err:
        return failed(str(err))
    except Exception as err:
        _LOGGER.exception("The simulator's calibration failed")
        return failed(f"{type(err).__name__}: {err}")
    terms = _terms(done)
    fitted = any(term["fitted"] for term in terms)
    if fitted:
        apply(done.calibration.params, started)
    _LOGGER.info(
        "Calibrated the simulator for %s on %d statuses: %d of %d terms fitted",
        runner.mac_address,
        done.n_statuses,
        sum(term["fitted"] for term in terms),
        len(terms),
    )
    return CalibrationReport(
        at=started,
        duration_s=time.monotonic() - t0,
        fitted=fitted,
        terms=terms,
        notes=list(done.notes),
        statuses=done.n_statuses,
        gaps=done.n_gaps,
        air=done.air,
        failures=0 if fitted else failures + 1,
    )


__all__ = [
    "CALIBRATE_AT",
    "CALIBRATE_EVERY",
    "FAILURES_REPORTED",
    "CalibrationReport",
    "async_calibrate",
    "day_files",
    "due",
    "recorded",
    "run_calibration",
]
