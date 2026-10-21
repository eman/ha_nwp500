"""Home Assistant's entities, for the library's environment source.

`nwp500.sim_home_assistant.HomeAssistantEnvironment` reads the air beside
the heater, its humidity and the inlet through a client with three calls.
The library's own client makes them over Home Assistant's REST API; this
one answers them in-process, from the state machine.

A state is parsed by the library's own `parse_state`, from the same JSON
the REST API returns for it (`State.as_dict_json`), so both clients date
and convert a reading alike.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util.json import json_loads

from nwp500.sim_home_assistant import (
    History,
    HomeAssistantError,
    Reading,
    State,
    parse_state,
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
        """Not used yet.

        Only the inlet reads history, and the simulator does not take an
        inlet probe in this version.
        """
        raise HomeAssistantError("history is not read in this version")

    async def forecast(self, entity: str) -> tuple[list[Reading], str | None]:
        """Not used yet.

        The air is projected from a forecast only with a fitted location
        model, which this version does not have.
        """
        raise HomeAssistantError("forecasts are not read in this version")
