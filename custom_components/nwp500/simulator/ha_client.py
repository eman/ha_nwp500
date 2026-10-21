"""Home Assistant's entities, for the library's environment source.

`nwp500.sim_home_assistant.HomeAssistantEnvironment` reads the air beside
the heater, its humidity and the inlet through a client with three calls.
The library's own client makes them over Home Assistant's REST API; this
one answers them in-process, from the state machine.

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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_bytes
from homeassistant.util.json import json_loads

from nwp500.sim_home_assistant import (
    History,
    HomeAssistantError,
    Reading,
    State,
    history_for,
    parse_outages,
    parse_state,
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
        outages and units.
        """
        names = sorted(set(entities))
        if not names:
            return History({}, {})
        if "recorder" not in self._hass.config.components:
            raise HomeAssistantError("the recorder is not running")
        doc = await get_instance(self._hass).async_add_executor_job(
            _history_document,
            self._hass,
            start.astimezone(UTC),
            end.astimezone(UTC),
            names,
        )
        outages = parse_outages(doc, end)
        units = parse_units(doc)
        return History(
            history_for(doc, names),
            {n: outages.get(n, []) for n in names},
            {n: units.get(n, []) for n in names},
        )

    async def forecast(self, entity: str) -> tuple[list[Reading], str | None]:
        """Not used yet.

        The air is projected from a forecast only with a fitted location
        model, which this version does not have.
        """
        raise HomeAssistantError("forecasts are not read in this version")


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
