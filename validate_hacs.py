#!/usr/bin/env python3
"""Validate HACS requirements for the integration."""

import json
import sys
from pathlib import Path


def validate_hacs() -> bool:
    """Validate HACS requirements."""
    errors = []
    warnings = []

    # Check manifest.json
    manifest_path = Path("custom_components/nwp500/manifest.json")
    if not manifest_path.exists():
        errors.append("custom_components/nwp500/manifest.json not found")
        return False

    with open(manifest_path) as f:
        manifest = json.load(f)

    required_manifest_keys = ["domain", "name", "documentation", "version"]
    for key in required_manifest_keys:
        if key not in manifest:
            errors.append(f"manifest.json: Missing required key '{key}'")

    if "issue_tracker" not in manifest:
        warnings.append("manifest.json: Missing 'issue_tracker' (recommended)")

    # Check hacs.json
    hacs_json_path = Path("hacs.json")
    if not hacs_json_path.exists():
        errors.append("hacs.json not found")
    else:
        with open(hacs_json_path) as f:
            hacs_json = json.load(f)

        if "name" not in hacs_json:
            errors.append("hacs.json: Missing required key 'name'")

    # Check required files
    required_files = {
        "README.md": "README.md not found",
        "LICENSE": "LICENSE not found",
        "custom_components/nwp500/__init__.py": "__init__.py not found",
    }

    for file_path, error_msg in required_files.items():
        if not Path(file_path).exists():
            errors.append(error_msg)

    # Check optional but recommended files
    optional_files = {
        "images/icon.png": "Icon not found (recommended)",
        "images/logo.png": "Logo not found (optional)",
    }

    for file_path, warning_msg in optional_files.items():
        if not Path(file_path).exists():
            warnings.append(warning_msg)

    # Print results
    if errors:
        print("❌ HACS validation failed:\n")
        for error in errors:
            print(f"  ✗ {error}")
        print()

    if warnings:
        print("⚠️  Warnings:\n")
        for warning in warnings:
            print(f"  ! {warning}")
        print()

    if not errors:
        print("✅ HACS validation passed!")
        if warnings:
            print("   (with warnings - see above)")
        return True

    return False


if __name__ == "__main__":
    success = validate_hacs()
    sys.exit(0 if success else 1)
