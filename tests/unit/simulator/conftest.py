"""Fixtures for the simulator's tests: a heater as the coordinator holds it.

The simulator's tests need its packages; without them (tox -e py314-nosim)
they are not collected.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

if importlib.util.find_spec("pandas") is None:
    collect_ignore_glob = ["test_*.py"]

FIXTURES = Path(__file__).parents[2] / "fixtures" / "simulator"
MAC = "AA:BB:CC:DD:EE:FF"


def status_dict(**changes: Any) -> dict[str, Any]:
    """The device status fixture, with `changes`.

    Energy Saver at 140 °F, the tank part drawn, the compressor and the
    elements off.
    """
    raw = json.loads((FIXTURES / "device_status.json").read_text())
    raw.update(changes)
    return raw


def make_status(**changes: Any) -> Any:
    from nwp500.models import DeviceStatus

    return DeviceStatus.model_validate(status_dict(**changes))


def make_feature() -> Any:
    from nwp500.models import DeviceFeature

    return DeviceFeature.model_validate(
        json.loads((FIXTURES / "device_feature.json").read_text())
    )


@pytest.fixture
def coordinator(mock_device) -> MagicMock:
    """The coordinator, with one heater that has reported."""
    coordinator = MagicMock()
    coordinator.data = {
        MAC: {
            "device": mock_device,
            "status": make_status(),
            "last_update": 1.0,
        }
    }
    coordinator.device_features = {MAC: make_feature()}
    coordinator.reservation_schedules = {}
    coordinator.tou_schedules = {}
    listeners: list[Any] = []

    def add_listener(listener: Any) -> Any:
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    coordinator.async_add_listener = MagicMock(side_effect=add_listener)
    coordinator.listeners = listeners
    return coordinator


@pytest.fixture(autouse=True)
def config_dir(hass: Any, tmp_path: Path) -> Path:
    """Home Assistant's configuration directory, private to the test.

    The recorded history is written under it.
    """
    hass.config.config_dir = str(tmp_path)
    return tmp_path
