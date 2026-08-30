#!/usr/bin/env python3
"""Update pinned dependency versions across every tracked file.

`manifest.json` is the source of truth, so the *current* version is read
from it rather than supplied on the command line -- passing a stale or
mistyped "old version" used to match nothing, change no files, and still
exit 0 while writing a CHANGELOG entry announcing the upgrade.

Files are discovered by scanning, using the same patterns and exclusions as
`check_dependency_pins.py`, so the two can never disagree about where a
version lives and adding a new file needs no change here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_dependency_pins import (  # noqa: E402
    EXCLUDED,
    MANIFEST,
    load_expected,
    repository_files,
)

VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def rewrite(path: Path, package: str, old: str, new: str) -> int:
    """Rewrite this package's version references in one file.

    Only pins (``pkg==X.Y.Z``), current-version references (``pkg vX.Y.Z``)
    and release-tag links are touched. Prose such as "dropped in pkg 9.3.0"
    records when something happened and is deliberately left alone.

    Returns:
        The number of references rewritten.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError, OSError:
        return 0

    pkg = re.escape(package)
    old_v = re.escape(old)
    updated = text
    for pattern, replacement in (
        (rf"{pkg}=={old_v}", f"{package}=={new}"),
        (rf"{pkg} v{old_v}", f"{package} v{new}"),
        (
            rf"({pkg}/releases/tag/v){old_v}",
            rf"\g<1>{new}",
        ),
    ):
        updated = re.sub(pattern, replacement, updated)

    if updated == text:
        return 0

    count = len(re.findall(rf"{pkg}[= v/][^\n]*?{re.escape(new)}", updated))
    path.write_text(updated, encoding="utf-8")
    return count


def update_changelog(path: Path, package: str, new: str) -> bool:
    """Record the upgrade under ``## [Unreleased]``.

    Scoped to the Unreleased section on purpose: the same bullet text
    appears in every past release, so searching the whole file would find a
    historical entry and quietly do nothing.

    Returns:
        True only if the file actually changed.
    """
    content = path.read_text(encoding="utf-8")
    match = re.search(
        r"## \[Unreleased\]\n(?P<body>.*?)(?=\n## \[|\Z)",
        content,
        re.DOTALL,
    )
    if not match:
        return False

    section = match.group("body")
    marker = f"- **Library Dependency: {package}**: Upgraded to"
    bullet = f"{marker} {new}"

    if marker in section:
        # Replace the version that follows, on this line or the next, with
        # or without a markdown link around it.
        updated = re.sub(
            rf"({re.escape(marker)})\s*\[?[\d.]+\]?"
            r"(\([^)]*\))?",
            rf"\g<1> {new}",
            section,
            count=1,
        )
    elif "### Changed" in section:
        updated = section.replace(
            "### Changed\n", f"### Changed\n{bullet}\n", 1
        )
    else:
        updated = f"\n### Changed\n{bullet}\n{section}"

    if updated == section:
        return False

    # Rewrite the section by span rather than by value: an empty Unreleased
    # section is the empty string, and `content.replace("", ...)` inserts at
    # offset 0 -- putting the new bullet above the file's own title.
    start, end = match.span("body")
    path.write_text(content[:start] + updated + content[end:], encoding="utf-8")
    return True


def main() -> int:
    """Bump one or both pinned dependencies."""
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage: update_nwp500_version.py <new-version> "
            "[--awsiotsdk <new-version>]\n\n"
            "The current version is read from manifest.json; do not pass it."
        )
        return 0 if args else 1

    targets: dict[str, str] = {}
    if VERSION.match(args[0]):
        targets["nwp500-python"] = args[0]
        args = args[1:]
    if args[:1] == ["--awsiotsdk"] and len(args) == 2:
        if not VERSION.match(args[1]):
            print(f"Invalid version: {args[1]}", file=sys.stderr)
            return 1
        targets["awsiotsdk"] = args[1]
        args = []
    if args or not targets:
        print(
            "Usage: update_nwp500_version.py <new-version> "
            "[--awsiotsdk <new-version>]",
            file=sys.stderr,
        )
        return 1

    if not MANIFEST.is_file():
        print(
            f"Cannot find {MANIFEST}. Run from the repository root.",
            file=sys.stderr,
        )
        return 1

    current = load_expected()
    changed_any = False

    for package, new in targets.items():
        old = current.get(package)
        if old is None:
            print(f"{package} is not pinned in manifest.json", file=sys.stderr)
            return 1
        if old == new:
            print(f"- {package} is already at {new}; nothing to do")
            continue

        print(f"Updating {package} {old} -> {new}")
        touched = []
        for path in repository_files():
            if path.as_posix() in EXCLUDED:
                continue
            if rewrite(path, package, old, new):
                touched.append(path)
                print(f"  updated {path}")

        if not touched:
            print(
                f"  no references to {package}=={old} were found -- "
                "refusing to record an upgrade that did not happen",
                file=sys.stderr,
            )
            return 1

        changelog = Path("CHANGELOG.md")
        if changelog.is_file() and update_changelog(changelog, package, new):
            print("  updated CHANGELOG.md")
        changed_any = True

    if not changed_any:
        return 0

    print(
        "\nNext steps:\n"
        "  1. python scripts/check_dependency_pins.py\n"
        "  2. Review the diff: git diff\n"
        "  3. Expand the CHANGELOG entry with behaviour changes\n"
        "  4. tox"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
