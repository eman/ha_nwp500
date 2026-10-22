"""The air beside the heater ahead of time, from a weather forecast.

The library projects the air over the simulation's horizon from an hourly
outdoor forecast through a model of how that air follows the outdoor air,
damped and late (`nwp500.sim.location_air`), anchored to the air sensor's
reading now. The model is this heater's own: fitted, with each
calibration, on the air sensor's recorded history against the weather
entity's, its `temperature` attribute being the outdoor air the forecast
starts from.

A later fit replaces the heater's model only when it does better on the
latest evidence, as the heater's own terms are refined: the latest fifth
of the readings is held out of the refit, and each model is scored there
by the spread of its error, which an anchor cannot take out.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.weather import (
    ATTR_WEATHER_TEMPERATURE,
    ATTR_WEATHER_TEMPERATURE_UNIT,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import State

from nwp500.sim import location_air
from nwp500.sim.location_air import LocationAirModel, Reading
from nwp500.sim_home_assistant import to_f

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .runner import SimulatorRunner

_LOGGER = logging.getLogger(__name__)

#: The history fitted, at most: as long as the heater's own.
HISTORY = timedelta(days=60)
#: The air's readings are averaged over this, so a sensor that reports
#: often doesn't outweigh one that reports on change.
BUCKET = timedelta(minutes=30)
#: At least this many averaged readings, over at least `MIN_SPAN`.
MIN_READINGS = 72
MIN_SPAN = timedelta(days=3)
#: Below this correlation the air doesn't follow the outdoor air enough
#: for a forecast to say where it goes.
MIN_R = 0.5
#: The share of the readings held out to judge a refit by, the latest.
HELD_OUT = 0.2

#: How a run ended for the model.
KEPT_BASE = "base"
KEPT_REFIT = "refit"


@dataclass(frozen=True)
class LocationFit:
    """A fitting run's model and how it was judged.

    `model` is the model to use from now on, None when there is none;
    `kept` says which it is, the heater's model (`base`) or the refit
    (`refit`), and is None when neither is used. `error` and
    `base_error` are the spread of each one's error on the readings
    held out, °F.
    """

    model: LocationAirModel | None
    kept: str | None
    r: float | None = None
    n: int = 0
    error: float | None = None
    base_error: float | None = None
    why: str | None = None

    def as_document(self) -> dict[str, Any]:
        """For the report: the model in force, and how it was judged."""
        doc: dict[str, Any] = {
            "kept": self.kept,
            "r": None if self.r is None else round(self.r, 3),
            "readings": self.n,
            "error_f": None if self.error is None else round(self.error, 2),
            "base_error_f": (
                None if self.base_error is None else round(self.base_error, 2)
            ),
            "why": self.why,
        }
        if self.model is not None:
            doc.update(model_to_document(self.model))
        return doc


def model_to_document(model: LocationAirModel) -> dict[str, float]:
    """For the store."""
    return {
        "damping": model.damping,
        "lag_hours": model.lag.total_seconds() / 3600,
        "offset_f": model.offset_f,
    }


def model_from_document(doc: Any) -> LocationAirModel | None:
    """The inverse of `model_to_document`; None for anything unreadable."""
    if not isinstance(doc, dict):
        return None
    try:
        model = LocationAirModel(
            damping=float(doc["damping"]),
            lag=timedelta(hours=float(doc["lag_hours"])),
            offset_f=float(doc["offset_f"]),
        )
    except KeyError, TypeError, ValueError:
        return None
    values = (model.damping, model.offset_f, model.lag.total_seconds())
    return model if all(math.isfinite(v) for v in values) else None


def _state_f(value: Any, unit: Any) -> float | None:
    """A state's temperature, °F; None for none."""
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    if not math.isfinite(number):
        return None
    try:
        return to_f(number, unit if isinstance(unit, str) else None)
    except ValueError:
        return None


def read_histories(
    hass: HomeAssistant,
    air: str,
    weather: str,
    start: datetime,
    end: datetime,
) -> tuple[list[Reading], list[Reading]]:
    """The air sensor's readings and the weather entity's outdoor air, °F.

    From `start` to `end`. Runs in the recorder's executor.
    """
    states = recorder_history.get_significant_states(
        hass,
        start.astimezone(UTC),
        end.astimezone(UTC),
        [air, weather],
        None,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=False,
    )
    readings: list[Reading] = []
    for state in states.get(air, []):
        if not isinstance(state, State):
            continue
        value = _state_f(
            state.state, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        )
        if value is not None:
            readings.append((state.last_updated, value))
    outdoor: list[Reading] = []
    for state in states.get(weather, []):
        if not isinstance(state, State):
            continue
        value = _state_f(
            state.attributes.get(ATTR_WEATHER_TEMPERATURE),
            state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT),
        )
        if value is not None:
            outdoor.append((state.last_updated, value))
    return readings, outdoor


def averaged(readings: Sequence[Reading]) -> list[Reading]:
    """`readings` averaged per `BUCKET`, each at its bucket's middle."""
    buckets: dict[int, list[float]] = {}
    width = BUCKET.total_seconds()
    for at, value in readings:
        buckets.setdefault(int(at.timestamp() // width), []).append(value)
    return [
        (
            datetime.fromtimestamp((key + 0.5) * width, UTC),
            float(np.mean(values)),
        )
        for key, values in sorted(buckets.items())
    ]


def spread(
    model: LocationAirModel,
    readings: Sequence[Reading],
    outdoor: Sequence[Reading],
) -> float:
    """The standard deviation of `model`'s error on `readings`, °F.

    What is left of the error once an anchor takes out its mean.
    """
    predicted = model.at(outdoor, [at for at, _ in readings])
    errors = np.array([v for _, v in readings]) - np.array(predicted)
    return float(np.std(errors))


def fit(
    readings: Sequence[Reading],
    outdoor: Sequence[Reading],
    base: LocationAirModel | None,
) -> LocationFit:
    """Fit the model on the air's `readings` against the `outdoor` air's.

    Or keep `base` where the refit does no better on the latest readings.
    Runs in an executor.
    """
    kept = KEPT_BASE if base is not None else None
    air = averaged(readings)
    # Only where the outdoor air is known: held past its ends, it would
    # say nothing about the lag.
    if outdoor:
        first, last = min(t for t, _ in outdoor), max(t for t, _ in outdoor)
        air = [(t, v) for t, v in air if first <= t <= last]
    if len(air) < MIN_READINGS or air[-1][0] - air[0][0] < MIN_SPAN:
        days = MIN_SPAN.total_seconds() / 86400
        return LocationFit(
            base,
            kept,
            n=len(air),
            why=(
                "too little history of the air and the outdoor air: "
                f"{len(air)} half-hours, at least {MIN_READINGS} over "
                f"{days:g} days needed"
            ),
        )
    split = len(air) - max(2, round(len(air) * HELD_OUT))
    train, held = air[:split], air[split:]
    try:
        refit = location_air.fit(train, outdoor)
    except ValueError as err:
        return LocationFit(base, kept, n=len(air), why=str(err))
    error = spread(refit.model, held, outdoor)
    base_error = None if base is None else spread(base, held, outdoor)
    if not math.isfinite(refit.r) or refit.r < MIN_R:
        return LocationFit(
            base,
            kept,
            r=refit.r if math.isfinite(refit.r) else None,
            n=len(air),
            error=error if math.isfinite(error) else None,
            base_error=base_error,
            why=(
                "the air does not vary, so it can't be fitted"
                if not math.isfinite(refit.r)
                else f"the air follows the outdoor air too loosely (r "
                f"{refit.r:.2f}, at least {MIN_R} needed)"
            ),
        )
    if base_error is not None and base_error <= error:
        return LocationFit(
            base,
            KEPT_BASE,
            r=refit.r,
            n=len(air),
            error=error,
            base_error=base_error,
            why="the heater's model did as well on the latest readings",
        )
    return LocationFit(
        refit.model,
        KEPT_REFIT,
        r=refit.r,
        n=len(air),
        error=error,
        base_error=base_error,
    )


__all__ = [
    "KEPT_BASE",
    "KEPT_REFIT",
    "LocationFit",
    "async_fit_location",
    "averaged",
    "fit",
    "model_from_document",
    "model_to_document",
    "read_histories",
    "spread",
]


async def async_fit_location(
    hass: HomeAssistant,
    runner: SimulatorRunner,
    now: datetime,
) -> LocationFit | None:
    """Fit, or keep, the heater's model from the recorder's history.

    None when the heater has no weather entity to project from.
    """
    entities = runner.entities
    if entities.weather is None:
        return None
    base = runner.location_air
    kept = KEPT_BASE if base is not None else None
    if entities.air is None:
        return LocationFit(
            None,
            None,
            why="an air sensor beside the heater is needed to fit and anchor "
            "the projection",
        )
    if "recorder" not in hass.config.components:
        return LocationFit(
            base, kept, why="Home Assistant's recorder is not running"
        )
    try:
        readings, outdoor = await get_instance(hass).async_add_executor_job(
            read_histories,
            hass,
            entities.air,
            entities.weather,
            now - HISTORY,
            now,
        )
    except Exception as err:
        _LOGGER.warning("The air's history is not read: %s", err)
        return LocationFit(
            base, kept, why=f"the recorder's history is not read: {err}"
        )
    return await hass.async_add_executor_job(fit, readings, outdoor, base)
