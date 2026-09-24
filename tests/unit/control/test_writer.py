"""The live writer: the one path from the feature to the heater (5.4)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.nwp500.control.writer import CoordinatorWriter

MAC = "AA:BB:CC:DD:EE:FF"
ENTRY = {"enable": 2, "week": 8, "hour": 10, "min": 29, "mode": 1, "param": 121}
SCHEDULE = {"reservation_use": 2, "reservation": [ENTRY]}
TARGET = "custom_components.nwp500.control.writer.update_reservations_confirmed"


def _coordinator(unit_system: str = "us_customary") -> MagicMock:
    coordinator = MagicMock()
    coordinator._reservation_lock = asyncio.Lock()
    coordinator.mqtt_manager.mqtt_client = MagicMock(name="client")
    coordinator._devices_by_mac = {MAC: MagicMock(name="device")}
    coordinator.reservation_schedules = {
        MAC: {"reservation_use": 1, "reservation": [], "other": "kept"}
    }
    coordinator.async_fetch_reservations = AsyncMock(return_value=None)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.unit_system = unit_system

    @asynccontextmanager
    async def guard(action):
        yield

    coordinator.unit_transition_guard = guard
    return coordinator


@pytest.mark.asyncio
async def test_a_confirmed_write_updates_the_coordinator_copy():
    coordinator = _coordinator()
    writer = CoordinatorWriter(coordinator, MAC)
    with patch(TARGET, AsyncMock(return_value=MagicMock())) as confirmed:
        result = await writer.async_write(SCHEDULE)

    confirmed.assert_awaited_once()
    args, kwargs = confirmed.await_args
    assert args[0] is coordinator.mqtt_manager.mqtt_client
    assert args[1] is coordinator._devices_by_mac[MAC]
    assert args[2] == [ENTRY]
    assert kwargs == {"enabled": True}
    assert result == {**SCHEDULE, "other": "kept"}
    assert coordinator.reservation_schedules[MAC] == result
    coordinator.async_fetch_reservations.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_an_echo_a_fresh_read_says_what_the_device_holds():
    coordinator = _coordinator()
    coordinator.async_fetch_reservations.return_value = {
        "reservation_use": 1,
        "reservation": [],
    }
    writer = CoordinatorWriter(coordinator, MAC)
    with patch(TARGET, AsyncMock(return_value=None)):
        result = await writer.async_write(SCHEDULE)

    assert result == {"reservation_use": 1, "reservation": []}
    coordinator.async_fetch_reservations.assert_awaited_once_with(MAC)


def test_locked_is_the_reservation_services_lock():
    coordinator = _coordinator()
    writer = CoordinatorWriter(coordinator, MAC)
    assert writer.locked() is coordinator._reservation_lock


@pytest.mark.asyncio
async def test_nothing_is_sent_without_a_session_or_device():
    coordinator = _coordinator()
    coordinator.mqtt_manager = None
    writer = CoordinatorWriter(coordinator, MAC)
    with patch(TARGET, AsyncMock()) as confirmed:
        assert await writer.async_write(SCHEDULE) is None
    confirmed.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_waits_for_the_device():
    coordinator = _coordinator()
    coordinator.async_fetch_reservations.return_value = SCHEDULE
    writer = CoordinatorWriter(coordinator, MAC)
    assert await writer.async_read() == SCHEDULE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unit_system", "temperature"),
    [("us_customary", 140.0), ("metric", 60.0)],
)
async def test_restore_state_writes_mode_then_setpoint(
    unit_system, temperature
):
    coordinator = _coordinator(unit_system)
    writer = CoordinatorWriter(coordinator, MAC)

    assert await writer.async_restore_state("energy_saver", 120) is True

    calls = coordinator.async_control_device.await_args_list
    assert calls[0].args == (MAC, "set_dhw_mode")
    assert calls[0].kwargs == {"mode": 3}
    assert calls[1].args == (MAC, "set_temperature")
    assert calls[1].kwargs["temperature"] == pytest.approx(temperature)


@pytest.mark.asyncio
async def test_a_refused_direct_write_is_reported():
    coordinator = _coordinator()
    coordinator.async_control_device.return_value = False
    writer = CoordinatorWriter(coordinator, MAC)
    assert await writer.async_restore_state("heat_pump", 120) is False
