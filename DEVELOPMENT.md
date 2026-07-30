# Development Guide

Developer-focused documentation for contributing to the Navien NWP500 Home Assistant integration.

## Architecture Overview

### Core Components

**Config Flow** (`config_flow.py`)
- User credential collection (email/password)
- Authentication validation against Navien API
- Device discovery and account verification

**Data Coordinator** (`coordinator.py`)
- Manages authentication with NavienAuthClient
- Handles device discovery via NavienAPIClient
- Establishes MQTT connection for real-time updates
- Coordinates periodic status requests (30s interval)
- Manages device control commands

**Base Entity** (`entity.py`)
- Common functionality for all NWP500 entities
- Device information and identification
- Availability and state management

**Platform Entities**
- **Water Heater** (`water_heater.py`) - Main control interface
- **Sensors** (`sensor.py`) - Temperature, power, diagnostics
- **Binary Sensors** (`binary_sensor.py`) - Status indicators
- **Switches** (`switch.py`) - Power control
- **Numbers** (`number.py`) - Temperature setpoint sliders

### Communication Flow

```
Authentication: User Credentials → NavienAuthClient → JWT Tokens → API Access
Data Updates: MQTT (30s) → Device Status → HA Entities
              (Fallback to REST API on connection issues)
Device Control: HA Command → MQTT Message → Device Response → Status Update
```

## Setting Up Development

### Prerequisites
- Python 3.14+
- Home Assistant 2026.3.0+
- Virtual environment recommended

### Environment Setup

**Using Dev Container (Recommended)**
1. Install VS Code and [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
2. Install Docker Desktop
3. Open repo in VS Code → "Reopen in Container"

**Local Setup**
```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

## Testing

### Run Tests
```bash
# All checks (tests, type checking, coverage)
tox

# Specific environments
tox -e py314            # Test on Python 3.14
tox -e coverage         # Test with coverage (requires 80%+)
tox -e mypy             # Type check with mypy
tox -e basedpyright     # Type check with basedpyright

# Direct pytest
pytest                  # All tests
pytest tests/unit/      # Unit tests only
pytest -k "temperature" # Pattern matching
pytest -vv              # Verbose output
pytest --pdb            # Drop to debugger on failure
pytest --lf             # Rerun last failed
```

### Test Organization
```
tests/
├── conftest.py              # Shared fixtures
├── unit/
│   ├── test_const.py
│   ├── test_entity.py
│   ├── test_water_heater.py
│   └── ...
└── fixtures/                # Test data
```

### Coverage Requirements
- **Minimum**: 80% (enforced by CI)
- **Target**: 90%+
- Check: `tox -e coverage` generates HTML report at `htmlcov/index.html`

### Test Fixtures
Available in `tests/conftest.py`:
- `mock_config_entry` - ConfigEntry mock
- `mock_device` - NWP500 device mock
- `mock_device_status` - Device status with realistic data
- `mock_coordinator` - Coordinator with test data
- `mock_nwp500_auth_client` - Authentication client mock
- `mock_nwp500_api_client` - API client mock
- `mock_nwp500_mqtt_client` - MQTT client mock

## Type Checking

### Mypy
```bash
tox -e mypy
```
- Industry standard type checker
- Must pass with zero errors before commits
- Flexible per-module configuration in `mypy.ini`

### Pyright
```bash
tox -e basedpyright
```
- Fast, IDE-integrated type checker
- Configuration in `pyrightconfig.json`
- Set to `basic` mode for balanced strictness
- Both mypy and basedpyright must pass in CI

### Type Hints
- Use full type hints throughout (from `__future__ import annotations`)
- Async functions: `async def func() -> ResultType:`
- Optional values: `value: int | None`

## Code Quality

### Style
- **PEP 8 compliance** required
- **Max line length**: 100 characters
- **Async/await**: Use async patterns for I/O

### API Compatibility Check
```bash
python3 scripts/check_deprecated_apis.py
```
- Scans for deprecated Home Assistant APIs
- Runs automatically in CI
- Required to pass before merge

### CI Checks (All Required)
1. `lint` - Automated linting and formatting (ruff)
2. `hassfest` - Official Home Assistant validation
3. `deprecated-apis` - No deprecated HA APIs
4. `mypy` - Type check with mypy (Python 3.14)
5. `basedpyright` - Type check with basedpyright (Python 3.14)
6. `tests` - Tests on Python 3.14
7. `coverage` - 80%+ coverage enforced

## Releasing

Releases are cut with `scripts/release.sh`, which handles the version bump,
the changelog section and the git tag in one step.

### Before you start

Record what changed under `## [Unreleased]` in `CHANGELOG.md`. The release
workflow publishes that section verbatim as the GitHub release notes, and it
fails the build if no section exists for the version being tagged.

### Cut the release

```bash
./scripts/release.sh patch    # 0.16.2 -> 0.16.3
./scripts/release.sh minor    # 0.16.2 -> 0.17.0
./scripts/release.sh major    # 0.16.2 -> 1.0.0
./scripts/release.sh          # prompts for an explicit version
```

The script reads the current version from `manifest.json` -- there is no
separate version file to keep in sync -- and then:

1. Refuses to run with uncommitted changes to tracked files
   (`git diff-index`), and warns if you are not on `main`. Untracked
   files do not block it
2. Bumps `version` in `manifest.json`
3. Rewrites `## [Unreleased]` into `## [Unreleased]` plus a dated
   `## [X.Y.Z] - YYYY-MM-DD` heading, and adds the compare links at the
   bottom of the changelog
4. Runs `.venv/bin/tox -e mypy`, aborting if type checking fails. Note
   the hardcoded path: the script requires a `.venv` in the repo root
   and does not fall back to `tox` on `PATH`
5. Shows the diff and asks for confirmation, rolling the files back if you
   decline
6. Creates the `chore: Release vX.Y.Z` commit and the `vX.Y.Z` tag

It stops there. Nothing is pushed, so you can inspect the commit and tag --
or delete them -- before publishing.

### Publish

```bash
git push && git push --tags
```

Pushing the tag is what triggers the release. `.github/workflows/release.yml`
extracts the `## [X.Y.Z]` changelog section, creates the GitHub release with
those notes, and attaches a `nwp500-<version>.zip` of the integration.

### Undoing an unpushed release

```bash
git tag -d vX.Y.Z
git reset --hard HEAD~1
```

The commit and tag already exist at this point -- nothing is left staged.
Once the tag is pushed the release is public, so review the commit
before pushing rather than after.

### CHANGELOG.md Format
```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New features

### Changed
- Changes to existing functionality

### Fixed
- Bug fixes

### Removed
- Removed features
```

## Updating nwp500-python Library

Use `scripts/update_nwp500_version.py`, which rewrites every pinned
reference in one pass:

```bash
python scripts/update_nwp500_version.py 9.2.0 9.2.1
```

It updates `manifest.json`, `requirements.txt`, `tox.ini` (all sections),
the install hints in `coordinator.py` and `config_flow.py`, `README.md`,
`.devcontainer/README.md`, `.github/copilot-instructions.md`, and adds or
updates the "Library Dependency: nwp500-python" entry under
`## [Unreleased]` in `CHANGELOG.md`.

Doing this by hand is error-prone: the version appears in eight files, and
the two install hints in `coordinator.py` and `config_flow.py` are easy to
miss individually, which leaves the runtime error paths telling users to
install different versions.

Afterwards:

1. Review the diff: `git diff`
2. Confirm nothing was missed: `grep -rn "nwp500-python==" --include="*.py" \
   --include="*.json" --include="*.txt" --include="*.ini" --include="*.md" .`
3. Read the release notes for behavior changes that affect this integration,
   and expand the CHANGELOG entry accordingly
4. Run `tox -e mypy --recreate` and `tox`

## Docker Development

### Run Home Assistant Locally
```bash
docker compose up -d
```
Access at `http://localhost:8123`

### API Testing
```bash
curl -H "Authorization: Bearer $(cat token.txt)" \
     http://localhost:8123/api/states | jq '.'
```

## Common Issues

### Import Errors
- Ensure dependencies: `uv pip install -r requirements.txt`

### Type Checking Failures
- Run `tox -e mypy --recreate` to reset cache
- Check for circular imports
- Verify `__future__` import at top of files

### Test Failures
- Check fixture availability in `tests/conftest.py`
- Run specific test: `pytest -vv tests/unit/test_file.py::test_function`
- Check for async issues: use `@pytest.mark.asyncio` decorator

### Coverage Too Low
- Add tests for uncovered lines
- View HTML report: `open htmlcov/index.html`

## Resources

- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [nwp500-python Documentation](https://nwp500-python.readthedocs.io/)
- [Pytest Documentation](https://docs.pytest.org/)
- [Pyright Configuration](https://github.com/microsoft/pyright/blob/main/docs/configuration.md)
- [Mypy Documentation](https://mypy.readthedocs.io/)
