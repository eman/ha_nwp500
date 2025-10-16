# GitHub Copilot Instructions for ha_nwp500

## Project Overview

This is a Home Assistant custom component that provides integration for Navien NWP500 Heat Pump Water Heaters. The integration is built around the `nwp500-python` library and provides comprehensive monitoring and control capabilities through Home Assistant.

## Core Dependencies

### Primary Library
- **nwp500-python**: The core Python library that handles communication with Navien NWP500 devices
  - GitHub Repository: https://github.com/eman/nwp500-python
  - Documentation: https://nwp500-python.readthedocs.io/en/stable/
  - Current Version: 1.1.5 (see `custom_components/nwp500/manifest.json`)

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

### Style Guidelines
- **PEP 8 Compliance**: All code must conform to PEP 8 standards
- **Line Length**: Maximum 100 characters per line
- **Type Hints**: Use type hints throughout (from `__future__ import annotations`)
- **Async/Await**: Use async/await patterns for I/O operations

### Testing & Linting
⚠️ **Important**: Linting and testing frameworks have not been configured yet, but code should be written with these standards in mind:
- Code should be testable and follow best practices
- Expect future implementation of pytest, black, flake8, or similar tools
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

### Unit Corrections
- Energy values from device are in **Wh** (watt-hours), not kWh
- Temperature values are in **Fahrenheit**
- Power values are in **Watts**
- Flow rates are in **GPM** (gallons per minute)

### Version Updates
When updating nwp500-python version:
1. Update `manifest.json` requirements
2. Update error messages in `coordinator.py` and `config_flow.py`
3. Update documentation references in `README.md`

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

## Future Considerations

### Planned Improvements
- Linting setup (black, flake8, mypy)
- Testing framework (pytest)
- CI/CD pipeline
- Enhanced error handling and diagnostics

### Compatibility
- Maintain backward compatibility with existing configurations
- Follow Home Assistant integration standards and best practices
- Support for future nwp500-python library versions