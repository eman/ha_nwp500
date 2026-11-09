"""The heater's own record: its statuses and its inputs, kept for weeks.

These write real files, in each test's own config directory, and read them
back as the library's calibration and its input reader take them.
"""

from __future__ import annotations

import functools
import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from nwp500.models import DeviceStatus
from nwp500.sim_home_assistant import Entities, record_inputs

from custom_components.nwp500.const import simulator_record_directory
from custom_components.nwp500.mqtt_manager import _RawStatusHandler
from custom_components.nwp500.simulator.ha_client import RecordClient
from custom_components.nwp500.simulator.record import (
    FLUSH_EVERY,
    KEEP,
    HeaterRecord,
)

from .conftest import MAC, status_dict

AIR = "sensor.air"
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _record(hass: HomeAssistant, inputs: tuple[str, ...] = ()) -> HeaterRecord:
    return HeaterRecord(hass, "entry1", MAC, inputs)


def _files(hass: HomeAssistant, series: str) -> list[str]:
    directory = (
        simulator_record_directory(hass, "entry1") / "aabbccddeeff" / series
    )
    return sorted(p.name for p in directory.glob("*.jsonl.gz"))


async def test_statuses_are_kept_as_sent_and_read_back(hass: HomeAssistant):
    record = _record(hass)
    await record.async_load()
    assert record.since is None

    sent = status_dict(macAddress=MAC)
    record.add_status(T0, sent)
    record.add_status(T0 + timedelta(minutes=1), status_dict(dhwUse=2))
    assert record.since == T0
    await record.async_flush(T0 + FLUSH_EVERY)

    readings = await hass.async_add_executor_job(
        lambda: list(record.readings(T0))
    )
    assert [at for at, _ in readings] == [T0, T0 + timedelta(minutes=1)]
    assert readings[0][1] == DeviceStatus.model_validate(status_dict())
    # Without the heater's address, as nwp-cli simulate record writes it.
    path = (
        simulator_record_directory(hass, "entry1")
        / "aabbccddeeff"
        / "statuses"
        / "2026-10-01.jsonl.gz"
    )
    with gzip.open(path, "rt") as f:
        assert "macAddress" not in f.read()


async def test_each_day_has_its_file_and_writes_append(hass: HomeAssistant):
    record = _record(hass)
    late = datetime(2026, 10, 1, 23, 59, tzinfo=UTC)
    record.add_status(late, status_dict())
    record.add_status(late + timedelta(minutes=2), status_dict())
    await record.async_flush(late + timedelta(minutes=3))
    record.add_status(late + timedelta(minutes=4), status_dict())
    await record.async_flush(late + timedelta(minutes=5))

    assert _files(hass, "statuses") == [
        "2026-10-01.jsonl.gz",
        "2026-10-02.jsonl.gz",
    ]
    readings = await hass.async_add_executor_job(
        lambda: list(record.readings(late))
    )
    assert len(readings) == 3
    # A later start reads from its own day on.
    later = await hass.async_add_executor_job(
        lambda: list(record.readings(late + timedelta(minutes=3)))
    )
    assert len(later) == 1


async def test_a_restart_finds_where_the_record_begins(hass: HomeAssistant):
    record = _record(hass)
    record.add_status(T0, status_dict())
    await record.async_close(T0 + FLUSH_EVERY)

    again = _record(hass)
    await again.async_load()
    assert again.since == T0


async def test_a_damaged_write_loses_only_itself(hass: HomeAssistant):
    record = _record(hass)
    record.add_status(T0, status_dict())
    await record.async_flush(T0 + FLUSH_EVERY)
    path: Path = (
        simulator_record_directory(hass, "entry1")
        / "aabbccddeeff"
        / "statuses"
        / "2026-10-01.jsonl.gz"
    )
    # A write cut short: half a member.
    member = gzip.compress(
        b'{"t": "2026-10-01T13:00:00+00:00", "status": {}}\n'
    )
    with path.open("ab") as f:
        f.write(member[: len(member) // 2])
    # And a line that is not a status.
    with gzip.open(path.with_name("2026-10-02.jsonl.gz"), "at") as f:
        f.write("not json\n")
    record.add_status(T0 + timedelta(days=1, hours=1), status_dict())
    await record.async_flush(T0 + timedelta(days=1, hours=2))

    readings = await hass.async_add_executor_job(
        lambda: list(record.readings(T0))
    )
    assert [at for at, _ in readings] == [T0, T0 + timedelta(days=1, hours=1)]


async def test_days_past_keeping_are_dropped(hass: HomeAssistant):
    record = _record(hass)
    record.add_status(T0, status_dict())
    record.add_status(T0 + timedelta(days=2), status_dict())
    await record.async_flush(T0 + timedelta(days=2))
    assert record.since == T0

    await record.async_flush(T0 + KEEP + timedelta(days=1))
    assert _files(hass, "statuses") == ["2026-10-03.jsonl.gz"]
    assert record.since == T0 + timedelta(days=2)


async def test_nothing_is_written_once_closed(hass: HomeAssistant):
    record = _record(hass)
    await record.async_close(T0)
    record.add_status(T0, status_dict())
    await record.async_flush(T0 + FLUSH_EVERY)
    assert _files(hass, "statuses") == []


async def test_the_inputs_read_as_the_recorders_history(
    hass: HomeAssistant, freezer
):
    """Through the library's own reader, as the recorder's would be."""
    record = _record(hass, (AIR,))
    freezer.move_to(T0 - timedelta(hours=1))
    hass.states.async_set(AIR, "61.0", {"unit_of_measurement": "°F"})
    # The day's file opens with the state in force, from before it.
    freezer.move_to(T0)
    await record.async_flush(T0)
    for minutes, value in ((10, "62.0"), (20, "unavailable"), (30, "64.0")):
        freezer.move_to(T0 + timedelta(minutes=minutes))
        hass.states.async_set(AIR, value, {"unit_of_measurement": "°F"})
        state = hass.states.get(AIR)
        assert state is not None
        record.add_state(state)
    await record.async_flush(T0 + timedelta(hours=1))

    got = await record_inputs(
        RecordClient(hass, record),  # type: ignore[arg-type]
        Entities(air=AIR),
        T0,
        T0 + timedelta(hours=1),
    )
    assert got.air is not None
    assert [(at - T0, value) for at, value in got.air] == [
        (timedelta(0), 61.0),
        (timedelta(minutes=10), 62.0),
        (timedelta(minutes=30), 64.0),
    ]
    assert got.air_outages == (
        (T0 + timedelta(minutes=20), T0 + timedelta(minutes=30)),
    )


async def test_the_raw_handler_passes_on_statuses_only():
    got: list[Any] = []
    handler = _RawStatusHandler(MAC, lambda *args: got.append(args))

    handler("cmd/52/navilink-x/st", {"response": {"status": {"dhwUse": 1}}})
    handler("cmd/52/navilink-x/did", {"response": {"feature": {"a": 1}}})

    ((mac, at, payload),) = got
    assert mac == MAC
    assert payload == {"dhwUse": 1}
    assert abs(at - dt_util.utcnow()) < timedelta(seconds=5)
    # Equal for the same heater: a resubscription registers it once.
    assert handler == _RawStatusHandler(MAC, handler.callback)


async def test_the_coordinator_hands_raw_statuses_to_the_loop(
    hass: HomeAssistant,
):
    from custom_components.nwp500.coordinator import (
        NWP500DataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator._raw_status_listeners = {}  # noqa: SLF001
    got: list[Any] = []
    remove = NWP500DataUpdateCoordinator.async_add_raw_status_listener(
        coordinator, MAC, lambda at, payload: got.append((at, payload))
    )
    coordinator._dispatch_raw_status = functools.partial(  # noqa: SLF001
        NWP500DataUpdateCoordinator._dispatch_raw_status,  # noqa: SLF001
        coordinator,
    )

    await hass.async_add_executor_job(
        NWP500DataUpdateCoordinator._on_raw_status,  # noqa: SLF001
        coordinator,
        MAC,
        T0,
        {"dhwUse": 1},
    )
    await hass.async_block_till_done()
    assert got == [(T0, {"dhwUse": 1})]

    remove()
    NWP500DataUpdateCoordinator._on_raw_status(  # noqa: SLF001
        coordinator, MAC, T0, {"dhwUse": 2}
    )
    await hass.async_block_till_done()
    assert len(got) == 1
