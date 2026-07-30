"""Consistency tests for the service documentation.

The same service and field descriptions are duplicated across three files:
``services.yaml`` (what Home Assistant renders in the UI), ``strings.json``
and ``translations/en.json``. They drifted apart in issue #105 -- the
``enable`` field was documented inverted in one place and the vacation range
disagreed with its own validator -- so these tests pin the invariants that
drift violates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import voluptuous as vol
import yaml

from custom_components.nwp500 import SERVICE_SET_VACATION_DAYS_SCHEMA

COMPONENT = Path(__file__).parent.parent.parent / "custom_components" / "nwp500"


def _load_yaml() -> dict:
    with open(COMPONENT / "services.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_json(name: str) -> dict:
    with open(COMPONENT / name, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def services_yaml() -> dict:
    return _load_yaml()


@pytest.fixture(scope="module", params=["strings.json", "translations/en.json"])
def translations(request) -> dict:
    return _load_json(request.param)["services"]


def test_same_services_defined_everywhere(services_yaml, translations):
    """Every service in services.yaml must be described in the strings files."""
    assert set(services_yaml) == set(translations)


def test_descriptions_match(services_yaml, translations):
    """A service's description must be identical in both places.

    Divergence here is exactly how the inverted `enable` documentation in
    issue #105 survived: services.yaml was corrected in isolation before.
    """
    for name, spec in services_yaml.items():
        expected = " ".join(spec["description"].split())
        actual = " ".join(translations[name]["description"].split())
        assert actual == expected, f"description drift in service '{name}'"


def test_same_fields_defined_everywhere(services_yaml, translations):
    """Each service must document the same field names in both places.

    Checked separately from the descriptions so a missing or extra field
    reports which key drifted, rather than surfacing as a KeyError. Six
    `entity_id` fields were missing from the strings files in issue #105,
    and an extra field there would otherwise go unnoticed entirely.
    """
    for name, spec in services_yaml.items():
        expected = set(spec.get("fields") or {})
        actual = set(translations[name].get("fields") or {})
        assert actual == expected, (
            f"field drift in service '{name}': "
            f"missing from strings {sorted(expected - actual)}, "
            f"unexpected in strings {sorted(actual - expected)}"
        )


def test_field_descriptions_match(services_yaml, translations):
    """Each field's description must be identical in both places."""
    for name, spec in services_yaml.items():
        for field, fspec in (spec.get("fields") or {}).items():
            if "description" not in fspec:
                continue
            expected = " ".join(fspec["description"].split())
            actual = " ".join(
                translations[name]["fields"][field]
                .get("description", "")
                .split()
            )
            assert actual == expected, f"description drift in '{name}.{field}'"


def test_enable_documented_with_device_bool_convention(services_yaml):
    """`enable` uses the device bool convention: 2=on, 1=off.

    nwp500-python `ReservationEntry.enabled` is `self.enable == 2`, so
    documenting 1 as enabled (issue #105) inverts the meaning.
    """
    description = services_yaml["update_reservations"]["fields"][
        "reservations"
    ]["description"]
    assert "2=enabled" in description
    assert "1=disabled" in description
    assert "1=enabled" not in description


@pytest.mark.parametrize("days", [1, 30])
def test_vacation_description_matches_validator_bounds(services_yaml, days):
    """The documented vacation range must be what the schema accepts.

    The service description claimed 1-365 while the validator, the field
    description and the selector all capped at 30 (issue #105).
    """
    SERVICE_SET_VACATION_DAYS_SCHEMA({"device_id": "d", "days": days})

    spec = services_yaml["set_vacation_days"]
    assert "1-30" in spec["description"]
    assert "1-30" in spec["fields"]["days"]["description"]
    selector = spec["fields"]["days"]["selector"]["number"]
    assert (selector["min"], selector["max"]) == (1, 30)


@pytest.mark.parametrize("days", [0, 31, 365])
def test_vacation_validator_rejects_out_of_range(days):
    """Values outside the documented range are rejected."""
    with pytest.raises(vol.Invalid):
        SERVICE_SET_VACATION_DAYS_SCHEMA({"device_id": "d", "days": days})
