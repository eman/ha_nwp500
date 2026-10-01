"""The integration's user-facing names follow Home Assistant's style (#197)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

COMPONENT = Path(__file__).parent.parent.parent / "custom_components" / "nwp500"

# Words kept as written: acronyms, product and brand names.
_KEEP = {"WiFi", "NaviLink", "Navien", "Anti-Legionella", "NWP500"}


def _is_sentence_case(text: str) -> bool:
    """First word capitalised; later words lower case unless kept."""
    for index, word in enumerate(text.split()):
        for part in word.strip("()").split("-") if index else [word]:
            if not part or not part[0].isalpha():
                continue
            if (
                word.strip("()") in _KEEP
                or part in _KEEP
                or (len(part) > 1 and part.isupper())
                or any(ch.isdigit() for ch in part)
            ):
                continue
            if index == 0:
                if not part[0].isupper():
                    return False
            elif part != part.lower():
                return False
    return True


def _names(strings: dict) -> list[str]:
    names: list[str] = []
    for entities in strings["entity"].values():
        for entity in entities.values():
            if "name" in entity:
                names.append(entity["name"])
            names += [
                attribute["name"]
                for attribute in entity.get("state_attributes", {}).values()
                if "name" in attribute
            ]
    for service in strings["services"].values():
        names.append(service["name"])
        names += [field["name"] for field in service.get("fields", {}).values()]
    return names


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_names_are_in_sentence_case(name):
    strings = json.loads((COMPONENT / name).read_text())
    wrong = [n for n in _names(strings) if not _is_sentence_case(n)]
    assert wrong == []


def test_the_brand_is_spelled_navilink():
    text = (COMPONENT / "strings.json").read_text()
    assert "Navilink" not in text


def test_a_reservation_temperature_has_no_fixed_unit():
    """It is taken in Home Assistant's unit, which may be Celsius."""
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    number = services["set_reservation"]["fields"]["temperature"]["selector"][
        "number"
    ]
    assert "unit_of_measurement" not in number
    assert number["min"] <= 27
