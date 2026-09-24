"""Shared helpers for the external control tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.util import dt as dt_util

from custom_components.nwp500.control.capabilities import (
    Capabilities,
    build_capabilities,
)
from custom_components.nwp500.control.intent import Intent, parse_intent

# The NWP500's usual range in half-degrees C: 40.5-65.5 degC, 104.9-149.9 degF.
DEVICE_MIN_RAW = 81
DEVICE_MAX_RAW = 131


def ts(base: datetime, minutes: float) -> str:
    """An ISO 8601 timestamp `minutes` after `base`, with an offset."""
    return (base + timedelta(minutes=minutes)).isoformat()


def make_document(
    now: datetime,
    directives: list[dict[str, Any]] | None = None,
    *,
    intent_id: str = "i-1",
    valid_for: float = 60,
    **extra: Any,
) -> dict[str, Any]:
    """A valid document issued now, with the given directives."""
    return {
        "protocol": "0",
        "intent_id": intent_id,
        "issued_at": now.isoformat(),
        "valid_until": ts(now, valid_for),
        "directives": directives if directives is not None else [],
        **extra,
    }


def directive(
    now: datetime,
    kind: str,
    directive_id: str = "d1",
    *,
    start: float = 0,
    end: float = 180,
    **extra: Any,
) -> dict[str, Any]:
    """A directive of `kind` whose window is given in minutes from now."""
    return {
        "id": directive_id,
        "type": kind,
        "start": ts(now, start),
        "end": ts(now, end),
        **extra,
    }


@pytest.fixture
def now() -> datetime:
    """A fixed, aware 'now' for building documents."""
    return dt_util.utcnow().replace(microsecond=0)


@pytest.fixture
def parse(now: datetime):
    """Parse a document as received now."""

    def _parse(document: dict[str, Any]) -> Intent:
        return parse_intent(document, now=now)

    return _parse


class FakeFeatures:
    """Just the raw setpoint range of a DeviceFeature."""

    dhw_temperature_min_raw = DEVICE_MIN_RAW
    dhw_temperature_max_raw = DEVICE_MAX_RAW


def capabilities(**options: Any) -> Capabilities:
    """A declaration from options, with the device range known."""
    return build_capabilities(
        options,
        features=FakeFeatures(),
        feature_version="0.0-test",
        telemetry={
            "delivery_temperature": "sensor.tank_upper",
            "compressor_running": "binary_sensor.comp",
            "power": "sensor.power",
        },
    )
