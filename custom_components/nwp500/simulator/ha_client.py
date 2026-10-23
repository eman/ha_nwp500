"""Home Assistant's entities, for the library's readers.

`nwp500.sim_home_assistant.HomeAssistantEnvironment` and the library's
history readers read Home Assistant through a client. The library's own
client reads it over Home Assistant's REST API; this one answers
in-process, from the state machine and the recorder.

A state is parsed by the library's own `parse_state`, from the JSON the
REST API returns for it (`State.as_dict_json`), so both clients convert a
reading alike. It is dated by its last report (`last_reported`), so a
sensor that keeps reporting the same value stays fresh. A history is read
from the recorder as `/api/history/period` reads it, every change with its
attributes, and goes through the library's own parsers as the same JSON.
A weather entity's forecast is its `weather.get_forecasts` answer, as the
REST client reads it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.weather import ATTR_WEATHER_TEMPERATURE_UNIT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_bytes
from homeassistant.util.json import json_loads, json_loads_object

from nwp500.sim_home_assistant import (
    History,
    HomeAssistantError,
    Reading,
    State,
    history_for,
    parse_forecast,
    parse_outages,
    parse_outdoor_history,
    parse_state,
    parse_states,
    parse_units,
    to_f,
)


class InProcessClient:
    """The calls the library's readers make, answered in-process."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Read from this Home Assistant's state machine and recorder."""
        self._hass = hass

    async def state(self, entity: str) -> State:
        """The entity's value now, as the REST API would give it."""
        state = self._hass.states.get(entity)
        if state is None:
            raise HomeAssistantError(f"{entity} does not exist")
        doc = json_loads_object(state.as_dict_json)
        # The JSON is cached with the state; a report of the same value
        # moves `last_reported` without renewing it.
        doc["last_reported"] = state.last_reported.isoformat()
        return parse_state(doc)

    async def read_history(
        self, entities: Iterable[str], start: datetime, end: datetime
    ) -> History:
        """Each entity's changes from `start` to `end`.

        The first is the value in force at `start`; with each entity's
        outages, units, and every row's state as written.
        """
        names = sorted(set(entities))
        if not names:
            return History({}, {})
        if "recorder" not in self._hass.config.components:
            raise HomeAssistantError("the recorder is not running")
        try:
            return await get_instance(self._hass).async_add_executor_job(
                _history,
                self._hass,
                start.astimezone(UTC),
                end.astimezone(UTC),
                names,
            )
        except HomeAssistantError:
            raise
        except Exception as err:
            # As the REST client reports an answer it can't read: the
            # caller can go on without the history.
            raise HomeAssistantError(
                f"the recorder's history is not read: {err}"
            ) from err

    async def forecast(self, entity: str) -> tuple[list[Reading], str | None]:
        """`entity`'s hourly forecast: each hour's temperature, and its unit.

        A service call that reads and changes nothing. The service answers
        in the entity's unit, which its state names.
        """
        state = self._hass.states.get(entity)
        if state is None:
            raise HomeAssistantError(f"{entity} does not exist")
        try:
            response = await self._hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "hourly"},
                target={"entity_id": entity},
                blocking=True,
                return_response=True,
            )
        except Exception as err:
            # As the REST client reports a call refused: the environment
            # goes on without the forecast.
            raise HomeAssistantError(
                f"the forecast is not read: {err}"
            ) from err
        hours, unit = parse_forecast({"service_response": response}, entity)
        if unit is None:
            unit = _temperature_unit(state.attributes)
        return hours, unit

    async def outdoor_history(
        self, entity: str, start: datetime, end: datetime
    ) -> list[Reading]:
        """A weather entity's outdoor air from `start` to `end`, °F.

        Each reading is converted from the unit it was recorded in, or the
        entity's unit now where a row names none; a reading in no unit the
        library reads is left out.
        """
        if "recorder" not in self._hass.config.components:
            raise HomeAssistantError("the recorder is not running")
        try:
            doc = await get_instance(self._hass).async_add_executor_job(
                _history_document,
                self._hass,
                start.astimezone(UTC),
                end.astimezone(UTC),
                [entity],
            )
        except Exception as err:
            raise HomeAssistantError(
                f"the recorder's history is not read: {err}"
            ) from err
        rows = parse_outdoor_history(doc, entity)
        now: str | None = None
        if any(unit is None for _, _, unit in rows):
            state = self._hass.states.get(entity)
            now = None if state is None else _temperature_unit(state.attributes)
        out: list[Reading] = []
        for at, value, unit in rows:
            try:
                out.append((at, to_f(value, unit or now)))
            except ValueError:
                continue
        return out


def _temperature_unit(attributes: Mapping[str, Any]) -> str | None:
    """The unit a weather entity's state names for its temperatures."""
    unit = attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT)
    return unit if isinstance(unit, str) else None


def _history_document(
    hass: HomeAssistant, start: datetime, end: datetime, names: list[str]
) -> Any:
    """What `/api/history/period` answers with every change and full rows.

    Runs in the recorder's executor. The same states, encoded by the same
    encoder, so the library parses what its REST client would.
    """
    states = recorder_history.get_significant_states(
        hass,
        start,
        end,
        names,
        None,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=False,
    )
    return json_loads(json_bytes(list(states.values())))


def _history(
    hass: HomeAssistant, start: datetime, end: datetime, names: list[str]
) -> History:
    """`_history_document`, parsed. Runs in the recorder's executor.

    A day of a heater's history is tens of thousands of rows: parsed here,
    not in the event loop.
    """
    doc = _history_document(hass, start, end, names)
    outages = parse_outages(doc, end)
    units = parse_units(doc)
    states = parse_states(doc)
    return History(
        history_for(doc, names),
        {n: outages.get(n, []) for n in names},
        {n: units.get(n, []) for n in names},
        {n: states.get(n, []) for n in names},
    )
