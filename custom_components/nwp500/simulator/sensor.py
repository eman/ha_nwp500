"""The simulator's sensors: what the next day's simulation says.

And the hot water the tank holds now, as `nwp500.sim.time_to_ready` says.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import callback

from ..entity import NWP500Entity
from .runner import (
    HEATING_STATES,
    STATE_HEATING,
    STOP_HORIZON,
    Answer,
    SimulatorRunner,
)

if TYPE_CHECKING:
    from . import SimulatorFeature

# Long: kept off the recorder.
_UNRECORDED = frozenset({"reasons", "defaults_used", "notes"})

# The energies, to a tenth of a kWh: a walk a minute moves them by Wh, and
# each change is a row in the recorder.
_KWH_DIGITS = 1


def _kwh(value: float | None) -> float | None:
    return None if value is None else round(value, _KWH_DIGITS)


#: Litres in a US gallon: the library answers in gallons.
_L_PER_GAL = 3.785411784


def _minute(at: datetime | None) -> datetime | None:
    """`at` to the minute.

    A walk that moves it by seconds then writes no new state.
    """
    return None if at is None else at.replace(second=0, microsecond=0)


class NWP500SimulatorEntity(NWP500Entity):
    """An entity that reports a heater's simulation.

    Available while the heater reports and the simulation has an answer.
    """

    _unrecorded_attributes = _UNRECORDED

    def __init__(self, runner: SimulatorRunner, key: str) -> None:
        """Bind to the heater's runner."""
        device = runner.coordinator.data[runner.mac_address]["device"]
        super().__init__(runner.coordinator, runner.mac_address, device)
        self.runner = runner
        self._attr_unique_id = f"{runner.mac_address}_simulator_{key}"
        self._attr_translation_key = f"simulator_{key}"

    @property
    def answer(self) -> Answer | None:
        """The runner's latest answer."""
        return self.runner.answer

    @property
    @override
    def available(self) -> bool:
        """Whether the heater reports and the simulation has an answer."""
        answer = self.answer
        return answer is not None and answer.ok and super().available

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the runner as well as the coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runner.async_add_listener(self._handle_answer)
        )

    @callback
    def _handle_answer(self) -> None:
        self.async_write_ha_state()

    def _common_attributes(self) -> dict[str, Any]:
        # Nothing here changes from one walk to the next unless the answer
        # does, so an unchanged answer writes no new state; when it was
        # computed is in the diagnostics.
        answer = self.answer
        if answer is None:
            return {}
        return {
            "reasons": list(answer.reasons),
            "defaults_used": list(answer.defaults_used),
            "notes": list(answer.notes),
        }


class SimulatorHeatingSensor(NWP500SimulatorEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """Whether the tank is heating, will heat within the day, or not."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(HEATING_STATES)
    _attr_icon = "mdi:water-boiler"

    @property
    def native_value(self) -> str | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """heating, scheduled, idle or disabled."""
        answer = self.answer
        return None if answer is None else answer.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The next heating interval, and what the answer rests on."""
        answer = self.answer
        attributes = self._common_attributes()
        if answer is None or not answer.ok:
            return attributes
        # Heating now, the interval "starts" at each walk's start: not news.
        start = None if answer.state == STATE_HEATING else _minute(answer.start)
        return {
            **attributes,
            "starts_at": None if start is None else start.isoformat(),
            "source": answer.source,
            "cause": answer.cause,
            "trigger": answer.trigger,
            "energy_kwh": _kwh(answer.energy_kwh),
            "tou_capped": answer.capped,
            "confidence": answer.confidence,
        }


class SimulatorHotWaterReadySensor(NWP500SimulatorEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """When the tank next finishes heating.

    The end of the heating it is in, or of the next one within the day,
    as the library's `RecoveryResult.ready_at` gives it; `stop` says what
    ended it. Unknown when there is none within the day, or when the
    day runs out before it ends (`stop` is `horizon`).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The end of the next heating interval."""
        answer = self.answer
        # Cut by the walk's horizon, the heating has no end yet: the
        # horizon is not when the tank is ready.
        if answer is None or answer.stop == STOP_HORIZON:
            return None
        return _minute(answer.end)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Why the heating ends, and what the answer rests on."""
        attributes = self._common_attributes()
        answer = self.answer
        if answer is None or not answer.ok:
            return attributes
        return {**attributes, "stop": answer.stop}


class SimulatorCompressorStartSensor(NWP500SimulatorEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """When the heat pump's compressor next starts, as modelled.

    The first compressor start the walk finds within the day. Unknown
    while the compressor runs (Simulated hot water ready is then when it
    stops) and when no start falls within the day, such as in Electric
    or Vacation.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:heat-pump-outline"

    @property
    def native_value(self) -> datetime | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The compressor's next start."""
        answer = self.answer
        return None if answer is None else _minute(answer.compressor_start)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Whether it runs now, what starts it, and what the answer rests on."""
        attributes = self._common_attributes()
        answer = self.answer
        if answer is None or not answer.ok:
            return attributes
        return {
            **attributes,
            "running": answer.compressor_running,
            "cause": answer.compressor_cause,
            "trigger": answer.compressor_trigger,
        }


class SimulatorEnergySensor(NWP500SimulatorEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The electricity the heater is expected to use over the next day.

    A forecast, not a meter: it has no state class, so it never enters
    long-term statistics or the energy dashboard.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = _KWH_DIGITS
    _attr_icon = "mdi:lightning-bolt-outline"

    @property
    def native_value(self) -> float | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """The kWh over the next 24 hours; unknown when unpriced."""
        answer = self.answer
        return None if answer is None else _kwh(answer.energy_total_kwh)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """What the answer rests on."""
        return self._common_attributes()


class NWP500HotWaterEntity(NWP500SimulatorEntity):
    """An entity that reports the hot water the tank holds now.

    From the heater's own state, and from each optional input given; its
    reasons say what the answer rests on.
    """

    @property
    @override
    def available(self) -> bool:
        """Whether the walk has a hot water answer."""
        answer = self.answer
        return (
            super().available
            and answer is not None
            and answer.readiness is not None
        )

    def _readiness_attributes(self) -> dict[str, Any]:
        answer = self.answer
        readiness = None if answer is None else answer.readiness
        if answer is None or readiness is None:
            return {}
        return {
            "reasons": list(readiness.reasons),
            "defaults_used": list(readiness.defaults_used),
            "notes": list(answer.notes),
        }


class SimulatorHotWaterAvailableSensor(NWP500HotWaterEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """The hot water the tank can deliver now, at the delivered line."""

    _attr_device_class = SensorDeviceClass.VOLUME_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:water-thermometer"

    def __init__(self, runner: SimulatorRunner, key: str) -> None:
        """In Home Assistant's volume unit, gallons or litres."""
        super().__init__(runner, key)
        self._litres = (
            runner.hass.config.units.volume_unit != UnitOfVolume.GALLONS
        )
        self._attr_native_unit_of_measurement = (
            UnitOfVolume.LITERS if self._litres else UnitOfVolume.GALLONS
        )

    @property
    def native_value(self) -> int | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Whole units: a walk a minute moves the tank's by fractions."""
        answer = self.answer
        readiness = None if answer is None else answer.readiness
        if readiness is None:
            return None
        gal = readiness.deliverable_gal
        return round(gal * _L_PER_GAL if self._litres else gal)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """What the answer rests on."""
        return self._readiness_attributes()


class SimulatorShowersReadySensor(NWP500HotWaterEntity, SensorEntity):  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
    """How many showers the tank has hot water for now: whole ones."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:shower-head"

    @property
    def native_value(self) -> int | None:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """Whole showers: one the tank can't finish is not counted."""
        answer = self.answer
        readiness = None if answer is None else answer.readiness
        return None if readiness is None else readiness.showers.ready_for_whole

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # type: ignore[reportIncompatibleVariableOverride,unused-ignore]
        """A shower's size, and what the answer rests on."""
        answer = self.answer
        readiness = None if answer is None else answer.readiness
        attributes = self._readiness_attributes()
        if readiness is not None:
            attributes["shower_gal"] = readiness.showers.volume_gal
        return attributes


def create_simulator_sensors(feature: SimulatorFeature) -> list[SensorEntity]:
    """The sensors for each heater the simulator runs for."""
    entities: list[SensorEntity] = []
    for runner in feature.runners.values():
        entities.extend(
            [
                SimulatorHeatingSensor(runner, "heating"),
                SimulatorHotWaterReadySensor(runner, "hot_water_ready"),
                SimulatorCompressorStartSensor(runner, "compressor_start"),
                SimulatorEnergySensor(runner, "energy_next_24h"),
                SimulatorHotWaterAvailableSensor(runner, "hot_water_available"),
                SimulatorShowersReadySensor(runner, "showers_ready"),
            ]
        )
    return entities
