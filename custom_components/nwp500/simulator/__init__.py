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

import asyncio
import dataclasses
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from nwp500.sim.params import (
    HeaterParams,
    params_from_document,
    params_to_document,
)

from ..const import (
    CONF_SIMULATOR_HISTORY_DAYS,
    CONF_SIMULATOR_RECORD,
    DATA_SIMULATOR,
    DEFAULT_SIMULATOR_HISTORY_DAYS,
    DOMAIN,
    ISSUE_SIMULATOR_CALIBRATION_FAILING,
    simulator_calibration_issue_id,
    simulator_device_options,
    simulator_storage_key,
)

# Imported with the package, in the import executor, rather than on the
# first call of the service, or the platforms' adding, in the event loop.
from . import binary_sensor as binary_sensor
from . import service as service
from .calibration import (
    CALIBRATE_AT,
    FAILURES_REPORTED,
    CalibrationReport,
    async_calibrate,
    due,
)
from .location import (
    KEPT_REFIT,
    async_fit_location,
    model_from_document,
    model_to_document,
)
from .recorder import HistoryRecorder
from .runner import HeatingHistory, SimulatorRunner
from .sensor import create_simulator_sensors
from .water import (
    async_fit_book,
    water_terms_from_document,
    water_terms_to_document,
)

if TYPE_CHECKING:
    from nwp500.sim.location_air import LocationAirModel
    from nwp500.sim.params import BookParams, CirculationParams

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
        # The heaters whose history is recorded: each is opted in on its own.
        self.recorders: dict[str, HistoryRecorder] = {}
        # Each heater's last calibration, as stored: its parameters and
        # when they were fitted (`async_set_calibration`).
        self.calibrations: dict[str, dict[str, Any]] = {}
        # Each heater's last calibration run, fitted or not.
        self.reports: dict[str, CalibrationReport] = {}
        # Each heater's model of how the air beside it follows the outdoor
        # air, and when it was fitted (`async_set_location_air`).
        self.location_models: dict[str, dict[str, Any]] = {}
        # Each heater's book and circulation loop terms, and the meter and
        # pump they were fitted from (`async_set_water_terms`).
        self.water_models: dict[str, dict[str, Any]] = {}
        #: The heater being calibrated now, if any: one at a time.
        self.calibrating: str | None = None
        self._calibration_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._removed = False
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, simulator_storage_key(entry.entry_id)
        )

    async def async_start(self) -> None:
        """Start a runner for each heater."""
        stored = await self._store.async_load() or {}
        histories = stored.get("history") or {}
        calibrations = stored.get("calibration") or {}
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
            stored_calibration = calibrations.get(mac_address)
            if not isinstance(stored_calibration, dict):
                stored_calibration = {}
            if (params := _stored_params(stored_calibration)) is not None:
                self.calibrations[mac_address] = {
                    "params": stored_calibration["params"],
                    "fitted_at": stored_calibration.get("fitted_at"),
                }
                runner.params = params
            stored_location = stored_calibration.get("location_air")
            if (
                isinstance(stored_location, dict)
                # Fitted to these inputs: another pair is another model.
                and stored_location.get("air_entity") == runner.entities.air
                and stored_location.get("weather_entity")
                == runner.entities.weather
                and (model := model_from_document(stored_location))
            ):
                self.location_models[mac_address] = dict(stored_location)
                runner.location_air = model
            stored_water = stored_calibration.get("water")
            water = runner.water_entities
            if (
                isinstance(stored_water, dict)
                # Fitted to this meter and pump: another is another fit.
                and stored_water.get("meter_entity") == water.meter_volume
                and stored_water.get("pump_entity") == water.circulation_speed
                and (
                    terms := water_terms_from_document(
                        stored_water.get("params")
                    )
                )
            ):
                self.water_models[mac_address] = dict(stored_water)
                runner.book, runner.circulation = terms
            if report := CalibrationReport.from_document(
                stored_calibration.get("report")
            ):
                self.reports[mac_address] = report
                # Repairs issues don't outlive a restart: the saved
                # failures raise theirs again.
                self._report_issue(mac_address, report)
            runner.async_start()
            if runner.options.get(CONF_SIMULATOR_RECORD) is True:
                recorder = HistoryRecorder(
                    self.hass,
                    self.coordinator,
                    mac_address,
                    int(
                        runner.options.get(
                            CONF_SIMULATOR_HISTORY_DAYS,
                            DEFAULT_SIMULATOR_HISTORY_DAYS,
                        )
                    ),
                )
                self.recorders[mac_address] = recorder
                recorder.async_start()
        if self.runners:
            self._unsubs.append(
                async_track_time_change(
                    self.hass,
                    self._on_night,
                    hour=CALIBRATE_AT.hour,
                    minute=CALIBRATE_AT.minute,
                    second=0,
                )
            )

    async def async_stop(self) -> None:
        """Stop the runners and save what they saw."""
        self._stop_calibrating()
        for runner in self.runners.values():
            runner.async_stop()
        await self._async_stop_recorders()
        if not self._removed:
            await self._store.async_save(self._document())

    async def async_remove(self) -> None:
        """Stop, and remove what the simulator stored and created.

        A recorded history is kept: it is deleted only when the owner says
        so (the options flow) or with the entry.
        """
        self._removed = True
        self._stop_calibrating()
        for runner in self.runners.values():
            runner.async_stop()
        await self._async_stop_recorders()
        await self._store.async_remove()

    def _stop_calibrating(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        # A fit already in the executor runs to its end; its answer is
        # dropped with its task.
        for task in self._tasks:
            task.cancel()

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `listener` when a calibration starts or ends."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def async_request_calibration(self, mac_address: str) -> None:
        """Calibrate a heater now, in the background."""
        if mac_address not in self.runners:
            return
        self._spawn(self.async_calibrate(mac_address))

    def _spawn(self, coro: Any) -> None:
        task = self.entry.async_create_background_task(
            self.hass, coro, f"{DOMAIN} simulator calibration"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _on_night(self, _now: datetime) -> None:
        self._spawn(self._async_calibrate_due())

    async def _async_calibrate_due(self) -> None:
        now = dt_util.utcnow()
        for mac_address in list(self.runners):
            if due(self.reports.get(mac_address), now):
                await self.async_calibrate(mac_address)

    async def async_calibrate(
        self, mac_address: str
    ) -> CalibrationReport | None:
        """Calibrate one heater, after any other's calibration."""
        async with self._calibration_lock:
            runner = self.runners.get(mac_address)
            if runner is None or self._removed:
                return None
            self.calibrating = mac_address
            self._notify()
            try:
                report = await async_calibrate(
                    runner,
                    self.recorders.get(mac_address),
                    self.reports.get(mac_address),
                    lambda params, at: self.async_set_calibration(
                        mac_address, params, at
                    ),
                )
                location = await async_fit_location(
                    self.hass, runner, report.at
                )
                if location is not None:
                    report.location_air = location.as_document()
                    if location.kept == KEPT_REFIT and location.model:
                        self.async_set_location_air(
                            mac_address, location.model, report.at
                        )
                book = await async_fit_book(self.hass, runner, report.at)
                if book is not None:
                    report.book = book.as_document()
                    if book.kept == "refit":
                        self.async_set_water_terms(
                            mac_address, book.book, book.circulation, report.at
                        )
            finally:
                self.calibrating = None
                self._notify()
            self.reports[mac_address] = report
            self._report_issue(mac_address, report)
            self._schedule_save()
            self._notify()
            return report

    def _report_issue(
        self, mac_address: str, report: CalibrationReport
    ) -> None:
        issue_id = simulator_calibration_issue_id(
            self.entry.entry_id, mac_address
        )
        if report.failures < FAILURES_REPORTED:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        device = self.coordinator.data.get(mac_address, {}).get("device")
        name = getattr(
            getattr(device, "device_info", None), "device_name", None
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SIMULATOR_CALIBRATION_FAILING,
            translation_placeholders={
                "device": name or "the water heater",
                "runs": str(report.failures),
                "reason": report.error or "no term could be fitted",
            },
        )

    async def _async_stop_recorders(self) -> None:
        for recorder in self.recorders.values():
            await recorder.async_stop()
        self.recorders.clear()

    @callback
    def _schedule_save(self) -> None:
        if self._removed:
            return
        self._store.async_delay_save(self._document, SAVE_DELAY_S)

    @callback
    def async_set_calibration(
        self,
        mac_address: str,
        params: HeaterParams,
        fitted_at: datetime,
    ) -> None:
        """Walk the heater with `params` from now on, and keep them."""
        runner = self.runners.get(mac_address)
        if runner is None:
            return
        self.calibrations[mac_address] = {
            "params": params_to_document(params),
            "fitted_at": fitted_at.isoformat(),
        }
        runner.async_set_params(params)
        self._schedule_save()

    @callback
    def async_set_location_air(
        self,
        mac_address: str,
        model: LocationAirModel,
        fitted_at: datetime,
    ) -> None:
        """Project the heater's air by `model` from now on, and keep it."""
        runner = self.runners.get(mac_address)
        if runner is None:
            return
        self.location_models[mac_address] = {
            **model_to_document(model),
            "air_entity": runner.entities.air,
            "weather_entity": runner.entities.weather,
            "fitted_at": fitted_at.isoformat(),
        }
        runner.async_set_location_air(model)
        self._schedule_save()

    @callback
    def async_set_water_terms(
        self,
        mac_address: str,
        book: BookParams,
        circulation: CirculationParams | None,
        fitted_at: datetime,
    ) -> None:
        """Book the heater's water by these terms from now on; keep them."""
        runner = self.runners.get(mac_address)
        if runner is None:
            return
        feature = self.coordinator.device_features.get(mac_address)
        if feature is None:
            return
        water = runner.water_entities
        params = dataclasses.replace(
            runner.walk_params(feature), book=book, circulation=circulation
        )
        self.water_models[mac_address] = {
            "params": water_terms_to_document(params),
            "meter_entity": water.meter_volume,
            "pump_entity": water.circulation_speed,
            "fitted_at": fitted_at.isoformat(),
        }
        runner.async_set_water_terms(book, circulation)
        self._schedule_save()

    def _document(self) -> dict[str, Any]:
        calibration: dict[str, dict[str, Any]] = {
            mac: dict(doc) for mac, doc in self.calibrations.items()
        }
        for mac, doc in self.location_models.items():
            calibration.setdefault(mac, {})["location_air"] = dict(doc)
        for mac, doc in self.water_models.items():
            calibration.setdefault(mac, {})["water"] = dict(doc)
        for mac, report in self.reports.items():
            calibration.setdefault(mac, {})["report"] = report.as_document()
        return {
            "history": {
                mac: runner.history.as_document()
                for mac, runner in self.runners.items()
            },
            "calibration": calibration,
        }


def _stored_params(doc: Any) -> HeaterParams | None:
    """A stored calibration's parameters; None for none, or unreadable."""
    if not isinstance(doc, dict) or not isinstance(doc.get("params"), dict):
        return None
    try:
        return params_from_document(doc["params"])
    except (KeyError, TypeError, ValueError) as err:
        _LOGGER.warning("A stored calibration is not read: %s", err)
        return None


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
