"""The hot water simulator: nwp500-python's `nwp500.sim`, for each heater.

The simulator is optional. This package is imported only when it is
switched on, and then in Home Assistant's import executor: it imports the
library's simulator, and with it numpy and pandas, at module level so
nothing heavy is imported later in the event loop.

For each heater a runner keeps a 24-hour simulation up to date from the
heater's reports (`runner.py`); the entities read its answer
(`sensor.py`); the `nwp500.simulate` service walks a scenario on request
(`service.py`). Each heater's parameters are calibrated to its history by
the library (`calibration.py`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from ..const import (
    DATA_SIMULATOR,
    DOMAIN,
    simulator_device_options,
    simulator_stopped_signal,
)

# Imported with the package, in the import executor, rather than on the
# first call of the service, in the event loop.
from . import button as button
from . import service as service
from .calibration import CHECK_EVERY, CalibrationError, SimulatorCalibration
from .runner import SimulatorRunner
from .sensor import create_simulator_sensors

if TYPE_CHECKING:
    from ..coordinator import NWP500ConfigEntry, NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


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
        self.calibration = SimulatorCalibration(hass, entry.entry_id)
        self._started = dt_util.utcnow()
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> None:
        """Start a runner for each heater, on its calibration if it has one."""
        await self.calibration.async_load()
        self._started = dt_util.utcnow()
        for mac_address in self.coordinator.data or {}:
            runner = SimulatorRunner(
                self.hass,
                self.coordinator,
                mac_address,
                simulator_device_options(self.entry, mac_address),
            )
            runner.calibrated_params = self.calibration.params(mac_address)
            self.runners[mac_address] = runner
            runner.async_start()
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_calibrate_due, CHECK_EVERY
            )
        )

    async def async_calibrate(
        self, mac_address: str, *, from_defaults: bool = False
    ) -> dict[str, Any]:
        """Calibrate the heater's parameters, and walk again on them.

        Raises `CalibrationError` saying why it could not.
        """
        runner = self.runners.get(mac_address)
        if runner is None:
            raise CalibrationError("the simulator doesn't run for this heater")
        record = await self.calibration.async_calibrate(
            runner, from_defaults=from_defaults
        )
        runner.calibrated_params = self.calibration.params(mac_address)
        runner.async_refresh()
        return record

    @callback
    def _async_calibrate_due(self, now: datetime) -> None:
        """Calibrate, in the background, each heater that is due."""
        for mac_address in self.runners:
            if self.calibration.due(mac_address, now, self._started):
                self.entry.async_create_background_task(
                    self.hass,
                    self._async_calibrate_quietly(mac_address),
                    f"nwp500 simulator calibration {mac_address}",
                )

    async def _async_calibrate_quietly(self, mac_address: str) -> None:
        """A scheduled calibration: a failure is kept and logged, not raised."""
        try:
            await self.async_calibrate(mac_address)
        except CalibrationError as err:
            _LOGGER.info(
                "The simulator's calibration of %s was not made: %s",
                mac_address,
                err,
            )
        else:
            _LOGGER.info(
                "The simulator's calibration of %s is made", mac_address
            )

    async def async_stop(self) -> None:
        """Stop the runners, and tell whoever follows them."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.calibration.async_close()
        for runner in self.runners.values():
            runner.async_stop()
        async_dispatcher_send(
            self.hass, simulator_stopped_signal(self.entry.entry_id)
        )


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
