"""The live writer: the one path from the feature to the heater (5.4)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.nwp500.control.writer import CoordinatorWriter

MAC = "AA:BB:CC:DD:EE:FF"
ENTRY = {"enable": 2, "week": 8, "hour": 10, "min": 29, "mode": 1, "param": 121}
SCHEDULE = {"reservation_use": 2, "reservation": [ENTRY]}


def _coordinator(unit_system: str = "us_customary") -> MagicMock:
    coordinator = MagicMock()
    coordinator._reservation_lock = asyncio.Lock()
    coordinator.mqtt_manager.mqtt_client = MagicMock(name="client")
    coordinator._devices_by_mac = {MAC: MagicMock(name="device")}
    coordinator.reservation_schedules = {
        MAC: {"reservation_use": 1, "reservation": [], "other": "kept"}
    }
    coordinator.reservation_schedules_read_at = {}
    coordinator.async_fetch_reservations = AsyncMock(return_value=None)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.unit_system = unit_system

    @asynccontextmanager
    async def guard(action):
        yield

    coordinator.unit_transition_guard = guard
    return coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize(("use", "enabled"), [(2, True), (1, False)])
async def test_a_write_is_the_coordinators_confirmed_write(use, enabled):
    """What the device holds afterwards comes back as the coordinator says."""
    coordinator = _coordinator()
    held = {"reservation_use": use, "reservation": [ENTRY]}
    coordinator.async_write_reservations = AsyncMock(return_value=held)
    writer = CoordinatorWriter(coordinator, MAC)

    result = await writer.async_write(
        {"reservation_use": use, "reservation": [ENTRY]}
    )

    assert result is held
    coordinator.async_write_reservations.assert_awaited_once_with(
        MAC, [ENTRY], enabled=enabled
    )


@pytest.mark.asyncio
async def test_no_confirmation_comes_back_as_none():
    coordinator = _coordinator()
    coordinator.async_write_reservations = AsyncMock(return_value=None)
    writer = CoordinatorWriter(coordinator, MAC)
    assert await writer.async_write(SCHEDULE) is None


def test_locked_is_the_reservation_services_lock():
    coordinator = _coordinator()
    writer = CoordinatorWriter(coordinator, MAC)
    assert writer.locked() is coordinator._reservation_lock


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


# -- device commands (section 3.7) -----------------------------------------


def _command(raw):
    from custom_components.nwp500.control.intent import Command

    raw = dict(raw)
    name = raw.pop("command")
    return Command(id="c", command=name, params=raw)


@pytest.mark.parametrize(
    ("raw", "method", "args"),
    [
        ({"command": "vacation", "days": 5}, "set_vacation_days", (5,)),
        ({"command": "power", "on": False}, "set_power", (False,)),
        ({"command": "power", "on": True}, "set_power", (True,)),
        (
            {"command": "anti_legionella", "enabled": True, "period_days": 7},
            "enable_anti_legionella",
            (7,),
        ),
        (
            {"command": "anti_legionella", "enabled": False},
            "disable_anti_legionella",
            (),
        ),
        ({"command": "tou", "enabled": True}, "set_tou_enabled", (True,)),
        ({"command": "tou", "enabled": False}, "set_tou_enabled", (False,)),
        (
            {"command": "demand_response", "enabled": True},
            "enable_demand_response",
            (),
        ),
        (
            {"command": "demand_response", "enabled": False},
            "disable_demand_response",
            (),
        ),
    ],
)
@pytest.mark.asyncio
async def test_each_command_makes_its_one_library_call(raw, method, args):
    coordinator = _coordinator()
    client = MagicMock(name="client")
    for name in (
        "set_vacation_days",
        "set_power",
        "enable_anti_legionella",
        "disable_anti_legionella",
        "set_tou_enabled",
        "enable_demand_response",
        "disable_demand_response",
    ):
        setattr(client, name, AsyncMock(name=name))
    coordinator.mqtt_manager.mqtt_client = client
    writer = CoordinatorWriter(coordinator, MAC)

    await writer.async_send_command(_command(raw))

    device = coordinator._devices_by_mac[MAC]
    getattr(client, method).assert_awaited_once_with(device, *args)
    called = [
        name
        for name in vars(client)
        if isinstance(getattr(client, name), AsyncMock)
        and getattr(client, name).await_count
    ]
    assert called == [method]


@pytest.mark.asyncio
async def test_the_library_error_is_raised():
    coordinator = _coordinator()
    client = MagicMock(name="client")
    client.set_vacation_days = AsyncMock(side_effect=ValueError("1-30"))
    coordinator.mqtt_manager.mqtt_client = client
    writer = CoordinatorWriter(coordinator, MAC)

    with pytest.raises(ValueError, match="1-30"):
        await writer.async_send_command(
            _command({"command": "vacation", "days": 45})
        )


@pytest.mark.asyncio
async def test_without_a_session_a_command_is_not_sent():
    coordinator = _coordinator()
    coordinator.mqtt_manager = None
    writer = CoordinatorWriter(coordinator, MAC)

    with pytest.raises(ConnectionError):
        await writer.async_send_command(
            _command({"command": "tou", "enabled": True})
        )
