"""Recording a heater's statuses: what goes on disk, and when."""

from __future__ import annotations

import gzip
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from nwp500 import status_history

from custom_components.nwp500.const import simulator_history_dir
from custom_components.nwp500.simulator import recorder as recorder_module
from custom_components.nwp500.simulator.recorder import (
    MAX_BUFFERED,
    HistoryRecorder,
    Stored,
    UnwrittenError,
    append,
    delete,
    maintain,
    stored,
)

from .conftest import MAC, status_dict

DAY = date(2026, 10, 21)


def write(
    directory: str, lines: list[tuple[date, str]], today: date, keep_days: int
) -> Stored:
    append(directory, lines)
    return maintain(directory, today, keep_days)


def _lines(directory: Path, name: str) -> list[str]:
    path = directory / name
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read().splitlines()
    return path.read_text(encoding="utf-8").splitlines()


def test_lines_go_to_their_days_file(tmp_path: Path):
    write(str(tmp_path), [(DAY, "a"), (DAY, "b")], DAY, 60)
    write(str(tmp_path), [(DAY, "c")], DAY, 60)

    assert _lines(tmp_path, "2026-10-21.jsonl") == ["a", "b", "c"]


def test_a_finished_day_is_compressed(tmp_path: Path):
    write(str(tmp_path), [(DAY, "a")], DAY, 60)

    result = write(str(tmp_path), [], DAY + timedelta(days=1), 60)

    assert sorted(os.listdir(tmp_path)) == ["2026-10-21.jsonl.gz"]
    assert _lines(tmp_path, "2026-10-21.jsonl.gz") == ["a"]
    assert result.days == 1
    assert result.first_day == result.last_day == DAY


def test_a_late_line_joins_its_compressed_day(tmp_path: Path):
    write(str(tmp_path), [(DAY, "a")], DAY + timedelta(days=1), 60)
    write(str(tmp_path), [(DAY, "b")], DAY + timedelta(days=1), 60)

    assert _lines(tmp_path, "2026-10-21.jsonl.gz") == ["a", "b"]


def test_days_older_than_kept_are_deleted(tmp_path: Path):
    old = DAY - timedelta(days=7)
    write(str(tmp_path), [(old, "old"), (DAY, "new")], DAY, 60)

    result = write(str(tmp_path), [], DAY, 7)

    assert sorted(os.listdir(tmp_path)) == ["2026-10-21.jsonl"]
    assert result.days == 1


def test_other_files_are_left_alone(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("mine")

    write(str(tmp_path), [], DAY, 7)

    assert (tmp_path / "notes.txt").exists()
    assert stored(str(tmp_path)).days == 0


def test_nothing_recorded_stores_nothing(tmp_path: Path):
    directory = tmp_path / "none"
    assert write(str(directory), [], DAY, 60).days == 0
    assert not directory.exists()
    delete(str(directory))


def test_the_history_is_per_heater_under_the_config_directory():
    assert simulator_history_dir("/config", "AA:BB:CC:DD:EE:FF") == (
        "/config/nwp500/history/aabbccddeeff"
    )


class _Coordinator:
    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def async_add_raw_status_listener(self, mac: str, listener: Any) -> Any:
        assert mac == MAC
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


@pytest.fixture
def clock() -> MagicMock:
    return MagicMock(return_value=datetime(2026, 10, 21, 12, 0, 30, tzinfo=UTC))


@pytest.mark.asyncio
async def test_each_status_is_recorded_as_sent_without_the_address(
    hass: HomeAssistant, clock
):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder.async_start()
    await hass.async_block_till_done()

    payload = {**status_dict(), "macAddress": MAC}
    coordinator.listeners[0](payload)
    coordinator.listeners[0]({"not": "a status"})
    await recorder.async_stop()

    assert coordinator.listeners == []
    directory = Path(recorder.directory)
    (line,) = _lines(directory, "2026-10-21.jsonl")
    ((at, status),) = status_history.read([line])
    assert at == clock.return_value
    assert status.tank_upper_temperature is not None
    assert MAC not in line
    assert '"macAddress"' not in line
    assert recorder.recorded == 1
    assert recorder.as_document()["days"] == 1


@pytest.mark.asyncio
async def test_lines_are_kept_while_the_disk_refuses_them(
    hass: HomeAssistant, clock, caplog
):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder.async_start()
    await hass.async_block_till_done()
    coordinator.listeners[0](status_dict())

    with patch.object(
        recorder_module,
        "append",
        side_effect=lambda _d, lines: (_ for _ in ()).throw(
            UnwrittenError(OSError("read-only"), lines)
        ),
    ):
        await recorder.async_flush()
        await recorder.async_flush()

    assert recorder.error == "read-only"
    assert recorder.as_document()["held"] == 1
    assert caplog.text.count("Cannot write the recorded history") == 1

    await recorder.async_stop()
    assert recorder.error is None
    assert len(_lines(Path(recorder.directory), "2026-10-21.jsonl")) == 1


@pytest.mark.asyncio
async def test_the_lines_held_are_bounded(hass: HomeAssistant, clock):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder._unsubs.append(  # noqa: SLF001
        coordinator.async_add_raw_status_listener(MAC, recorder._on_status)  # noqa: SLF001
    )
    payload = status_dict()
    for _ in range(MAX_BUFFERED + 5):
        coordinator.listeners[0](payload)

    assert recorder.as_document()["held"] == MAX_BUFFERED
    assert recorder.dropped == 5
    for unsub in recorder._unsubs:  # noqa: SLF001
        unsub()


@pytest.mark.asyncio
async def test_the_longest_gap_is_reported(hass: HomeAssistant, clock):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder._unsubs.append(  # noqa: SLF001
        coordinator.async_add_raw_status_listener(MAC, recorder._on_status)  # noqa: SLF001
    )
    start = clock.return_value
    for seconds in (0, 30, 300, 330):
        clock.return_value = start + timedelta(seconds=seconds)
        coordinator.listeners[0](status_dict())

    document = recorder.as_document()
    assert document["longest_gap_s"] == 270
    assert document["last_status"] == clock.return_value.isoformat()
    for unsub in recorder._unsubs:  # noqa: SLF001
        unsub()


@pytest.mark.asyncio
async def test_deleting_drops_the_lines_held_too(hass: HomeAssistant, clock):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder.async_start()
    await hass.async_block_till_done()
    coordinator.listeners[0](status_dict())
    await recorder.async_flush()
    coordinator.listeners[0](status_dict())

    await recorder.async_delete(stop=False)
    await recorder.async_stop()

    assert not Path(recorder.directory).exists()
    assert recorder.as_document()["days"] == 0


def test_a_failed_append_leaves_its_day_as_it_was(tmp_path: Path):
    write(str(tmp_path), [(DAY, "a")], DAY, 60)
    later = DAY + timedelta(days=1)
    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        if str(path).endswith(later.isoformat() + ".jsonl"):
            f.write("partial")
            f.close()
            raise OSError("disk full")
        return f

    with (
        patch("builtins.open", failing_open),
        pytest.raises(UnwrittenError) as raised,
    ):
        append(str(tmp_path), [(DAY, "b"), (later, "c"), (later, "d")])

    # The first day went in; the failed one is cut back, and only its
    # lines are to be written again.
    assert raised.value.lines == [(later, "c"), (later, "d")]
    assert _lines(tmp_path, "2026-10-21.jsonl") == ["a", "b"]
    assert not (tmp_path / "2026-10-22.jsonl").exists()


def test_a_failed_compression_keeps_the_day_once(tmp_path: Path):
    write(str(tmp_path), [(DAY, "a")], DAY + timedelta(days=1), 60)
    write(str(tmp_path), [(DAY, "b")], DAY, 60)
    tomorrow = DAY + timedelta(days=1)

    with (
        patch.object(
            recorder_module.os, "replace", side_effect=OSError("no space")
        ),
        pytest.raises(OSError, match="no space"),
    ):
        maintain(str(tmp_path), tomorrow, 60)

    assert sorted(os.listdir(tmp_path)) == [
        "2026-10-21.jsonl",
        "2026-10-21.jsonl.gz",
    ]
    maintain(str(tmp_path), tomorrow, 60)
    assert _lines(tmp_path, "2026-10-21.jsonl.gz") == ["a", "b"]


@pytest.mark.asyncio
async def test_a_failed_compression_does_not_write_the_lines_again(
    hass: HomeAssistant, clock
):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder.async_start()
    await hass.async_block_till_done()
    coordinator.listeners[0](status_dict())

    with patch.object(
        recorder_module, "maintain", side_effect=OSError("no space")
    ):
        await recorder.async_flush()

    assert recorder.error == "no space"
    assert recorder.as_document()["held"] == 0
    await recorder.async_stop()
    assert recorder.error is None
    assert len(_lines(Path(recorder.directory), "2026-10-21.jsonl")) == 1


@pytest.mark.asyncio
async def test_lines_kept_after_a_failure_count_what_they_drop(
    hass: HomeAssistant, clock
):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder._unsubs.append(  # noqa: SLF001
        coordinator.async_add_raw_status_listener(MAC, recorder._on_status)  # noqa: SLF001
    )
    payload = status_dict()
    for _ in range(MAX_BUFFERED):
        coordinator.listeners[0](payload)

    def fail(_directory, lines):
        # Statuses arrive while the write is under way.
        for _ in range(3):
            hass.loop.call_soon_threadsafe(coordinator.listeners[0], payload)
        raise UnwrittenError(OSError("read-only"), lines)

    with patch.object(recorder_module, "append", side_effect=fail):
        await recorder.async_flush()
        await hass.async_block_till_done()

    document = recorder.as_document()
    assert document["held"] == MAX_BUFFERED
    assert document["recorded"] == MAX_BUFFERED + 3
    assert document["dropped"] == 3
    for unsub in recorder._unsubs:  # noqa: SLF001
        unsub()


@pytest.mark.asyncio
async def test_home_assistant_stopping_writes_what_is_held(
    hass: HomeAssistant, clock, caplog
):
    from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE

    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder.async_start()
    await hass.async_block_till_done()
    coordinator.listeners[0](status_dict())

    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()

    assert len(_lines(Path(recorder.directory), "2026-10-21.jsonl")) == 1
    # Stopping after it does not remove the listener twice.
    await recorder.async_stop()
    assert "Unable to remove" not in caplog.text


def test_a_compression_stopped_before_its_last_step_is_not_repeated(
    tmp_path: Path,
):
    """The compressed file was swapped in; the plain file was not removed."""
    write(str(tmp_path), [(DAY, "a")], DAY + timedelta(days=1), 60)
    write(str(tmp_path), [(DAY, "late")], DAY, 60)
    tomorrow = DAY + timedelta(days=1)
    real_remove = os.remove

    def remove(path):
        if str(path).endswith(".jsonl"):
            raise OSError("busy")
        real_remove(path)

    with (
        patch.object(recorder_module.os, "remove", side_effect=remove),
        pytest.raises(OSError, match="busy"),
    ):
        maintain(str(tmp_path), tomorrow, 60)
    maintain(str(tmp_path), tomorrow, 60)

    assert sorted(os.listdir(tmp_path)) == ["2026-10-21.jsonl.gz"]
    assert _lines(tmp_path, "2026-10-21.jsonl.gz") == ["a", "late"]


@pytest.mark.asyncio
async def test_deleting_for_good_takes_nothing_more(hass: HomeAssistant, clock):
    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder.async_start()
    await hass.async_block_till_done()
    listener = coordinator.listeners[0]

    await recorder.async_delete(stop=True)
    assert coordinator.listeners == []
    listener(status_dict())  # one already on its way
    await recorder.async_stop()

    assert not Path(recorder.directory).exists()


async def test_a_cancelled_flush_finishes_before_the_next_starts(
    hass: HomeAssistant, clock
):
    """A cancelled flush ends its write before the next write starts.

    Such as a calibration's, cancelled on unload.
    """
    import asyncio
    import threading

    coordinator = _Coordinator()
    recorder = HistoryRecorder(hass, coordinator, MAC, 60, clock=clock)  # type: ignore[arg-type]
    recorder.async_start()
    await hass.async_block_till_done()
    coordinator.listeners[0](status_dict())
    entered, release = threading.Event(), threading.Event()
    active, most = 0, 0
    guard = threading.Lock()
    real = recorder_module.append

    def slow_append(directory: str, lines: Any) -> None:
        nonlocal active, most
        with guard:
            active += 1
            most = max(most, active)
        entered.set()
        release.wait(5)
        try:
            real(directory, lines)
        finally:
            with guard:
                active -= 1

    with patch.object(recorder_module, "append", slow_append):
        first = hass.async_create_task(recorder.async_flush())
        await hass.async_add_executor_job(entered.wait, 5)
        first.cancel()
        stop = hass.async_create_task(recorder.async_stop())
        await asyncio.sleep(0)
        release.set()
        await stop
        with pytest.raises(asyncio.CancelledError):
            await first

    assert most == 1
    assert recorder.as_document()["days"] == 1
