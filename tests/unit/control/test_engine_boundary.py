"""The control engine stands without Home Assistant.

The engine (planning, entries, checking plans, commands and what the
heater reports) and the package modules it imports never import Home
Assistant: it can be reasoned about, and tested, as plain Python.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[3] / "custom_components" / "nwp500"
ENGINE = (
    "control/capabilities.py",
    "control/commands.py",
    "control/engine.py",
    "control/entries.py",
    "control/evaluate.py",
    "control/intent.py",
    "control/observed.py",
)


def _imports(path: Path) -> tuple[set[str], set[Path]]:
    """Its outside modules, and its own package's module files."""
    outside: set[str] = set()
    inside: set[Path] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            outside.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                outside.add(node.module or "")
                continue
            base = path.parent
            for _ in range(node.level - 1):
                base = base.parent
            parts = (node.module or "").split(".") if node.module else []
            names = [parts] if parts else [[alias.name] for alias in node.names]
            for name in names:
                target = base.joinpath(*name)
                for candidate in (
                    target.with_suffix(".py"),
                    target / "__init__.py",
                ):
                    if candidate.is_file():
                        inside.add(candidate)
    return outside, inside


def _reach(start: Path) -> dict[Path, set[str]]:
    seen: dict[Path, set[str]] = {}
    todo = [start]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        outside, inside = _imports(path)
        seen[path] = outside
        todo.extend(inside)
    return seen


@pytest.mark.parametrize("module", ENGINE)
def test_the_engine_never_imports_home_assistant(module):
    reached = _reach(PACKAGE / module)
    offenders = {
        str(path.relative_to(PACKAGE)): sorted(
            m for m in outside if m.split(".")[0] == "homeassistant"
        )
        for path, outside in reached.items()
    }
    assert {k: v for k, v in offenders.items() if v} == {}
