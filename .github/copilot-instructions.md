# GitHub Copilot Instructions for ha_nwp500

## Project Overview

This is a Home Assistant custom component that provides integration for Navien NWP500 Heat Pump Water Heaters. The integration is built around the `nwp500-python` library and provides comprehensive monitoring and control capabilities through Home Assistant.

## Core Dependencies

### Primary Library
- **nwp500-python**: The core Python library that handles communication with Navien NWP500 devices
  - **GitHub Repository**: https://github.com/eman/nwp500-python
  - **Documentation**: https://nwp500-python.readthedocs.io/en/stable/
  - **PyPI Package**: https://pypi.org/project/nwp500-python/
  - **Current Version**: 6.0.0 (see `custom_components/nwp500/manifest.json`)
  - **Note**: When instructions refer to "adopting a new library version" or "updating the library," they mean updating nwp500-python

### Home Assistant Integration
- Platform: Home Assistant Custom Component
- Domain: `nwp500`
- Integration Type: Device-based cloud polling integration
- Supported Platforms: water_heater, sensor, binary_sensor, switch, number

## Architecture

### Key Files
- `manifest.json`: Component metadata and dependencies
- `__init__.py`: Component initialization and setup
- `config_flow.py`: Configuration flow for UI setup
- `coordinator.py`: Data update coordinator for API communication
- `const.py`: Constants, mappings, and sensor definitions
- Platform files: `water_heater.py`, `sensor.py`, `binary_sensor.py`, `switch.py`, `number.py`

### Data Flow
1. Authentication via Navien cloud credentials
2. Device discovery through API
3. MQTT connection for real-time updates
4. Periodic data updates via coordinator
5. Entity state updates in Home Assistant

## Code Standards

### General Best Practices
- **Always Get Current Date**: Never assume or hardcode dates. Always use `date +%Y-%m-%d` command to get the correct date for changelogs, releases, or any date-dependent content.
- **No Summary Documents**: Do not create any summary documents (e.g., `SUMMARY.md`, `CHANGES.md`, `ANALYSIS.md`, quick summaries, etc.) after completing tasks or fixes unless explicitly requested by the user. Code changes, git commits, and documentation updates are sufficient. Do not provide summary responses in the chat—be concise and direct instead.
- **Changelog Management**: 
  - **DO NOT** add library version history or detailed changelogs to `README.md`
  - All library dependency changes belong in `CHANGELOG.md` under "Library Dependency: nwp500-python" section
  - `README.md` should only reference the current version and link to CHANGELOG.md
  - Keep README focused on features, installation, and usage - not version history

### Style Guidelines
- **PEP 8 Compliance**: All code must conform to PEP 8 standards
- **Line Length**: Maximum 100 characters per line
- **Type Hints**: Use type hints throughout (from `__future__ import annotations`)
- **Async/Await**: Use async/await patterns for I/O operations

### Testing & Linting
**Type Checking with mypy**:
- **Configuration**: `mypy.ini` and `tox.ini`
- **Run Command**: `tox -e mypy` (from project root with virtual environment)
- **Setup**: Virtual environment in `.venv/` with tox and mypy installed
- **Always run mypy**: Before completing any task that modifies Python code
- **Standard**: Must pass with zero errors before committing changes

**Future Testing**:
- Expect future implementation of pytest for unit tests
- Code should be testable and follow best practices
- Write defensive code with proper error handling

## Device Integration Details

### Supported Device Types
- Navien NWP500 Heat Pump Water Heaters (Device Type ID: 52)

### Operation Modes
The integration maps nwp500-python operation modes to Home Assistant states:
- `ENERGY_SAVER` (3) → `eco`
- `HEAT_PUMP` (1) → `heat_pump` 
- `HIGH_DEMAND` (4) → `high_demand`
- `ELECTRIC` (2) → `electric`

### Entity Coverage
- **40+ Sensors**: Temperature, power, status, diagnostic sensors
- **15+ Binary Sensors**: Boolean status indicators
- **Water Heater Entity**: Primary control interface
- **Switch Entities**: Power control
- **Number Entities**: Temperature setpoints

## Common Tasks

### Adding New Sensors
1. Update `const.py` with sensor definition
2. Add sensor description in appropriate platform file
3. Ensure proper units and device classes
4. Set appropriate `entity_registry_enabled_default` value

### Updating nwp500-python Library Version

When adopting a new version of the nwp500-python library (the primary dependency), update version references in the following files:

#### 1. **`custom_components/nwp500/manifest.json`** (REQUIRED)
   - Update the `requirements` array: `"nwp500-python==X.Y.Z"`
   - This is the authoritative version that Home Assistant uses

#### 2. **`requirements.txt`** (REQUIRED)
   - Update the version: `nwp500-python==X.Y.Z`
   - Used for development environment setup

#### 3. **`custom_components/nwp500/coordinator.py`** (REQUIRED)
   - Update the error message in the ImportError handler
   - Search for: `"pip install nwp500-python=="`
   - Update to new version

#### 4. **`custom_components/nwp500/config_flow.py`** (REQUIRED)
   - Update the error message in the library availability check
   - Search for: `"pip install nwp500-python=="`
   - Update to new version

#### 5. **`CHANGELOG.md`** (REQUIRED)
   - Add new entry in "Library Dependency: nwp500-python" section
   - Document version number with date: `### vX.Y.Z (YYYY-MM-DD)`
   - Include breaking changes, improvements, and migration notes
   - Add link to GitHub release notes
   - Check release notes: https://github.com/eman/nwp500-python/releases

#### 6. **`README.md`** (REQUIRED)
   - Update only the version number in "Library Version" section
   - DO NOT add detailed changelog information to README
   - README should only show current version and link to CHANGELOG.md
   - Update troubleshooting section version reference if present

#### 7. **`.devcontainer/README.md`** (RECOMMENDED)
   - Update version reference in the "Python Packages" section
   - Search for: `nwp500-python==`

#### 8. **`.github/copilot-instructions.md`** (THIS FILE)
   - Update "Current Version" in the "Primary Library" section

#### 9. **`tox.ini`** (CRITICAL - OFTEN MISSED!)
   - Update version in `[testenv]` deps section
   - Update version in `[testenv:pyright]` deps section
   - **Important**: Ensure `[testenv:mypy]` includes the library for type resolution
   - Search for all occurrences: `nwp500-python==`
   - CI will fail if this file is not updated!

#### Workflow for Version Updates

```bash
# 1. Check for new versions
pip index versions nwp500-python

# 2. Check release notes
curl -s https://api.github.com/repos/eman/nwp500-python/releases | jq '.[0]'

# 3. Update all files listed above

# 4. Run type checking (MANDATORY)
.venv/bin/tox -e mypy

# 5. Test in Docker container (if applicable)
docker compose up -d
```

#### Version Update Checklist

- [ ] Check PyPI for latest version
- [ ] Review GitHub release notes for breaking changes
- [ ] Update `manifest.json` requirements
- [ ] Update `requirements.txt`
- [ ] Update error messages in `coordinator.py`
- [ ] Update error messages in `config_flow.py`
- [ ] **Add entry to `CHANGELOG.md` under "Library Dependency: nwp500-python"**
- [ ] Update `README.md` version number only (no detailed changelog)
- [ ] Update `.devcontainer/README.md`
- [ ] Update this file's "Current Version"
- [ ] **Update `tox.ini` - ALL occurrences (CRITICAL for CI)**
- [ ] Run `tox -e mypy --recreate` - must pass with zero errors
- [ ] Test in development environment (optional but recommended)

**Common Mistakes**: 
- Forgetting to update `tox.ini` causes CI failures because type checkers cannot resolve the new library APIs
- Adding detailed changelog to `README.md` instead of `CHANGELOG.md` - keep README clean and focused
- Always search for ALL occurrences of `nwp500-python==` in the project

### Unit Corrections
- Energy values from device are in **Wh** (watt-hours), not kWh
- Temperature values are in **Fahrenheit**
- Power values are in **Watts**
- Flow rates are in **GPM** (gallons per minute)

## Development Environment

### Docker Container Setup
The project includes a Docker Compose configuration for running Home Assistant with this integration:

- **Compose File**: `compose.yaml`
- **Container Name**: `nwp500`
- **Image**: `ghcr.io/home-assistant/home-assistant:stable`
- **Port**: `8123` (mapped to localhost:8123)
- **Volume Mount**: `./custom_components` → `/config/custom_components`

Start the environment:
```bash
docker compose up -d
```

### API Access & Debugging
- **Home Assistant API**: `http://localhost:8123/api/states`
- **Authentication Token**: Stored in `token.txt`
- **API Testing**: Use `curl` with Bearer token:
  ```bash
  curl -H "Authorization: Bearer $(cat token.txt)" \
       -H "Content-Type: application/json" \
       http://localhost:8123/api/states | jq '.'
  ```

### Container Development Workflow
1. Make code changes in `custom_components/nwp500/`
2. Changes are immediately reflected in the container via volume mount
3. Restart Home Assistant service or container to reload integration
4. Use API to verify sensor values and entity states

### File Structure
```
custom_components/nwp500/
├── __init__.py              # Component setup
├── manifest.json            # Component metadata
├── config_flow.py          # Configuration UI
├── coordinator.py          # Data coordinator
├── const.py               # Constants and mappings
├── entity.py              # Base entity class
├── water_heater.py        # Water heater platform
├── sensor.py              # Sensor platform
├── binary_sensor.py       # Binary sensor platform
├── switch.py              # Switch platform
├── number.py              # Number platform
└── translations/          # UI translations
```

## Important Notes

### Entity Registry Defaults
- Most sensors are **disabled by default** to avoid cluttering the entity list
- Only essential sensors (temperatures, power, errors) are enabled by default
- Users can enable additional sensors as needed

### Error Handling
- Always check for library availability with try/except ImportError
- Provide helpful error messages referencing installation commands
- Use Home Assistant's UpdateFailed exception for coordinator errors

### Documentation References
- Device status fields: Check nwp500-python documentation for accurate field descriptions and units
- API methods: Refer to library documentation for available control methods
- MQTT events: Integration uses event emitter functionality from v1.1.1+

## Task Completion Requirements

### MANDATORY: Type Checking

**Before completing ANY task that modifies Python code:**

```bash
# Run from project root
.venv/bin/tox -e mypy
```

**Must pass with zero errors.** If errors occur:
1. Review error messages carefully
2. Apply fixes following patterns in `TYPE_CHECKING.md`
3. Re-run until clean
4. See `MYPY_SETUP_SUMMARY.md` for common issues and solutions

### Task Completion Checklist

For every task involving Python code changes:

- [ ] Code changes complete
- [ ] Type hints added to new code  
- [ ] **`tox -e mypy` passes with zero errors** ← MANDATORY
- [ ] Code tested in Docker container (when applicable)
- [ ] Documentation updated (if applicable)
- [ ] Changes verified in running Home Assistant instance

## Future Considerations

### Planned Improvements
- Linting setup (black, flake8, mypy)~~ mypy completed
- Testing framework (pytest)
- CI/CD pipeline with automated type checking
- Enhanced error handling and diagnostics

### Compatibility
- Maintain backward compatibility with existing configurations
- Follow Home Assistant integration standards and best practices
- Support for future nwp500-python library versions