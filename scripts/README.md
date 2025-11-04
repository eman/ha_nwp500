# Build and Quality Assurance Scripts

This directory contains utility scripts for maintaining code quality and compatibility with Home Assistant.

## check_deprecated_apis.py

Scans the custom component for deprecated Home Assistant APIs and patterns that should not be used in modern integrations.

### Usage

```bash
python3 scripts/check_deprecated_apis.py
```

### What It Checks

- **Deprecated API calls**: Methods that were removed or replaced in newer HA versions
- **Old import patterns**: Imports from modules that have been refactored
- **Obsolete constants**: Constants that were removed or replaced with enums
- **Legacy patterns**: Patterns that don't follow current HA best practices

### Examples

**Will flag:**
```python
async_get_registry()          # Use specific registries
get_registry()                # Use async_get instead
ENTITY_ID_ALL_ATTRS          # Removed in 2022.2+
EntityComponent()             # Use entity platform instead
```

**Will not flag (whitelisted):**
```python
async_add_entities()          # Standard platform method
STATE_UNKNOWN                 # Valid state constants
from homeassistant.const      # Standard imports
```

### Exit Codes

- **0**: No violations found (check passes)
- **1**: Violations found (check fails)

### Output Examples

**Pass:**
```
No deprecated Home Assistant APIs detected
All imports and patterns are compatible with current HA
```

**Fail:**
```
Deprecated API Check Results
============================================================

custom_components/nwp500/example.py
  Line 42: ERROR: Use specific registries (2022.8+)
    > registry = await async_get_registry(hass)

============================================================
Summary: 1 error(s), 0 warning(s)
Deprecated APIs found - please fix before release
```

### CI Integration

This script is automatically run as part of the CI/CD pipeline (see `.github/workflows/ci.yml`):

- Runs on all pushes to `main` and `develop` branches
- Runs on all pull requests
- Must pass before merging (required status check)

### Severity Levels

- **ERROR**: Deprecated API that must be fixed. Indicates HA version incompatibility.
- **WARNING**: Potentially deprecated pattern that should be reviewed. May work but not recommended.

### Version Information

API deprecations are tracked by Home Assistant version. The script documents when each API was deprecated.
