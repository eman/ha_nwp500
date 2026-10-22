"""Home Assistant's entities, for the library's environment source.

`nwp500.sim_home_assistant.HomeAssistantEnvironment` reads the air beside
the heater, its humidity and the inlet through a client with three calls.
The library's own client makes them over Home Assistant's REST API; this
one answers them in-process, from the state machine, the recorder and the
weather entity's `weather.get_forecasts` service.

A state is parsed by the library's own `parse_state`, from the same JSON
the REST API returns for it (`State.as_dict_json`), so both clients date
and convert a reading alike: by its last report (`last_reported`), so a
sensor that keeps reporting the same value stays fresh. A history is read
from the recorder as `/api/history/period` reads it, every change with its
attributes, and goes through the library's own parsers as the same JSON.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.weather import ATTR_WEATHER_TEMPERATURE_UNIT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_bytes
from homeassistant.util.json import json_loads

from nwp500.sim_home_assistant import (
    History,
    HomeAssistantError,
    Reading,
    State,
    history_for,
    parse_forecast,
    parse_outages,
    parse_state,
    parse_states,
    parse_units,
)


class InProcessClient:
    """The calls `HomeAssistantEnvironment` makes, answered in-process."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Read from this Home Assistant's state machine."""
        self._hass = hass

    async def state(self, entity: str) -> State:
        """The entity's value now, as the REST API would give it."""
        state = self._hass.states.get(entity)
        if state is None:
            raise HomeAssistantError(f"{entity} does not exist")
        return parse_state(json_loads(state.as_dict_json))

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

        The service answers in the entity's unit, which its state names.
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
            named = state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT)
            unit = named if isinstance(named, str) else None
        return hours, unit


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
