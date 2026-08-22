#!/usr/bin/env python3
"""Verify every pinned dependency version agrees with manifest.json.

`manifest.json` is the single source of truth: it is what Home Assistant
installs and what hassfest validates. Every other mention of a pinned
version -- requirements files, tox environments, the "install this" hints in
runtime error messages, the READMEs -- is a copy, and copies drift.

Rather than maintain a list of files to keep in sync (which rots the moment
someone adds a file), this scans every tracked file and fails on any
disagreement.

Historical references are deliberately not checked: prose like "dropped in
nwp500-python 9.3.0" documents when something happened and must not track
the current pin. That is why bare `package X.Y.Z` prose is not compared --
there is no way to tell the two apart.

So documentation that wants to state the current version must use a
package-qualified form this scanner recognises:

    nwp500-python vX.Y.Z
    https://github.com/eman/nwp500-python/releases/tag/vX.Y.Z

Anything else is invisible to this check and will go stale silently, which
is what happened to a "Current Version: 9.0.0" line in
.github/copilot-instructions.md while the manifest pinned 9.3.0. Prefer
pointing at manifest.json over restating the number at all.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

MANIFEST = Path("custom_components/nwp500/manifest.json")

# Files whose version mentions are historical or illustrative by nature.
EXCLUDED = {
    "CHANGELOG.md",
    "scripts/check_dependency_pins.py",
    "scripts/update_nwp500_version.py",
}

# A hard pin: nwp500-python==9.3.0
PIN = re.compile(r"(?P<pkg>[A-Za-z0-9_.\-]+)==(?P<version>\d+\.\d+\.\d+)")

# A prose/link reference to the *current* version, as used in READMEs:
#   [nwp500-python v9.3.0](https://github.com/eman/nwp500-python/...)
NAMED = re.compile(r"(?P<pkg>[A-Za-z0-9_.\-]+)\s+v(?P<version>\d+\.\d+\.\d+)")

# A release-tag link for a package we pin.
TAG = re.compile(
    r"github\.com/[^/\s]+/(?P<pkg>[A-Za-z0-9_.\-]+)"
    r"/releases/tag/v(?P<version>\d+\.\d+\.\d+)"
)


def load_expected() -> dict[str, str]:
    """Return {package: version} from the manifest's requirements array."""
    manifest = json.loads(MANIFEST.read_text())
    expected = {}
    for requirement in manifest.get("requirements", []):
        match = PIN.fullmatch(requirement.strip())
        if not match:
            raise SystemExit(
                f"manifest.json requirement is not an exact pin: "
                f"{requirement!r}. This checker assumes '=='."
            )
        expected[match["pkg"]] = match["version"]
    if not expected:
        raise SystemExit("manifest.json declares no requirements")
    return expected


def tracked_files() -> list[Path]:
    """Return the repository's tracked files.

    Uses git rather than walking the tree so that ignored directories --
    .venv, .tox, htmlcov -- stay out of the scan. They contain installed
    package metadata whose pins are not ours to check.
    """
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to enumerate tracked files")
    result = subprocess.run(  # noqa: S603
        [git, "ls-files"], capture_output=True, text=True, check=True
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def scan(
    path: Path, expected: dict[str, str]
) -> list[tuple[int, str, str, str]]:
    """Return (line_no, package, found_version, kind) for each mismatch."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError, OSError:
        return []  # binary or unreadable: nothing to check

    problems = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in (
            ("pin", PIN),
            ("reference", NAMED),
            ("release link", TAG),
        ):
            for match in pattern.finditer(line):
                package = match["pkg"]
                if package not in expected:
                    continue
                if match["version"] != expected[package]:
                    problems.append((line_no, package, match["version"], kind))
    return problems


def main() -> int:
    """Compare every tracked file against the manifest."""
    if not MANIFEST.is_file():
        print(f"Cannot find {MANIFEST}", file=sys.stderr)
        return 1

    expected = load_expected()
    failures = []

    for path in tracked_files():
        if path.as_posix() in EXCLUDED:
            continue
        for line_no, package, found, kind in scan(path, expected):
            failures.append(
                f"  {path}:{line_no}: {package} {kind} says {found}, "
                f"manifest says {expected[package]}"
            )

    pins = ", ".join(f"{p}=={v}" for p, v in sorted(expected.items()))

    if failures:
        print("Dependency pins disagree with manifest.json:\n")
        print("\n".join(failures))
        print(
            f"\nmanifest.json is the source of truth ({pins}).\n"
            "Run: python scripts/update_nwp500_version.py <new-version>"
        )
        return 1

    print(f"All dependency pins agree with manifest.json ({pins})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
