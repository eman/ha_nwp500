"""Home Assistant's entities, for the library's environment source.

`nwp500.sim_home_assistant.HomeAssistantEnvironment` reads the air beside
the heater and its humidity through a client. The library's own client
reads them over Home Assistant's REST API; this one answers in-process,
from the state machine.

A state is parsed by the library's own `parse_state`, from the JSON the
REST API returns for it (`State.as_dict_json`), so both clients convert a
reading alike. It is dated by its last report (`last_reported`), so a
sensor that keeps reporting the same value stays fresh.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util.json import json_loads_object

from nwp500.sim_home_assistant import HomeAssistantError, State, parse_state


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
        doc = json_loads_object(state.as_dict_json)
        # The JSON is cached with the state; a report of the same value
        # moves `last_reported` without renewing it.
        doc["last_reported"] = state.last_reported.isoformat()
        return parse_state(doc)
