"""Writing to the heater: the one narrow path live mode uses.

Spec sections 5.4 and 6.6 of issue #158. Everything live mode sends to the
heater goes through a `ListWriter`, so tests can put a simulated heater in
its place and nothing else in the feature can reach the device.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from nwp500.reservations import update_reservations_confirmed
from nwp500.temperature import HalfCelsius

from ..const import MODE_TO_DHW_ID
from .observed import DEVICE_BOOL_ON

if TYPE_CHECKING:
    from ..coordinator import NWP500DataUpdateCoordinator


class ListWriter(Protocol):
    """What live mode needs from the heater."""

    async def async_read(self) -> dict[str, Any] | None:
        """A fresh read of the reservation list, or None if none came."""
        ...

    async def async_write(
        self, schedule: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Write the whole list; return a fresh read of it afterwards.

        None if the write could not be sent or no read came back. The caller
        compares the read with what it wrote: a write counts only once the
        heater holds the new list.
        """
        ...

    async def async_restore_state(self, mode: str, setpoint_raw: int) -> bool:
        """Disabling's one direct write: the owner's mode and setpoint."""
        ...


class CoordinatorWriter:
    """Writes through the integration's own coordinator and MQTT session."""

    def __init__(
        self, coordinator: NWP500DataUpdateCoordinator, mac_address: str
    ) -> None:
        """Bind to one heater."""
        self.coordinator = coordinator
        self.mac_address = mac_address

    def _lock(self) -> asyncio.Lock:
        # The lock the integration's reservation services hold, so a write
        # from the feature and one from a service cannot interleave.
        lock: asyncio.Lock = self.coordinator._reservation_lock  # noqa: SLF001
        return lock

    async def async_read(self) -> dict[str, Any] | None:
        """Ask the heater for its list and wait for the reply."""
        return await self.coordinator.async_fetch_reservations(self.mac_address)

    async def async_write(
        self, schedule: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Write the whole list with the library's confirmed write.

        The library resolves only on a device echo that matches what was
        written. Without one, a fresh read says what the device holds: the
        write may have landed without an echo, or been lost (section 8).
        """
        manager = self.coordinator.mqtt_manager
        client = manager.mqtt_client if manager is not None else None
        device = self.coordinator._devices_by_mac.get(  # noqa: SLF001
            self.mac_address
        )
        if client is None or device is None:
            return None
        entries = [dict(e) for e in schedule["reservation"]]
        enabled = schedule["reservation_use"] == DEVICE_BOOL_ON
        async with self._lock():
            confirmed = await update_reservations_confirmed(
                client, device, entries, enabled=enabled
            )
            if confirmed is None:
                return await self.coordinator.async_fetch_reservations(
                    self.mac_address
                )
        # The device holds exactly this list. The coordinator's copy is
        # updated now rather than when the echo reaches it, so the next
        # pass does not read the list from before the write.
        written = {
            **(
                self.coordinator.reservation_schedules.get(self.mac_address)
                or {}
            ),
            "reservation_use": schedule["reservation_use"],
            "reservation": entries,
        }
        self.coordinator.reservation_schedules[self.mac_address] = written
        return written

    async def async_restore_state(self, mode: str, setpoint_raw: int) -> bool:
        """Write the owner's mode, then setpoint, in Home Assistant's unit.

        The setpoint goes through the same `set_temperature` dispatch as the
        water heater entity, which the library converts from the unit
        system in force; the guard keeps that unit from changing meanwhile.
        """
        mode_sent = await self.coordinator.async_control_device(
            self.mac_address, "set_dhw_mode", mode=MODE_TO_DHW_ID[mode]
        )
        async with self.coordinator.unit_transition_guard(
            "restore the owner's setpoint"
        ):
            celsius = self.coordinator.unit_system == "metric"
            setpoint_sent = await self.coordinator.async_control_device(
                self.mac_address,
                "set_temperature",
                temperature=HalfCelsius(setpoint_raw).to_preferred(celsius),
            )
        return bool(mode_sent and setpoint_sent)
