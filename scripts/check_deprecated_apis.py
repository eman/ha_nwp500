#!/usr/bin/env python3
"""Check for deprecated Home Assistant APIs in the custom component.

This script scans the custom component for known deprecated patterns and APIs
that should not be used in modern Home Assistant integrations.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Deprecated patterns that should trigger warnings/errors
DEPRECATED_PATTERNS = {
    # API changes in recent HA versions
    r"async_get_registry\(": {
        "severity": "error",
        "message": "Use specific registry methods (e.g., device_registry.async_get)",
        "version": "2022.8+",
    },
    r"get_registry\(": {
        "severity": "error",
        "message": "Use async_get instead",
        "version": "2021.12+",
    },
    r"async_track_state_change_old": {
        "severity": "error",
        "message": "Use async_track_state_change instead",
        "version": "2021.3+",
    },
    r"ENTITY_ID_ALL_ATTRS": {
        "severity": "error",
        "message": "Constant removed",
        "version": "2022.2+",
    },
    r"\.state_attributes\(\)": {
        "severity": "warning",
        "message": "Verify implementation - modern entities use @property state_attributes",
        "version": "2023.1+",
    },
    r"from homeassistant\.helpers\.service import": {
        "severity": "error",
        "message": "Use service registry instead",
        "version": "2022.5+",
    },
    r"EntityComponent\(": {
        "severity": "error",
        "message": "Direct usage deprecated, use entity platform instead",
        "version": "2021.12+",
    },
    r"async_add_entity": {
        "severity": "warning",
        "message": "Use async_add_entities (plural) instead",
        "version": "2021.3+",
    },
    # Enum usage checks
    r'DEVICE_CLASS_\w+["\']': {
        "severity": "warning",
        "message": "Use DeviceClass enum instead of string constants",
        "version": "2021.12+",
    },
    r"ATTR_UNIT_OF_MEASUREMENT": {
        "severity": "warning",
        "message": "Use UnitOfMeasurement or native_unit_of_measurement instead",
        "version": "2023.10+",
    },
}

# Patterns that are OK (whitelisted)
WHITELIST_PATTERNS = {
    # These are standard patterns that are not deprecated
    r"async_add_entities",  # Standard platform method
    r"STATE_",  # Constants for state mappings are valid
    r"from homeassistant.const import",  # Standard imports
}


def check_file(filepath: Path) -> list[tuple[int, str, str]]:
    """Check a Python file for deprecated patterns.

    Args:
        filepath: Path to Python file to check

    Returns:
        List of (line_number, pattern, message) tuples for violations found
    """
    violations: list[tuple[int, str, str]] = []

    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    for line_num, line in enumerate(lines, 1):
        # Check against deprecated patterns
        for pattern, info in DEPRECATED_PATTERNS.items():
            if re.search(pattern, line):
                # Check if it's whitelisted
                is_whitelisted = any(
                    re.search(wl, line) for wl in WHITELIST_PATTERNS
                )
                if not is_whitelisted:
                    violations.append(
                        (
                            line_num,
                            f"{info['severity'].upper()}: {info['message']} ({info['version']})",
                            line.strip(),
                        )
                    )

    return violations


def main() -> int:
    """Run the deprecated API check.

    Returns:
        0 if no violations found, 1 otherwise
    """
    custom_component_dir = (
        Path(__file__).parent.parent / "custom_components" / "nwp500"
    )

    if not custom_component_dir.exists():
        print(
            f"❌ Custom component directory not found: {custom_component_dir}"
        )
        return 1

    all_violations: dict[Path, list[tuple[int, str, str]]] = {}
    total_errors = 0
    total_warnings = 0

    # Check all Python files
    for filepath in sorted(custom_component_dir.rglob("*.py")):
        violations = check_file(filepath)
        if violations:
            all_violations[filepath] = violations

            for _line_num, message, _line_content in violations:
                if "ERROR" in message:
                    total_errors += 1
                else:
                    total_warnings += 1

    # Report results
    if all_violations:
        print("🔍 Deprecated API Check Results")
        print("=" * 70)

        for filepath, violations in all_violations.items():
            rel_path = filepath.relative_to(filepath.parent.parent.parent)
            print(f"\n📄 {rel_path}")

            for line_num, message, line_content in violations:
                print(f"  Line {line_num}: {message}")
                print(f"    > {line_content}")

        print("\n" + "=" * 70)
        print(f"Summary: {total_errors} error(s), {total_warnings} warning(s)")

        if total_errors > 0:
            print("❌ Deprecated APIs found - please fix before release")
            return 1
        else:
            print("⚠️  Warnings found - review recommended")
            return 0
    else:
        print("✅ No deprecated Home Assistant APIs detected")
        print("   All imports and patterns are compatible with current HA")
        return 0


if __name__ == "__main__":
    sys.exit(main())
