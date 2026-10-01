"""The intent document: a plan of segments and device commands.

Spec section 3 of issue #158. Parsing checks what the document says on its
own terms: types, required keys, ids and the order of segments. Checks that
depend on the capability declaration (bounds, allowed modes) are in
`evaluate.py`; the superseded check needs the plan in force and is made by
the device controller.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nwp500.temperature import HalfCelsius

from ..const import CONTROL_MODE_NAMES

# Protocol 1 is the document format of the revised protocol 0, with the
# compatibility promises of spec section 1.3. Protocol 0 documents are still
# accepted for the transition; they mean the same.
SUPPORTED_PROTOCOLS: tuple[str, ...] = ("1", "0")
# The newest version implemented of each major above, in the same order
# (spec section 4.1), so a consumer can check that a minor version's keys
# are honoured before relying on them. A feature that knew only 1.0 would
# keep 1.1's keys as opaque.
PROTOCOL_VERSIONS: tuple[str, ...] = ("1.3", "0")
# A major version, and optionally a minor one: "1" or "1.1". The schema
# carries the same pattern.
_PROTOCOL_PATTERN = re.compile(r"[0-9]+(\.[0-9]+)?")
INTENT_ID_MAX_LENGTH = 64
# Segment ids. Bounded so that the entities naming them stay
# within the recorder's attribute limit (spec section 4.2).
ITEM_ID_MAX_LENGTH = 64

# Document-level rejection reasons (spec section 3.5).
REASON_INVALID_DOCUMENT = "invalid_document"
REASON_UNSUPPORTED_PROTOCOL = "unsupported_protocol"
REASON_DUPLICATE_ID = "duplicate_id"
REASON_UNORDERED_SEGMENTS = "unordered_segments"
REASON_OUT_OF_BOUNDS = "out_of_bounds"
REASON_MODE_NOT_ALLOWED = "mode_not_allowed"
REASON_SUPERSEDED = "superseded"
# A command rejected on its own; the plan proceeds (section 3.7).
REASON_INVALID_COMMAND = "invalid_command"
REASON_UNSUPPORTED_COMMAND = "unsupported_command"

# Device commands (protocol 1.3, section 3.7): each one's own keys, all
# required. Anti-Legionella's period is given only when switching it on.
COMMAND_KEYS: dict[str, dict[str, type]] = {
    "vacation": {"days": int},
    "power": {"on": bool},
    "anti_legionella": {"enabled": bool, "period_days": int},
    "tou": {"enabled": bool},
    "demand_response": {"enabled": bool},
}

_TOP_LEVEL_KEYS = frozenset(
    {"protocol", "intent_id", "issued_at", "segments", "commands"}
)
_SEGMENT_KEYS = frozenset(
    {"id", "start", "setpoint_f", "setpoint_c", "mode", "reassert"}
)
# Attributes Home Assistant adds to a state for presentation. They are not
# part of the document and are not echoed as opaque keys.
HA_PRESENTATION_ATTRIBUTES = frozenset(
    {
        "friendly_name",
        "icon",
        "entity_picture",
        "supported_features",
        "device_class",
        "state_class",
        "unit_of_measurement",
        "attribution",
        "restored",
        "assumed_state",
    }
)


class IntentRejected(Exception):  # noqa: N818 - the spec's own word for it
    """The document was rejected whole."""

    def __init__(self, reason: str, detail: str) -> None:
        """Record the reason (a stable identifier) and a readable detail."""
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Segment:
    """One segment: a state from its start until the next segment's start.

    The setpoint is held in the device's own resolution, half-degrees
    Celsius, so a value converts exactly once, on parsing.
    """

    id: str
    start: datetime
    mode: str
    setpoint_raw: int
    # How the setpoint was given: `f` or `c`, so that it is echoed
    # and stored the way it arrived.
    setpoint_form: str
    # Whether `mode` was given, or kept from the previous segment.
    mode_given: bool = True
    # Protocol 1.1: program an entry even if the state repeats the segment
    # before, so a person's change is ended at its start (section 3.2).
    reassert: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_document(self) -> dict[str, Any]:
        """The segment as it was given."""
        doc: dict[str, Any] = {
            **self.extra,
            "id": self.id,
            "start": self.start.isoformat(),
        }
        value = HalfCelsius(self.setpoint_raw)
        doc[f"setpoint_{self.setpoint_form}"] = round(
            value.to_celsius()
            if self.setpoint_form == "c"
            else value.to_fahrenheit(),
            1,
        )
        if self.mode_given:
            doc["mode"] = self.mode
        if self.reassert:
            doc["reassert"] = True
        return doc


@dataclass(frozen=True)
class Command:
    """One device command, applied once as sent (section 3.7).

    A command that is malformed or unknown is kept, with why, so the
    acknowledgement can report it; it is never applied.
    """

    id: str
    command: str
    params: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)
    # `invalid_command` or `unsupported_command`; None if it can be applied.
    rejection: str | None = None
    detail: str | None = None
    # The item as it was given, for storage.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def content(self) -> str:
        """What the command does: the same id with other content is new."""
        return json.dumps(
            {"command": self.command, **self.params}, sort_keys=True
        )

    def as_document(self) -> dict[str, Any]:
        """The command as it was given."""
        return dict(self.raw)


@dataclass(frozen=True)
class Plan:
    """An accepted intent document."""

    intent_id: str
    issued_at: datetime
    segments: tuple[Segment, ...]
    extra: dict[str, Any] = field(default_factory=dict)
    commands: tuple[Command, ...] = ()

    def segment_at(self, when: datetime) -> Segment | None:
        """The segment in force at `when`, or None before the first."""
        current: Segment | None = None
        for segment in self.segments:
            if segment.start <= when:
                current = segment
            else:
                break
        return current

    def next_segment_after(self, when: datetime) -> Segment | None:
        """The first segment that starts after `when`."""
        for segment in self.segments:
            if segment.start > when:
                return segment
        return None

    def as_document(self) -> dict[str, Any]:
        """The plan as a document, for storage."""
        doc: dict[str, Any] = {
            **self.extra,
            "protocol": SUPPORTED_PROTOCOLS[0],
            "intent_id": self.intent_id,
            "issued_at": self.issued_at.isoformat(),
            "segments": [s.as_document() for s in self.segments],
        }
        if self.commands:
            doc["commands"] = [c.as_document() for c in self.commands]
        return doc


def document_from_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Strip Home Assistant's presentation attributes from an entity state."""
    return {
        key: value
        for key, value in attributes.items()
        if key not in HA_PRESENTATION_ATTRIBUTES
    }


def _reject(reason: str, detail: str) -> IntentRejected:
    return IntentRejected(reason, detail)


def _parse_timestamp(value: Any, where: str) -> datetime:
    """An ISO 8601 timestamp with an offset; anything else is invalid."""
    if not isinstance(value, str):
        raise _reject(REASON_INVALID_DOCUMENT, f"{where} must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise _reject(
            REASON_INVALID_DOCUMENT, f"{where} is not ISO 8601: {value!r}"
        ) from err
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _reject(
            REASON_INVALID_DOCUMENT, f"{where} has no UTC offset: {value!r}"
        )
    return parsed


def truncate_to_minute(when: datetime) -> datetime:
    """The start of the minute; entries fire at the start of their minute."""
    return when.replace(second=0, microsecond=0)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _to_raw(value: float, unit: str) -> int:
    if not math.isfinite(value) or abs(value) > 1000:
        raise _reject(
            REASON_INVALID_DOCUMENT, f"setpoint {value!r} is not a temperature"
        )
    temperature = (
        HalfCelsius.from_celsius(value)
        if unit == "c"
        else HalfCelsius.from_fahrenheit(value)
    )
    return int(temperature.raw_value)


def _parse_segment_setpoint(
    raw: Mapping[str, Any], where: str
) -> tuple[int, str]:
    """Exactly one of `setpoint_f` or `setpoint_c`, a number."""
    given = [k for k in ("setpoint_f", "setpoint_c") if k in raw]
    if len(given) != 1 or "setpoint" in raw:
        raise _reject(
            REASON_INVALID_DOCUMENT,
            f"{where} needs exactly one of setpoint_f or setpoint_c",
        )
    key = given[0]
    value = raw[key]
    if not _is_number(value):
        raise _reject(
            REASON_INVALID_DOCUMENT, f"{where} {key} must be a number"
        )
    unit = key.rsplit("_", 1)[1]
    return _to_raw(float(value), unit), unit


def _parse_id(raw: Mapping[str, Any], where: str) -> str:
    if "id" not in raw:
        raise _reject(REASON_INVALID_DOCUMENT, f"{where} lacks id")
    value = raw["id"]
    if (
        not isinstance(value, str)
        or not value
        or len(value) > ITEM_ID_MAX_LENGTH
    ):
        raise _reject(
            REASON_INVALID_DOCUMENT,
            f"{where} id must be a string of 1 to {ITEM_ID_MAX_LENGTH} "
            "characters",
        )
    return value


def _parse_segments(raw_segments: Any) -> tuple[Segment, ...]:
    if not isinstance(raw_segments, list | tuple):
        raise _reject(REASON_INVALID_DOCUMENT, "segments must be a list")
    segments: list[Segment] = []
    previous_mode: str | None = None
    for index, raw in enumerate(raw_segments):
        where = f"segments[{index}]"
        if not isinstance(raw, Mapping):
            raise _reject(REASON_INVALID_DOCUMENT, f"{where} must be an object")
        segment_id = _parse_id(raw, where)
        if "start" not in raw:
            raise _reject(REASON_INVALID_DOCUMENT, f"{where} lacks start")
        start = truncate_to_minute(
            _parse_timestamp(raw["start"], f"{where} start")
        )
        setpoint_raw, setpoint_form = _parse_segment_setpoint(raw, where)

        mode_given = "mode" in raw
        if mode_given:
            mode = raw["mode"]
            if not isinstance(mode, str):
                raise _reject(
                    REASON_INVALID_DOCUMENT, f"{where} mode must be a string"
                )
        elif previous_mode is None:
            raise _reject(
                REASON_INVALID_DOCUMENT,
                f"{where} is the first segment and must give a mode",
            )
        else:
            mode = previous_mode
        previous_mode = mode
        reassert = raw.get("reassert", False)
        if not isinstance(reassert, bool):
            raise _reject(
                REASON_INVALID_DOCUMENT,
                f"{where} reassert must be true or false",
            )

        if segments and start <= segments[-1].start:
            raise _reject(
                REASON_UNORDERED_SEGMENTS,
                f"{where} ({segment_id}) does not start after "
                f"{segments[-1].id}",
            )
        segments.append(
            Segment(
                id=segment_id,
                start=start,
                mode=mode,
                setpoint_raw=setpoint_raw,
                setpoint_form=setpoint_form,
                mode_given=mode_given,
                reassert=reassert,
                extra={k: v for k, v in raw.items() if k not in _SEGMENT_KEYS},
            )
        )
    return tuple(segments)


def _is_type(value: Any, kind: type) -> bool:
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, kind)


def _command_problem(
    name: Any, raw: Mapping[str, Any]
) -> tuple[str, str] | None:
    """Why a command cannot be applied, or None if it can."""
    if not isinstance(name, str):
        return REASON_INVALID_COMMAND, "command must be a string"
    keys = COMMAND_KEYS.get(name)
    if keys is None:
        return REASON_UNSUPPORTED_COMMAND, f"command {name!r} is not supported"
    required = dict(keys)
    if name == "anti_legionella" and raw.get("enabled") is not True:
        del required["period_days"]
        if "period_days" in raw:
            return (
                REASON_INVALID_COMMAND,
                "period_days is given only with enabled true",
            )
    for key, kind in required.items():
        if key not in raw:
            return REASON_INVALID_COMMAND, f"{name} lacks {key}"
        if not _is_type(raw[key], kind):
            return (
                REASON_INVALID_COMMAND,
                f"{name} {key} must be {'a whole number' if kind is int else 'true or false'}",
            )
    return None


def _parse_commands(raw_commands: Any) -> tuple[Command, ...]:
    """The plan's commands. Only the list's shape and the ids are fatal."""
    if not isinstance(raw_commands, list | tuple):
        raise _reject(REASON_INVALID_DOCUMENT, "commands must be a list")
    commands: list[Command] = []
    for index, raw in enumerate(raw_commands):
        where = f"commands[{index}]"
        if not isinstance(raw, Mapping):
            raise _reject(REASON_INVALID_DOCUMENT, f"{where} must be an object")
        command_id = _parse_id(raw, where)
        name = raw.get("command")
        keys = COMMAND_KEYS.get(name, {}) if isinstance(name, str) else {}
        problem = _command_problem(name, raw)
        own = {"id", "command", *keys}
        commands.append(
            Command(
                id=command_id,
                command=name if isinstance(name, str) else "",
                params={k: raw[k] for k in keys if k in raw},
                extra={k: v for k, v in raw.items() if k not in own},
                rejection=problem[0] if problem else None,
                detail=problem[1] if problem else None,
                raw=dict(raw),
            )
        )
    return tuple(commands)


def parse_plan(document: Mapping[str, Any]) -> Plan:
    """Parse a document. Raises `IntentRejected` on failure."""
    for key in ("protocol", "intent_id", "issued_at", "segments"):
        if key not in document:
            raise _reject(REASON_INVALID_DOCUMENT, f"document lacks {key}")

    protocol = document["protocol"]
    if not isinstance(protocol, str):
        raise _reject(REASON_INVALID_DOCUMENT, "protocol must be a string")
    if not _PROTOCOL_PATTERN.fullmatch(protocol):
        raise _reject(
            REASON_INVALID_DOCUMENT,
            f'protocol {protocol!r} is not a version such as "1" or "1.1"',
        )
    if protocol.split(".", 1)[0] not in SUPPORTED_PROTOCOLS:
        raise _reject(
            REASON_UNSUPPORTED_PROTOCOL,
            f"protocol {protocol!r}; supported: "
            f"{', '.join(SUPPORTED_PROTOCOLS)}",
        )

    intent_id = document["intent_id"]
    if (
        not isinstance(intent_id, str)
        or not intent_id
        or len(intent_id) > INTENT_ID_MAX_LENGTH
    ):
        raise _reject(
            REASON_INVALID_DOCUMENT,
            f"intent_id must be a string of 1 to {INTENT_ID_MAX_LENGTH} "
            "characters",
        )

    issued_at = _parse_timestamp(document["issued_at"], "issued_at")
    segments = _parse_segments(document["segments"])
    commands = _parse_commands(document.get("commands", []))

    seen: set[str] = set()
    for item_id in [s.id for s in segments] + [c.id for c in commands]:
        if item_id in seen:
            raise _reject(
                REASON_DUPLICATE_ID, f"id {item_id!r} appears more than once"
            )
        seen.add(item_id)

    return Plan(
        intent_id=intent_id,
        issued_at=issued_at,
        segments=segments,
        extra={k: v for k, v in document.items() if k not in _TOP_LEVEL_KEYS},
        commands=commands,
    )


def mode_is_valid(mode: str) -> bool:
    """Whether a mode name is one a segment may ever use."""
    return mode in CONTROL_MODE_NAMES
