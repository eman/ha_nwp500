"""The hot water simulator: nwp500-python's `nwp500.sim`, for each heater.

The simulator is optional. This package is imported only when it is
switched on, and then in Home Assistant's import executor: it imports the
library's simulator, and with it numpy and pandas, at module level so
nothing heavy is imported later in the event loop.

For each heater a runner keeps a 24-hour simulation up to date from the
heater's reports (`runner.py`); the entities read its answer
(`sensor.py`); the `nwp500.simulate` service walks a scenario on request
(`service.py`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from ..const import (
    DATA_SIMULATOR,
    DOMAIN,
    simulator_device_options,
    simulator_storage_key,
)

# Imported with the package, in the import executor, rather than on the
# first call of the service, in the event loop.
from . import service as service
from .runner import HeatingHistory, SimulatorRunner
from .sensor import create_simulator_sensors

if TYPE_CHECKING:
    from ..coordinator import NWP500ConfigEntry, NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# The heating history is saved at most this often, seconds: it changes on
# every report while the tank heats.
SAVE_DELAY_S = 60


class SimulatorFeature:
    """The simulator for one config entry: a runner per heater."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NWP500ConfigEntry,
        coordinator: NWP500DataUpdateCoordinator,
    ) -> None:
        """Set up; nothing runs until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.runners: dict[str, SimulatorRunner] = {}
        self._removed = False
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, simulator_storage_key(entry.entry_id)
        )

    async def async_start(self) -> None:
        """Start a runner for each heater."""
        stored = await self._store.async_load() or {}
        histories = stored.get("history") or {}
        for mac_address in self.coordinator.data or {}:
            runner = SimulatorRunner(
                self.hass,
                self.coordinator,
                mac_address,
                simulator_device_options(self.entry, mac_address),
                HeatingHistory.from_document(histories.get(mac_address)),
                self._schedule_save,
            )
            self.runners[mac_address] = runner
            runner.async_start()

    async def async_stop(self) -> None:
        """Stop the runners and save what they saw."""
        for runner in self.runners.values():
            runner.async_stop()
        if not self._removed:
            await self._store.async_save(self._document())

    async def async_remove(self) -> None:
        """Stop, and remove what the simulator stored and created."""
        self._removed = True
        for runner in self.runners.values():
            runner.async_stop()
        await self._store.async_remove()

    @callback
    def _schedule_save(self) -> None:
        if self._removed:
            return
        self._store.async_delay_save(self._document, SAVE_DELAY_S)

    def _document(self) -> dict[str, Any]:
        return {
            "history": {
                mac: runner.history.as_document()
                for mac, runner in self.runners.items()
            }
        }


async def async_setup_simulator(
    hass: HomeAssistant,
    entry: NWP500ConfigEntry,
    coordinator: NWP500DataUpdateCoordinator,
) -> None:
    """Start the simulator and register it where the platforms find it."""
    feature = SimulatorFeature(hass, entry, coordinator)
    await feature.async_start()
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})[
        DATA_SIMULATOR
    ] = feature
    _LOGGER.info(
        "The hot water simulator is on for %d heater(s)", len(feature.runners)
    )


async def async_unload_simulator(
    hass: HomeAssistant, entry: NWP500ConfigEntry
) -> None:
    """Stop the simulator on entry unload."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    feature: SimulatorFeature | None = entry_data.pop(DATA_SIMULATOR, None)
    if feature is not None:
        await feature.async_stop()


__all__ = [
    "SimulatorFeature",
    "create_simulator_sensors",
    "async_setup_simulator",
    "async_unload_simulator",
]
