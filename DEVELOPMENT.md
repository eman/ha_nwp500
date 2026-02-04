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
- Python 3.13+
- Home Assistant 2025.1.0+
- Virtual environment recommended

### Environment Setup

**Using Dev Container (Recommended)**
1. Install VS Code and [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
2. Install Docker Desktop
3. Open repo in VS Code → "Reopen in Container"

**Local Setup**
```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Testing

### Run Tests
```bash
# All checks (tests, type checking, coverage)
tox

# Specific environments
tox -e py313            # Test on Python 3.13
tox -e coverage         # Test with coverage (requires 80%+)
tox -e mypy             # Type check with mypy
tox -e pyright          # Type check with pyright

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
tox -e pyright
```
- Fast, IDE-integrated type checker
- Configuration in `pyrightconfig.json`
- Set to `basic` mode for balanced strictness
- Both mypy and pyright must pass in CI

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
4. `mypy` - Type check with mypy (Python 3.13)
5. `pyright` - Type check with pyright (Python 3.13)
6. `tests` - Tests on Python 3.13 + 3.14
7. `coverage` - 80%+ coverage enforced

## Releasing

### Checklist
1. All tests passing: `tox`
2. Update version in `manifest.json`
3. Update `CHANGELOG.md` with date: `date +%Y-%m-%d`
4. Commit: `git commit -m "Release vX.Y.Z"`
5. Tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
6. Push: `git push origin main && git push origin vX.Y.Z`

### Automated Workflow
After pushing tag, GitHub Actions will:
1. Extract changelog from `CHANGELOG.md`
2. Create GitHub release with changelog
3. Build and upload ZIP archive
4. Attach to release

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

Update comparison links at bottom:
```markdown
[Unreleased]: https://github.com/eman/ha_nwp500/compare/vX.Y.Z...HEAD
[X.Y.Z]: https://github.com/eman/ha_nwp500/releases/tag/vX.Y.Z
```

## Updating nwp500-python Library

When adopting a new version of the core library:

1. Update `manifest.json`: `"requirements": ["nwp500-python==X.Y.Z"]`
2. Update `requirements.txt`: `nwp500-python==X.Y.Z`
3. Update error messages in `coordinator.py` and `config_flow.py`
4. Update `CHANGELOG.md` under "Library Dependency: nwp500-python"
5. Update `README.md` version reference only (no detailed changelog)
6. Update `.devcontainer/README.md` if present
7. Update `.github/copilot-instructions.md`
8. **Critical**: Update `tox.ini` - ALL occurrences of version
9. Run type checking: `tox -e mypy --recreate`
10. Commit and test in dev environment

See `.github/copilot-instructions.md` for comprehensive update checklist.

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
- Ensure dependencies: `pip install -r requirements.txt`

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
