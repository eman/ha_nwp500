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
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_bytes
from homeassistant.util.json import json_loads, json_loads_object

from nwp500.sim_home_assistant import (
    History,
    HomeAssistantError,
    State,
    history_for,
    parse_outages,
    parse_state,
    parse_states,
    parse_units,
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
