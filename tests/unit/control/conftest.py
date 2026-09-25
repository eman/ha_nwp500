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
from custom_components.nwp500.control.intent import Plan, parse_plan

# The NWP500's usual range in half-degrees C: 40.5-65.5 degC, 104.9-149.9 degF.
DEVICE_MIN_RAW = 81
DEVICE_MAX_RAW = 131


def ts(base: datetime, minutes: float) -> str:
    """An ISO 8601 timestamp `minutes` after `base`, with an offset."""
    return (base + timedelta(minutes=minutes)).isoformat()


def segment(
    base: datetime,
    segment_id: str,
    start: float,
    *,
    mode: str | None = None,
    **setpoint: Any,
) -> dict[str, Any]:
    """A segment starting `start` minutes after `base`.

    Pass the setpoint as `setpoint_f=...`, `setpoint_c=...` or
    `setpoint="min"`, and any opaque keys.
    """
    doc: dict[str, Any] = {
        "id": segment_id,
        "start": ts(base, start),
        **setpoint,
    }
    if mode is not None:
        doc["mode"] = mode
    return doc


def grant(
    base: datetime,
    grant_id: str,
    start: float,
    end: float,
    **maximum: Any,
) -> dict[str, Any]:
    """A grant whose window is given in minutes from `base`."""
    return {
        "id": grant_id,
        "start": ts(base, start),
        "end": ts(base, end),
        **maximum,
    }


def make_document(
    now: datetime,
    segments: list[dict[str, Any]] | None = None,
    *,
    grants: list[dict[str, Any]] | None = None,
    intent_id: str = "i-1",
    issued_at: datetime | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A valid document issued now."""
    doc: dict[str, Any] = {
        "protocol": "1",
        "intent_id": intent_id,
        "issued_at": (issued_at or now).isoformat(),
        "segments": segments if segments is not None else [],
        **extra,
    }
    if grants is not None:
        doc["grants"] = grants
    return doc


@pytest.fixture
def now() -> datetime:
    """A fixed, aware 'now' for building documents, on a whole minute."""
    return dt_util.utcnow().replace(second=0, microsecond=0)


@pytest.fixture
def parse():
    """Parse a document."""

    def _parse(document: dict[str, Any]) -> Plan:
        return parse_plan(document)

    return _parse


class FakeFeatures:
    """Just the raw setpoint range of a DeviceFeature."""

    dhw_temperature_min_raw = DEVICE_MIN_RAW
    dhw_temperature_max_raw = DEVICE_MAX_RAW


def capabilities(**options: Any) -> Capabilities:
    """A declaration from options, with the device range known."""
    options.setdefault(
        "control_allowed_modes",
        ["heat_pump", "energy_saver", "high_demand", "electric"],
    )
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
