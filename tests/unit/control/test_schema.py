"""The protocol 1 schema against what the feature parses and reports.

Every example plan in docs/examples is valid by the schema and parses;
every control entity's attributes, from a controller running a plan, fit
the schema's `*_attributes` for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from custom_components.nwp500.control.binary_sensor import BINARY_SENSOR_KEYS
from custom_components.nwp500.control.intent import parse_plan
from custom_components.nwp500.control.sensor import SENSOR_KEYS

from . import test_live
from .conftest import make_document, segment
from .test_device import _publish

live_gate = test_live.live_gate
live_factory = test_live.live_factory

SCHEMA = json.loads(
    Path("docs/external-control-protocol-1.schema.json").read_text()
)
EXAMPLES = Path("docs/examples")
PLANS = sorted(p.name for p in EXAMPLES.glob("plan-*.json"))

# Each entity key's $def. `wanted_mode` reports no attributes.
ATTRIBUTES = {
    "capabilities": "capabilities_attributes",
    "intent": "intent_attributes",
    "ack": "ack_attributes",
    "program_hash": "program_hash_attributes",
    "programmed_until": "programmed_until_attributes",
    "next_entry": "next_entry_attributes",
    "wanted_setpoint": "wanted_setpoint_attributes",
    "last_write": "last_write_attributes",
    "heartbeat": "heartbeat_attributes",
    "in_sync": "in_sync_attributes",
    "override": "override_attributes",
}


def _validator(ref: str | None = None) -> Draft202012Validator:
    if ref is None:
        return Draft202012Validator(SCHEMA)
    return Draft202012Validator(
        {"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{ref}"}
    )


def test_the_schema_is_a_schema():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("name", PLANS)
def test_each_example_plan_is_valid_and_parses(name):
    """Valid, but for a command the feature does not apply.

    The schema describes the commands applied; plan-commands.json shows
    one it does not, rejected on its own (unsupported_command).
    """
    document = json.loads((EXAMPLES / name).read_text())
    plan = parse_plan(document)
    unsupported = {
        c.id for c in plan.commands if c.rejection == "unsupported_command"
    }
    applied = {
        **document,
        "commands": [
            c
            for c in document.get("commands", [])
            if c.get("id") not in unsupported
        ],
    }
    if "commands" not in document:
        applied.pop("commands")
    _validator().validate(applied)
    if unsupported:
        assert not _validator().is_valid(document)


def test_a_first_segment_without_its_mode_is_invalid_both_ways(now):
    document = make_document(now, [segment(now, "s", 0, setpoint_f=130)])
    document["segments"][0].pop("mode", None)
    assert not _validator().is_valid(document)
    with pytest.raises(Exception, match="mode"):
        parse_plan(document)


def test_every_entity_with_attributes_has_its_def():
    keys = {key for key, _ in (*SENSOR_KEYS, *BINARY_SENSOR_KEYS)}
    assert keys - {"wanted_mode"} == set(ATTRIBUTES)


@pytest.mark.asyncio
async def test_every_entitys_attributes_fit_the_schema(hass, live_factory, now):
    """Before a plan, and with one programmed, every attribute fits."""
    _heater, control = await live_factory()

    def check() -> None:
        for key, cls in (*SENSOR_KEYS, *BINARY_SENSOR_KEYS):
            if key not in ATTRIBUTES:
                continue
            entity = cls(control, key)
            entity.hass = hass
            _validator(ATTRIBUTES[key]).validate(entity.extra_state_attributes)

    check()
    _publish(
        hass,
        make_document(
            now,
            [
                segment(now, "s1", -5, mode="heat_pump", setpoint_f=140),
                segment(now, "s2", 120, setpoint_f=125),
            ],
        ),
    )
    await hass.async_block_till_done()
    assert control.plan is not None
    check()
    # The heater's range is known: the declaration says it.
    assert control.capabilities.as_attributes()["setpoint"] is not None
