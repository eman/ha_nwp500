"""The simulator's modelled path over Home Assistant's websocket API.

For a chart: when the tank comes to temperature, when the compressor
next starts, and the tank's two probes along the next day, as each
heater's latest simulation has them, in Home Assistant's temperature
unit. `nwp500/simulator/path` answers once; `nwp500/simulator/subscribe_path`
sends the path now and again with each new simulation, across a reload
of the integration.

Registered with the integration whether or not the simulator is on, and
light: the simulator's own code is reached only through a running
feature, so nothing here imports its packages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Typed as probatio, as Home Assistant types schemas (config_flow.py).
    import probatio as vol
else:
    import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import VolDictType

from .const import (
    DOMAIN,
    simulator_feature,
    simulator_started_signal,
    simulator_stopped_signal,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .simulator.runner import SimulatorRunner

ERR_NOT_FOUND = "not_found"
ERR_SIMULATOR_OFF = "simulator_not_enabled"
#: The path's `waiting` once the simulator stops for the heater.
WAIT_SIMULATOR_OFF = "simulator_off"

_HEATER: VolDictType = {
    vol.Optional(ATTR_DEVICE_ID): cv.string,
    vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
}


class _NoHeaterError(Exception):
    """The message names no heater of this integration."""


def _heater(hass: HomeAssistant, msg: dict[str, Any]) -> tuple[str, str]:
    """The config entry and MAC of the heater a message names."""
    device_id = msg.get(ATTR_DEVICE_ID)
    if (entity_id := msg.get(ATTR_ENTITY_ID)) is not None:
        entity = er.async_get(hass).async_get(entity_id)
        device_id = None if entity is None else entity.device_id
    device = (
        None if device_id is None else dr.async_get(hass).async_get(device_id)
    )
    if device is None:
        raise _NoHeaterError
    macs = {value for domain, value in device.identifiers if domain == DOMAIN}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        coordinator = entry.runtime_data
        for mac in (coordinator.data if coordinator else None) or {}:
            if mac in macs:
                return entry.entry_id, mac
    raise _NoHeaterError


def _readable(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    mac: str,
) -> bool:
    """Whether the user may read the heater, as its entities' state.

    The path is the heater's state and forecast: the user must be able to
    read its water heater entity, and the entity the message names.
    """
    user = connection.user
    if user.is_admin or user.permissions.access_all_entities(POLICY_READ):
        return True
    heater = er.async_get(hass).async_get_entity_id(
        "water_heater", DOMAIN, f"{mac}_water_heater"
    )
    named = msg.get(ATTR_ENTITY_ID)
    return all(
        entity_id is not None
        and user.permissions.check_entity(entity_id, POLICY_READ)
        for entity_id in (heater, *(() if named is None else (named,)))
    )


def _runner(
    hass: HomeAssistant, entry_id: str, mac: str
) -> SimulatorRunner | None:
    """The heater's runner, while the simulator runs for it."""
    entry: ConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    feature = None if entry is None else simulator_feature(hass, entry)
    return None if feature is None else feature.runners.get(mac)


def _document(hass: HomeAssistant, runner: SimulatorRunner) -> dict[str, Any]:
    """The runner's latest path; before its first walk, a wait."""
    unit = hass.config.units.temperature_unit
    answer = runner.answer
    if answer is None:
        return {"waiting": "no_answer", "unit": unit, "points": []}
    return answer.path_document(unit)


def _resolve(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> tuple[str, str, SimulatorRunner] | None:
    """The heater and its runner, or None with the error sent."""
    try:
        entry_id, mac = _heater(hass, msg)
    except _NoHeaterError:
        connection.send_error(
            msg["id"], ERR_NOT_FOUND, "No NWP500 water heater found"
        )
        return None
    if not _readable(hass, connection, msg, mac):
        connection.send_error(
            msg["id"], websocket_api.ERR_UNAUTHORIZED, "Unauthorized"
        )
        return None
    runner = _runner(hass, entry_id, mac)
    if runner is None:
        connection.send_error(
            msg["id"],
            ERR_SIMULATOR_OFF,
            "The hot water simulator isn't running for this water heater",
        )
        return None
    return entry_id, mac, runner


@websocket_api.websocket_command(
    {vol.Required("type"): "nwp500/simulator/path", **_HEATER}
)
@callback
def websocket_path(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """The heater's modelled path, once."""
    if (found := _resolve(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], _document(hass, found[2]))


@websocket_api.websocket_command(
    {vol.Required("type"): "nwp500/simulator/subscribe_path", **_HEATER}
)
@callback
def websocket_subscribe_path(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """The heater's modelled path, now and with each new simulation.

    A reload makes a new runner: the subscription follows it. While the
    simulator is stopped, the path is empty and says so.
    """
    if (found := _resolve(hass, connection, msg)) is None:
        return
    entry_id, mac, runner = found
    unsubs: list[CALLBACK_TYPE] = []
    following: list[CALLBACK_TYPE] = []

    @callback
    def send(current: SimulatorRunner) -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], _document(hass, current))
        )

    @callback
    def follow(current: SimulatorRunner) -> None:
        for remove in following:
            remove()
        following[:] = [current.async_add_listener(lambda: send(current))]

    @callback
    def started() -> None:
        current = _runner(hass, entry_id, mac)
        if current is not None:
            follow(current)
            send(current)

    @callback
    def stopped() -> None:
        for remove in following:
            remove()
        following.clear()
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {
                    "waiting": WAIT_SIMULATOR_OFF,
                    "unit": hass.config.units.temperature_unit,
                    "points": [],
                },
            )
        )

    @callback
    def unsubscribe() -> None:
        for remove in (*following, *unsubs):
            remove()

    follow(runner)
    unsubs.append(
        async_dispatcher_connect(
            hass, simulator_started_signal(entry_id), started
        )
    )
    unsubs.append(
        async_dispatcher_connect(
            hass, simulator_stopped_signal(entry_id), stopped
        )
    )
    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    send(runner)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the simulator's websocket commands."""
    websocket_api.async_register_command(hass, websocket_path)
    websocket_api.async_register_command(hass, websocket_subscribe_path)
