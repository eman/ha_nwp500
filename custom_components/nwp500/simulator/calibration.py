"""Fitting the simulator to a heater, from its history.

Each heater is calibrated once a week, at night, and when the owner asks,
from one of two histories:

- the statuses it reported, where its history is recorded (`recorder.py`):
  the day files are read one status at a time (`status_history.iter_read`)
  and fitted by `sim_calibrate.calibrate`;
- the changes of its own entities, as Home Assistant's recorder keeps
  them (`sim_home_assistant.read_heater_history`, a day at a time), fitted
  by `sim_calibrate.calibrate_changes`.

The statuses are the finer record, and are used once they reach as far
back as the recorder does; before then, the entities' longer history is.
The air beside the heater and its humidity come from the recorder over the
same span (`sim_home_assistant.record_inputs`). The fit runs in an
executor: on a small machine, weeks of history take minutes.

A first run fits the defaults for the heater's tank size, and its fit
replaces them. A later run refines the heater's fit without making it
worse (`keep_better`): each group of terms is refitted with its latest
evidence held out, and replaces the fit only if it scores better on that
evidence; a group with too little evidence to judge keeps the fit. The
heater's parameters change only when a group was replaced. A run that
fitted nothing, or failed, is reported and the heater keeps what it had.
"""

from __future__ import annotations

import gzip
import logging
import os
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any, TextIO, cast

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from nwp500 import sim_adapters as sa
from nwp500 import status_history
from nwp500.sim_calibrate import (
    Calibrated,
    CalibrationInputError,
    calibrate,
    calibrate_changes,
    report,
)
from nwp500.sim_home_assistant import (
    HeaterEntities,
    HomeAssistantError,
    read_heater_history,
    record_inputs,
)

from ..const import DOMAIN
from .ha_client import InProcessClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from nwp500.models import DeviceStatus
    from nwp500.sim.params import HeaterParams
    from nwp500.sim_home_assistant import (
        HeaterHistory,
        HomeAssistantClient,
        RecordInputs,
    )

    from .recorder import HistoryRecorder
    from .runner import SimulatorRunner

_LOGGER = logging.getLogger(__name__)

#: How often a heater is refitted, and when in the night it may be.
CALIBRATE_EVERY = timedelta(days=7)
CALIBRATE_AT = dt_time(3, 30)
#: Runs in a row that fit nothing, or failed, before Repairs says so.
FAILURES_REPORTED = 3
#: At most this much of the entities' history is read: the recorder can
#: keep a year, and a year of a heater's changes is millions of rows.
ENTITY_HISTORY_DAYS = 60

#: Where the history came from.
SOURCE_STATUSES = "statuses"
SOURCE_ENTITIES = "entities"

#: The heater's own entities, by their role in `HeaterEntities`: each
#: one's platform and unique ID after the heater's address. Only `heard`
#: and the probes are needed.
_HEATER_ENTITIES: dict[str, tuple[str, str]] = {
    "heard": ("sensor", "diagnostic_last_response"),
    "upper": ("sensor", "tank_upper_temperature"),
    "lower": ("sensor", "tank_lower_temperature"),
    "compressor": ("binary_sensor", "comp_use"),
    "upper_element": ("binary_sensor", "heat_upper_use"),
    "lower_element": ("binary_sensor", "heat_lower_use"),
    "heat_source": ("sensor", "current_heat_use"),
    "dhw_in_use": ("binary_sensor", "dhw_use"),
    "target": ("sensor", "hp_lower_off_temp_setting"),
    "operation_mode": ("sensor", "operation_mode"),
    "anti_legionella": ("binary_sensor", "anti_legionella_operation_busy"),
    "error_code": ("sensor", "error_code"),
    "power": ("sensor", "current_inst_power"),
    "energy_to_setpoint": ("sensor", "energy_to_setpoint"),
    "ambient": ("sensor", "ambient_temperature"),
}
_REQUIRED = ("heard", "upper", "lower")

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
        except (OSError, EOFError, ValueError, zlib.error) as err:
            _LOGGER.warning("Part of %s is not read: %s", path, err)


def first_status(paths: Sequence[str]) -> datetime | None:
    """When the first status in `paths` arrived. Runs in an executor."""
    for at, _ in recorded(paths):
        return at
    return None


def _first_change(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> datetime | None:
    """When the recorder's history of `entity_id` begins after `start`.

    Runs in the recorder's executor.
    """
    states = recorder_history.state_changes_during_period(
        hass,
        start,
        end,
        entity_id,
        no_attributes=True,
        limit=1,
        include_start_time_state=True,
    )
    rows = states.get(entity_id) or []
    # The state in force at the start, where there was one, is dated
    # when it was set.
    return max(rows[0].last_updated, start) if rows else None


async def first_change(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> datetime | None:
    """`_first_change`, in the recorder's executor."""
    return await get_instance(hass).async_add_executor_job(
        _first_change, hass, entity_id, start, end
    )


def run_calibration(
    paths: Sequence[str],
    base: HeaterParams,
    record: RecordInputs | None,
    keep_better: bool = False,
) -> Calibrated:
    """The library's calibration of `base` on `paths`. Runs in an executor."""
    return calibrate(
        recorded(paths), params=base, record=record, keep_better=keep_better
    )


def run_change_calibration(
    history: HeaterHistory,
    base: HeaterParams,
    record: RecordInputs | None,
    keep_better: bool = False,
) -> Calibrated:
    """The library's calibration of `base` on the heater's entity changes.

    Runs in an executor.
    """
    return calibrate_changes(
        history.rows,
        history.observed,
        params=base,
        record=record,
        notes=history.notes,
        keep_better=keep_better,
    )


def heater_entities(
    hass: HomeAssistant, mac_address: str
) -> tuple[HeaterEntities | None, list[str]]:
    """The heater's own entities whose history the recorder keeps.

    None when one it needs is disabled or not recorded; with the names of
    those it needs that are missing.
    """
    registry = er.async_get(hass)
    recorder = get_instance(hass)
    keeps = getattr(recorder, "entity_filter", None)
    found: dict[str, str] = {}
    for role, (platform, key) in _HEATER_ENTITIES.items():
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{mac_address}_{key}"
        )
        if entity_id is None:
            continue
        entry = registry.async_get(entity_id)
        if entry is None or entry.disabled:
            continue
        if keeps is not None and not keeps(entity_id):
            continue
        found[role] = entity_id
    missing = [
        _HEATER_ENTITIES[role][1] for role in _REQUIRED if role not in found
    ]
    if missing:
        return None, missing
    return HeaterEntities(**found), []


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
    #: The history fitted, `statuses` or `entities`; None when none was.
    source: str | None = None
    #: Whether the run's fit replaced the heater's parameters.
    applied: bool = False
    #: Refining a fit: each group of terms, kept (`base`) or replaced
    #: (`refit`), its error and the refit's on the evidence held out, and
    #: why. Empty for a first fit.
    judged: list[dict[str, Any]] = field(default_factory=list)
    #: The air's projection from the weather forecast: the model in force
    #: and how the run judged it (`location.LocationFit`); None with no
    #: weather entity.
    location_air: dict[str, Any] | None = None
    #: The circulation loop's terms: those in force and how the run
    #: judged them (`CirculationFit`); None without a meter and a pump.
    circulation: dict[str, Any] | None = None

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
            "source": self.source,
            "applied": self.applied,
            "judged": self.judged,
            "location_air": self.location_air,
            "circulation": self.circulation,
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
                source=doc.get("source"),
                applied=bool(doc.get("applied")),
                judged=list(doc.get("judged") or []),
                location_air=(
                    doc["location_air"]
                    if isinstance(doc.get("location_air"), dict)
                    else None
                ),
                circulation=(
                    doc["circulation"]
                    if isinstance(doc.get("circulation"), dict)
                    else None
                ),
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
    recorder: HistoryRecorder | None,
    last: CalibrationReport | None,
    apply: Callable[[HeaterParams, datetime], None],
    clock: Callable[[], datetime] = dt_util.utcnow,
) -> CalibrationReport:
    """Refit one heater from its history; report what was found.

    `recorder` is the heater's status recording, if it has one. `apply`
    takes the parameters when the run fitted, or replaced, anything.
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
    days: list[tuple[date, str]] = []
    statuses_from: datetime | None = None
    if recorder is not None:
        # Today's statuses too.
        await recorder.async_flush()
        try:
            days = await hass.async_add_executor_job(
                day_files, recorder.directory
            )
            if days:
                statuses_from = await hass.async_add_executor_job(
                    first_status, [path for _, path in days]
                )
        except OSError as err:
            return failed(f"the recorded history is not read: {err}")
    entities: HeaterEntities | None = None
    entities_from: datetime | None = None
    if "recorder" not in hass.config.components:
        unrecorded = "Home Assistant's recorder is not running"
    elif len(runner.coordinator.data or {}) > 1:
        # The last response time is the account's, on its first heater:
        # it does not say when each heater was heard from.
        unrecorded = (
            "Home Assistant's history does not say when each heater of an "
            "account with several was heard from"
        )
    else:
        entities, missing = heater_entities(hass, runner.mac_address)
        unrecorded = (
            "Home Assistant's recorder does not keep its "
            + ", ".join(missing)
            + " sensor"
            + ("s" if len(missing) > 1 else "")
        )
        if entities is not None:
            # From when all it needs are recorded: the last of their
            # histories to begin.
            since = started - timedelta(days=ENTITY_HISTORY_DAYS)
            try:
                firsts = [
                    await first_change(hass, entity_id, since, started)
                    for entity_id in (
                        entities.heard,
                        entities.upper,
                        entities.lower,
                    )
                ]
            except Exception as err:
                # The recorder's history is left out, and the statuses, if
                # any, are fitted.
                _LOGGER.warning("The recorder's history is not read: %s", err)
                unrecorded = f"Home Assistant's recorder is not read ({err})"
            else:
                if None in firsts:
                    unrecorded = (
                        "Home Assistant's recorder has no history of its "
                        "last response time and tank temperatures"
                    )
                else:
                    entities_from = max(t for t in firsts if t is not None)
    # The history that reaches further back first; the statuses, the finer
    # record, when both reach as far. The other if the first fails.
    sources: list[tuple[datetime, str]] = []
    if statuses_from is not None:
        sources.append((statuses_from, SOURCE_STATUSES))
    if entities_from is not None:
        sources.append((entities_from, SOURCE_ENTITIES))
    sources.sort(key=lambda s: (s[0], s[1] != SOURCE_STATUSES))
    if not sources:
        why = "the heater's history is not recorded"
        return failed(f"no history: {why}, and {unrecorded}")
    # The in-process client answers the calls the library's REST client
    # would.
    client = cast("HomeAssistantClient", InProcessClient(hass))
    # Refine the heater's fit, where it has one: a group of terms is
    # replaced only when its refit scores better on its latest evidence.
    # Not the defaults: they are another heater's fit, kept wherever the
    # evidence is too little to judge by.
    refine = runner.params is not None
    base = runner.params if runner.params is not None else sa.params(feature)
    done: Calibrated | None = None
    errors: list[tuple[str, str]] = []
    for start, source in sources:
        record = None
        if (
            runner.entities.air is not None
            or runner.entities.humidity is not None
        ):
            try:
                record = await record_inputs(
                    client, runner.entities, start, started
                )
            except HomeAssistantError as err:
                # record_inputs reports what it could not read in its
                # notes; anything else leaves the air out, as with no
                # sensor.
                _LOGGER.debug("The air's history is not read: %s", err)
        try:
            if source == SOURCE_STATUSES or entities is None:
                done = await hass.async_add_executor_job(
                    run_calibration,
                    [path for _, path in days],
                    base,
                    record,
                    refine,
                )
            else:
                history = await read_heater_history(
                    client, entities, start, started
                )
                done = await hass.async_add_executor_job(
                    run_change_calibration, history, base, record, refine
                )
            break
        except (HomeAssistantError, CalibrationInputError) as err:
            errors.append((source, str(err)))
        except Exception as err:
            _LOGGER.exception("The simulator's calibration failed")
            errors.append((source, f"{type(err).__name__}: {err}"))
    if done is None:
        if len(errors) == 1:
            return failed(errors[0][1])
        return failed("; ".join(f"{s}: {e}" for s, e in errors))
    terms = _terms(done)
    # Refining, a group kept is reported unfitted: a term fitted is one
    # the run replaced.
    fitted = any(term["fitted"] for term in terms)
    if fitted:
        apply(done.calibration.params, started)
    judged = [
        {
            "group": j.group,
            "kept": j.kept,
            "base_error": j.base_error,
            "refit_error": j.refit_error,
            "held_out": j.n_held,
            "why": j.why,
        }
        for j in done.judged
    ]
    # A fit kept because it scored no worse is no failure; one kept for
    # want of a refit, or over a refit rejected on another ground (the
    # compressor's pinned on a bound, say), is.
    kept_better = any(
        j.kept == "base"
        and j.base_error is not None
        and j.refit_error is not None
        and j.base_error <= j.refit_error
        for j in done.judged
    )
    _LOGGER.info(
        "Calibrated the simulator for %s on %d %s: %d of %d terms fitted",
        runner.mac_address,
        done.n_statuses,
        "statuses" if source == SOURCE_STATUSES else "entity changes",
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
        failures=0 if fitted or kept_better else failures + 1,
        source=source,
        applied=fitted,
        judged=judged,
    )


__all__ = [
    "CALIBRATE_AT",
    "CALIBRATE_EVERY",
    "FAILURES_REPORTED",
    "ENTITY_HISTORY_DAYS",
    "SOURCE_ENTITIES",
    "SOURCE_STATUSES",
    "CalibrationReport",
    "async_calibrate",
    "day_files",
    "due",
    "first_change",
    "first_status",
    "heater_entities",
    "recorded",
    "run_calibration",
    "run_change_calibration",
]
