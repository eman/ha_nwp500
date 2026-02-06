#!/usr/bin/env python3
"""Update nwp500-python dependency version across all project files.

This script updates the nwp500-python version in all configuration files,
requirements, error messages, and documentation.
"""

import re
import sys
from pathlib import Path


def find_project_root() -> Path:
    """Find the project root directory."""
    current = Path.cwd()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path.cwd()


def update_file(file_path: Path, old_version: str, new_version: str) -> bool:
    """Update version string in a file.

    Args:
        file_path: Path to file to update
        old_version: Old version string (e.g., "7.3.4")
        new_version: New version string (e.g., "7.4.5")

    Returns:
        True if file was modified, False otherwise
    """
    content = file_path.read_text()
    original_content = content

    # Replace nwp500-python==X.Y.Z
    content = re.sub(
        rf"nwp500-python=={re.escape(old_version)}",
        f"nwp500-python=={new_version}",
        content,
    )

    # Replace uv pip install nwp500-python==X.Y.Z in error messages
    content = re.sub(
        rf"uv pip install nwp500-python=={re.escape(old_version)}",
        f"uv pip install nwp500-python=={new_version}",
        content,
    )

    # Replace in CHANGELOG upgrade descriptions (e.g., "from 7.3.1 to 7.3.4")
    # This handles "Upgraded from X to Y" patterns
    pattern = rf"(?i)to\s+{re.escape(old_version)}"
    content = re.sub(pattern, f"to {new_version}", content)

    if content != original_content:
        file_path.write_text(content)
        return True
    return False


def update_changelog(file_path: Path, new_version: str) -> bool:
    """Update CHANGELOG.md to reflect new nwp500-python version.

    Args:
        file_path: Path to CHANGELOG.md
        new_version: New nwp500-python version

    Returns:
        True if file was modified, False otherwise
    """
    content = file_path.read_text()

    # Check if there's an [Unreleased] section with nwp500-python
    unreleased_pattern = (
        r"## \[Unreleased\]\s*\n((?:[^#]|\n(?!##))*?)(?=\n##|\Z)"
    )
    match = re.search(unreleased_pattern, content)

    if match:
        unreleased_section = match.group(0)

        # Check if nwp500-python is already mentioned in unreleased
        if "nwp500-python" in unreleased_section:
            # Update existing version reference
            updated = re.sub(
                r"(- \*\*Library Dependency: nwp500-python\*\*: Upgraded .*?to\s+)[\d.]+",
                rf"\1{new_version}",
                unreleased_section,
                count=1,
            )
            if updated != unreleased_section:
                content = content.replace(unreleased_section, updated)
                file_path.write_text(content)
                return True
        else:
            # Add nwp500-python entry to Unreleased section
            entry = f"""## [Unreleased]

### Changed
- **Library Dependency: nwp500-python**: Upgraded to {new_version}

"""
            content = content.replace("## [Unreleased]\n\n", entry)

            # If [Unreleased] didn't exist, add it at the top after the intro
            if "## [Unreleased]" not in content:
                intro_end = content.find("\n## [")
                if intro_end != -1:
                    content = (
                        content[:intro_end]
                        + f"\n\n{entry.strip()}"
                        + content[intro_end:]
                    )

            file_path.write_text(content)
            return True

    return False


def main() -> int:
    """Main entry point."""
    if len(sys.argv) < 3:
        print(
            "Usage: python update_nwp500_version.py <old_version> <new_version>"
        )
        print("Example: python update_nwp500_version.py 7.3.4 7.4.5")
        return 1

    old_version = sys.argv[1]
    new_version = sys.argv[2]

    # Validate version format
    if not re.match(r"^\d+\.\d+\.\d+$", old_version):
        print(f"Invalid old version format: {old_version}")
        return 1

    if not re.match(r"^\d+\.\d+\.\d+$", new_version):
        print(f"Invalid new version format: {new_version}")
        return 1

    project_root = find_project_root()

    files_to_update = [
        project_root / "custom_components/nwp500/manifest.json",
        project_root / "requirements.txt",
        project_root / "tox.ini",
        project_root / "custom_components/nwp500/coordinator.py",
        project_root / "custom_components/nwp500/config_flow.py",
        project_root / "README.md",
        project_root / ".devcontainer/README.md",
        project_root / ".github/copilot-instructions.md",
    ]

    changelog = project_root / "CHANGELOG.md"

    print(f"Updating nwp500-python from {old_version} to {new_version}...")
    print(f"Project root: {project_root}\n")

    updated_files = []

    # Update regular files
    for file_path in files_to_update:
        if file_path.exists():
            if update_file(file_path, old_version, new_version):
                updated_files.append(file_path.relative_to(project_root))
                print(f"✓ Updated: {file_path.relative_to(project_root)}")
            else:
                print(f"- No changes: {file_path.relative_to(project_root)}")
        else:
            print(f"⚠ File not found: {file_path.relative_to(project_root)}")

    # Update CHANGELOG
    print()
    if changelog.exists():
        if update_changelog(changelog, new_version):
            print("✓ Updated CHANGELOG.md")
        else:
            print("- No changes to CHANGELOG.md")
    else:
        print("⚠ CHANGELOG.md not found")

    print(f"\nCompleted: Updated {len(updated_files)} files")

    if updated_files:
        print("\nNext steps:")
        print("1. Review the changes: git diff")
        print("2. Run type checking: tox -e mypy")
        print("3. Run tests: tox")
        print("4. Commit changes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
