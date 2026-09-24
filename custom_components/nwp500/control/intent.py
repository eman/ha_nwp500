"""The intent document: parsing and document-level validation.

Spec sections 3.1 to 3.3 (issue #158). A document is either accepted whole
or rejected whole with a reason; the per-directive checks against the
capability declaration are in `evaluate.py`, because they depend on the
declaration and on the time of evaluation, not on the document alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from nwp500.temperature import HalfCelsius

from ..const import CONTROL_DIRECTIVE_TYPES, CONTROL_MODE_NAMES

SUPPORTED_PROTOCOLS: tuple[str, ...] = ("0",)
INTENT_ID_MAX_LENGTH = 64

# Document-level rejection reasons.
REASON_INVALID_DOCUMENT = "invalid_document"
REASON_UNSUPPORTED_PROTOCOL = "unsupported_protocol"
REASON_INVALID_VALIDITY = "invalid_validity"
REASON_STALE_ON_RECEIPT = "stale_on_receipt"
REASON_DUPLICATE_DIRECTIVE_ID = "duplicate_directive_id"
REASON_INVALID_WINDOW = "invalid_window"
REASON_CHARGE_OVERLAPS_HOLD_OFF = "charge_overlaps_hold_off"
REASON_GRANT_OVERLAPS_MODE = "grant_overlaps_mode"

_TOP_LEVEL_KEYS = frozenset(
    {"protocol", "intent_id", "issued_at", "valid_until", "directives"}
)
_DIRECTIVE_KEYS = frozenset(
    {
        "id",
        "type",
        "start",
        "end",
        "target_f",
        "target_c",
        "max_f",
        "max_c",
        "mode",
        "request",
    }
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
class Directive:
    """One directive of an accepted intent.

    Temperatures are held in the device's own resolution, half degrees
    Celsius, so a value converts exactly once, on parsing.
    """

    id: str
    type: str
    start: datetime
    end: datetime
    # The charge target or the surplus-grant maximum, in half-degrees C.
    temperature_raw: int | None = None
    mode: str | None = None
    request: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> timedelta:
        """Length of the window."""
        return self.end - self.start

    def overlaps(self, other: Directive) -> bool:
        """Whether the two windows share any time."""
        return self.start < other.end and other.start < self.end

    def as_document(self) -> dict[str, Any]:
        """The directive as it was given, minus what did not parse.

        Temperatures come back in the unit they were given in, so a stored
        document round-trips through the parser unchanged.
        """
        doc: dict[str, Any] = {
            **self.extra,
            "id": self.id,
            "type": self.type,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }
        if self.type == "mode":
            doc["mode"] = self.mode
            doc["request"] = self.request
        elif self.temperature_raw is not None:
            key = "target" if self.type == "charge" else "max"
            unit = self.extra_unit
            value = HalfCelsius(self.temperature_raw)
            doc[f"{key}_{unit}"] = (
                value.to_celsius() if unit == "c" else value.to_fahrenheit()
            )
        return doc

    @property
    def extra_unit(self) -> str:
        """The unit the temperature was given in, `f` or `c`."""
        return str(self.extra.get("_unit", "f"))


@dataclass(frozen=True)
class Intent:
    """An accepted intent document."""

    intent_id: str
    issued_at: datetime
    valid_until: datetime
    directives: tuple[Directive, ...]
    extra: dict[str, Any] = field(default_factory=dict)

    def is_stale(self, now: datetime) -> bool:
        """Whether `valid_until` has passed."""
        return now >= self.valid_until

    def as_document(self) -> dict[str, Any]:
        """The intent as a document, for storage and for echoing."""
        return {
            **self.extra,
            "protocol": SUPPORTED_PROTOCOLS[0],
            "intent_id": self.intent_id,
            "issued_at": self.issued_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "directives": [d.as_document() for d in self.directives],
        }


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


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _parse_temperature(
    directive: Mapping[str, Any], key: str, where: str
) -> tuple[int, str]:
    """Exactly one of `<key>_f` / `<key>_c`, as half-degrees C and its unit."""
    given = [unit for unit in ("f", "c") if f"{key}_{unit}" in directive]
    if len(given) != 1:
        raise _reject(
            REASON_INVALID_DOCUMENT,
            f"{where} needs exactly one of {key}_f or {key}_c",
        )
    unit = given[0]
    value = directive[f"{key}_{unit}"]
    if not _is_number(value):
        raise _reject(
            REASON_INVALID_DOCUMENT, f"{where} {key}_{unit} must be a number"
        )
    temperature = (
        HalfCelsius.from_celsius(float(value))
        if unit == "c"
        else HalfCelsius.from_fahrenheit(float(value))
    )
    return int(temperature.raw_value), unit


def _parse_directive(raw: Any, index: int) -> Directive:
    where = f"directives[{index}]"
    if not isinstance(raw, Mapping):
        raise _reject(REASON_INVALID_DOCUMENT, f"{where} must be an object")
    for key in ("id", "type", "start", "end"):
        if key not in raw:
            raise _reject(REASON_INVALID_DOCUMENT, f"{where} lacks {key}")
    directive_id = raw["id"]
    if not isinstance(directive_id, str) or not directive_id:
        raise _reject(
            REASON_INVALID_DOCUMENT, f"{where} id must be a non-empty string"
        )
    directive_type = raw["type"]
    if directive_type not in CONTROL_DIRECTIVE_TYPES:
        raise _reject(
            REASON_INVALID_DOCUMENT,
            f"{where} type {directive_type!r} is not one of "
            f"{', '.join(CONTROL_DIRECTIVE_TYPES)}",
        )
    start = _parse_timestamp(raw["start"], f"{where} start")
    end = _parse_timestamp(raw["end"], f"{where} end")
    if end <= start:
        raise _reject(
            REASON_INVALID_WINDOW,
            f"{where} ({directive_id}) end is not later than start",
        )

    extra = {k: v for k, v in raw.items() if k not in _DIRECTIVE_KEYS}
    temperature_raw: int | None = None
    mode: str | None = None
    request = False

    if directive_type == "charge":
        temperature_raw, unit = _parse_temperature(raw, "target", where)
        extra["_unit"] = unit
    elif directive_type == "surplus_grant":
        temperature_raw, unit = _parse_temperature(raw, "max", where)
        extra["_unit"] = unit
    elif directive_type == "mode":
        mode = raw.get("mode")
        if mode not in CONTROL_MODE_NAMES:
            raise _reject(
                REASON_INVALID_DOCUMENT,
                f"{where} mode {mode!r} is not one of "
                f"{', '.join(CONTROL_MODE_NAMES)}",
            )
        request = raw.get("request", False)
        if not isinstance(request, bool):
            raise _reject(
                REASON_INVALID_DOCUMENT, f"{where} request must be a boolean"
            )

    return Directive(
        id=directive_id,
        type=directive_type,
        start=start,
        end=end,
        temperature_raw=temperature_raw,
        mode=mode,
        request=request,
        extra=extra,
    )


def parse_intent(document: Mapping[str, Any], *, now: datetime) -> Intent:
    """Parse and validate a document. Raises `IntentRejected` on failure.

    `now` is the receipt time, used for the stale-on-receipt check only.
    """
    for key in _TOP_LEVEL_KEYS:
        if key not in document:
            raise _reject(REASON_INVALID_DOCUMENT, f"document lacks {key}")

    protocol = document["protocol"]
    if not isinstance(protocol, str):
        raise _reject(REASON_INVALID_DOCUMENT, "protocol must be a string")
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
    valid_until = _parse_timestamp(document["valid_until"], "valid_until")
    if valid_until <= issued_at:
        raise _reject(
            REASON_INVALID_VALIDITY, "valid_until is not later than issued_at"
        )
    if valid_until <= now:
        raise _reject(
            REASON_STALE_ON_RECEIPT,
            f"valid_until {valid_until.isoformat()} has passed",
        )

    raw_directives = document["directives"]
    if not isinstance(raw_directives, list | tuple):
        raise _reject(REASON_INVALID_DOCUMENT, "directives must be a list")
    directives = tuple(
        _parse_directive(raw, index) for index, raw in enumerate(raw_directives)
    )

    seen: set[str] = set()
    for directive in directives:
        if directive.id in seen:
            raise _reject(
                REASON_DUPLICATE_DIRECTIVE_ID,
                f"directive id {directive.id!r} appears more than once",
            )
        seen.add(directive.id)

    _check_overlaps(directives)

    extra = {k: v for k, v in document.items() if k not in _TOP_LEVEL_KEYS}
    return Intent(
        intent_id=intent_id,
        issued_at=issued_at,
        valid_until=valid_until,
        directives=tuple(sorted(directives, key=lambda d: d.start)),
        extra=extra,
    )


def _check_overlaps(directives: tuple[Directive, ...]) -> None:
    """The two forbidden overlaps: charge/hold_off and surplus_grant/mode."""
    forbidden = (
        ("charge", "hold_off", REASON_CHARGE_OVERLAPS_HOLD_OFF),
        ("surplus_grant", "mode", REASON_GRANT_OVERLAPS_MODE),
    )
    for first_type, second_type, reason in forbidden:
        firsts = [d for d in directives if d.type == first_type]
        seconds = [d for d in directives if d.type == second_type]
        for a in firsts:
            for b in seconds:
                if a.overlaps(b):
                    raise _reject(
                        reason,
                        f"{a.type} {a.id!r} overlaps {b.type} {b.id!r}",
                    )
