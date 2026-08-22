"""Tests for the dependency version tooling in scripts/.

These guard the machinery that keeps every pinned version in step with
manifest.json. A bug here is quiet by nature -- it leaves some files edited
and others behind -- so the refusal and no-op paths matter as much as the
happy ones.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    """Import a module from scripts/, which is not a package."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("check_dependency_pins")
update = _load("update_nwp500_version")


def _manifest(tmp_path: Path, **pins: str) -> Path:
    """Write a manifest with the given package pins and return the repo root."""
    manifest = tmp_path / "custom_components/nwp500/manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "domain": "nwp500",
                "name": "Navien NWP500",
                "requirements": [f"{p}=={v}" for p, v in pins.items()],
            }
        )
    )
    return tmp_path


# ---------------------------------------------------------------------------
# check_dependency_pins
# ---------------------------------------------------------------------------


def test_load_expected_reads_the_manifest(tmp_path, monkeypatch):
    """The manifest's requirements array is the declared source of truth."""
    monkeypatch.chdir(_manifest(tmp_path, **{"nwp500-python": "9.3.0"}))

    assert check.load_expected() == {"nwp500-python": "9.3.0"}


def test_load_expected_rejects_a_loose_requirement(tmp_path, monkeypatch):
    """A range instead of an exact pin makes the whole check meaningless."""
    manifest = tmp_path / "custom_components/nwp500/manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"requirements": ["nwp500-python>=9.3.0"]}))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="not an exact pin"):
        check.load_expected()


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("nwp500-python==9.2.1", "pin"),
        ("Uses **[nwp500-python v9.2.1](x)**", "reference"),
        (
            "https://github.com/eman/nwp500-python/releases/tag/v9.2.1",
            "release link",
        ),
    ],
)
def test_scan_flags_every_recognised_form(tmp_path, line, kind):
    """Each supported way of naming a version is compared to the manifest."""
    target = tmp_path / "doc.md"
    target.write_text(line)

    problems = check.scan(target, {"nwp500-python": "9.3.0"})

    assert [(p[1], p[2], p[3]) for p in problems] == [
        ("nwp500-python", "9.2.1", kind)
    ]


def test_scan_ignores_historical_prose(tmp_path):
    """Prose naming a version records history, not the current pin."""
    target = tmp_path / "notes.md"
    target.write_text(
        "Sensor keys dropped in nwp500-python 9.3.0 were removed.\n"
        "The pre-9.3.0 fields are gone.\n"
    )

    assert check.scan(target, {"nwp500-python": "9.9.9"}) == []


def test_scan_ignores_packages_we_do_not_pin(tmp_path):
    """Unrelated pins in the repository are none of this check's business."""
    target = tmp_path / "other.txt"
    target.write_text("somethingelse==1.0.0\n")

    assert check.scan(target, {"nwp500-python": "9.3.0"}) == []


def test_scan_skips_unreadable_files(tmp_path):
    """Binary files must not crash a repository-wide scan."""
    target = tmp_path / "icon.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")

    assert check.scan(target, {"nwp500-python": "9.3.0"}) == []


# ---------------------------------------------------------------------------
# update_nwp500_version.rewrite
# ---------------------------------------------------------------------------


def test_rewrite_updates_every_recognised_form(tmp_path):
    """Pins, prose references and release links all move together."""
    target = tmp_path / "README.md"
    target.write_text(
        "nwp500-python==9.3.0\n"
        "Uses **[nwp500-python v9.3.0]"
        "(https://github.com/eman/nwp500-python/releases/tag/v9.3.0)**\n"
    )

    assert update.rewrite(target, "nwp500-python", "9.3.0", "9.4.0")

    text = target.read_text()
    assert "nwp500-python==9.4.0" in text
    assert "nwp500-python v9.4.0" in text
    assert "releases/tag/v9.4.0" in text
    assert "9.3.0" not in text


def test_rewrite_leaves_historical_prose_alone(tmp_path):
    """Bumping the pin must not rewrite what happened in an old release."""
    target = tmp_path / "notes.py"
    target.write_text(
        "# Sensor keys dropped in nwp500-python 9.3.0.\n"
        'PIN = "nwp500-python==9.3.0"\n'
    )

    update.rewrite(target, "nwp500-python", "9.3.0", "9.4.0")

    text = target.read_text()
    assert "dropped in nwp500-python 9.3.0" in text
    assert 'PIN = "nwp500-python==9.4.0"' in text


def test_rewrite_reports_nothing_for_an_untouched_file(tmp_path):
    """Files with no reference are left byte-identical."""
    target = tmp_path / "unrelated.txt"
    target.write_text("nothing to see\n")

    assert update.rewrite(target, "nwp500-python", "9.3.0", "9.4.0") == 0
    assert target.read_text() == "nothing to see\n"


def test_rewrite_only_touches_the_named_package(tmp_path):
    """An awsiotsdk-only bump must not disturb the library pin."""
    target = tmp_path / "requirements.txt"
    target.write_text("nwp500-python==9.3.0\nawsiotsdk==1.31.0\n")

    update.rewrite(target, "awsiotsdk", "1.31.0", "1.32.0")

    assert target.read_text() == "nwp500-python==9.3.0\nawsiotsdk==1.32.0\n"


# ---------------------------------------------------------------------------
# update_nwp500_version.update_changelog
# ---------------------------------------------------------------------------


UNRELEASED_WITH_CHANGED = """# Changelog

## [Unreleased]

### Changed
- Something else changed.

## [0.18.0] - 2026-08-05

### Changed
- **Library Dependency: nwp500-python**: Upgraded to
  [9.2.1](https://github.com/eman/nwp500-python/releases/tag/v9.2.1).
"""


def test_changelog_adds_a_bullet_under_unreleased(tmp_path):
    """A new upgrade is recorded in the section being released next."""
    path = tmp_path / "CHANGELOG.md"
    path.write_text(UNRELEASED_WITH_CHANGED)

    assert update.update_changelog(path, "nwp500-python", "9.4.0") is True

    text = path.read_text()
    unreleased = text.split("## [0.18.0]")[0]
    assert "Upgraded to 9.4.0" in unreleased
    # The historical entry is untouched.
    assert "[9.2.1]" in text.split("## [0.18.0]")[1]


def test_changelog_updates_an_existing_unreleased_bullet(tmp_path):
    """Two bumps before a release update one bullet rather than stacking."""
    path = tmp_path / "CHANGELOG.md"
    path.write_text(UNRELEASED_WITH_CHANGED)

    update.update_changelog(path, "nwp500-python", "9.4.0")
    update.update_changelog(path, "nwp500-python", "9.5.0")

    unreleased = path.read_text().split("## [0.18.0]")[0]
    assert unreleased.count("Library Dependency: nwp500-python") == 1
    assert "Upgraded to 9.5.0" in unreleased


def test_changelog_does_not_match_a_released_bullet(tmp_path):
    """The same bullet text appears in every past release.

    Searching the whole file finds a historical entry, changes nothing, and
    reports success -- which is what this function used to do.
    """
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n"
        "## [0.18.0] - 2026-08-05\n\n"
        "### Changed\n"
        "- **Library Dependency: nwp500-python**: Upgraded to\n"
        "  [9.2.1](https://example.invalid).\n"
    )

    assert update.update_changelog(path, "nwp500-python", "9.4.0") is True

    unreleased = path.read_text().split("## [0.18.0]")[0]
    assert "Upgraded to 9.4.0" in unreleased
    assert "[9.2.1]" in path.read_text().split("## [0.18.0]")[1]


def test_changelog_without_an_unreleased_section(tmp_path):
    """Nothing to update means a truthful False, not a silent success."""
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [0.18.0] - 2026-08-05\n")

    assert update.update_changelog(path, "nwp500-python", "9.4.0") is False


# ---------------------------------------------------------------------------
# update_nwp500_version.main
# ---------------------------------------------------------------------------


def _repo(tmp_path: Path, track: bool = True, **pins: str) -> Path:
    """Build a throwaway git repo, since the tooling scans tracked files."""
    import shutil
    import subprocess

    git = shutil.which("git")
    assert git, "git is required for these tests"

    root = _manifest(tmp_path, **pins)
    (root / "requirements.txt").write_text(
        "".join(f"{p}=={v}\n" for p, v in pins.items())
    )
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    subprocess.run([git, "init", "-q"], cwd=root, check=True)  # noqa: S603
    if track:
        subprocess.run([git, "add", "-A"], cwd=root, check=True)  # noqa: S603
    return root


def test_main_is_a_noop_when_already_current(tmp_path, monkeypatch, capsys):
    """Re-running a completed bump must not rewrite or re-announce anything."""
    root = _repo(tmp_path, **{"nwp500-python": "9.3.0"})
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["prog", "9.3.0"])

    assert update.main() == 0

    assert "already at 9.3.0" in capsys.readouterr().out
    assert "## [Unreleased]\n" in (root / "CHANGELOG.md").read_text()
    assert "Library Dependency" not in (root / "CHANGELOG.md").read_text()


def test_main_bumps_awsiotsdk_alone(tmp_path, monkeypatch):
    """--awsiotsdk moves only that pin and leaves the library untouched."""
    root = _repo(tmp_path, **{"nwp500-python": "9.3.0", "awsiotsdk": "1.31.0"})
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["prog", "--awsiotsdk", "1.32.0"])

    assert update.main() == 0

    requirements = (root / "requirements.txt").read_text()
    assert "awsiotsdk==1.32.0" in requirements
    assert "nwp500-python==9.3.0" in requirements
    assert "awsiotsdk" in (root / "CHANGELOG.md").read_text()


def test_main_refuses_to_record_an_upgrade_it_did_not_make(
    tmp_path, monkeypatch, capsys
):
    """The old script rewrote nothing, exited 0, and still wrote a CHANGELOG.

    Here nothing is tracked, so no file can be rewritten; that must be a
    failure rather than a release note announcing a bump that never happened.
    """
    root = _repo(tmp_path, track=False, **{"nwp500-python": "9.3.0"})
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["prog", "9.4.0"])

    assert update.main() == 1

    assert "did not happen" in capsys.readouterr().err
    assert "Library Dependency" not in (root / "CHANGELOG.md").read_text()


def test_main_rejects_a_malformed_version(tmp_path, monkeypatch):
    """A typo in the target version is refused rather than half-applied."""
    root = _repo(tmp_path, **{"nwp500-python": "9.3.0"})
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["prog", "--awsiotsdk", "not-a-version"])

    assert update.main() == 1


def test_main_requires_an_argument(tmp_path, monkeypatch):
    """Bare invocation prints usage and fails rather than guessing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog"])

    assert update.main() == 1


def test_main_bump_leaves_the_repo_consistent(tmp_path, monkeypatch):
    """After a bump the pin checker agrees, which is the whole point."""
    root = _repo(tmp_path, **{"nwp500-python": "9.3.0", "awsiotsdk": "1.31.0"})
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["prog", "9.4.0"])

    assert update.main() == 0
    assert check.load_expected()["nwp500-python"] == "9.4.0"
    for path in check.tracked_files():
        assert check.scan(path, check.load_expected()) == []
