"""icons.json names real entities and actions; code sets no icons."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

COMPONENT = Path(__file__).parents[2] / "custom_components" / "nwp500"
ICONS = json.loads((COMPONENT / "icons.json").read_text())
STRINGS = json.loads((COMPONENT / "strings.json").read_text())


def test_every_entity_icon_is_a_translated_entity():
    for platform, keys in ICONS["entity"].items():
        known = STRINGS["entity"].get(platform, {})
        for key in keys:
            assert key in known, f"{platform}.{key} has an icon, no entity"


def test_every_action_has_an_icon():
    actions = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    assert set(ICONS["services"]) == set(actions)


def test_no_entity_sets_its_icon_in_code():
    offenders = [
        f"{path.relative_to(COMPONENT)}:{n}"
        for path in COMPONENT.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"_attr_icon\s*=", line)
    ]
    assert offenders == []
