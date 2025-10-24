# Navien NWP500 Heat Pump Water Heater - Home Assistant Integration

This custom integration provides comprehensive monitoring and control of Navien NWP500 Heat Pump Water Heaters through Home Assistant.

## Features

### Water Heater Entity
- **Temperature Control**: Set target DHW (Domestic Hot Water) temperature
- **Operation Mode Control**: Switch between Heat Pump, Energy Saver, High Demand, and Electric modes
- **Real-time Status**: Current temperature, target temperature, and operation state
- **Power Control**: Turn the water heater on/off

### Comprehensive Sensor Coverage
The integration provides over 40 sensors covering all device status fields:

#### Temperature Sensors (Enabled by Default)
- Outside Temperature
- Tank Upper Temperature  
- Tank Lower Temperature
- DHW Temperature
- Current Power

#### Temperature Sensors (Disabled by Default)
- Discharge Temperature
- Suction Temperature
- Evaporator Temperature
- Ambient Temperature
- DHW Temperature 2
- Current Inlet Temperature
- Freeze Protection Temperature
- Target/Current Super Heat

#### Power & Energy Sensors
- Current Power (enabled)
- Total Energy Capacity (disabled)
- Available Energy Capacity (disabled)

#### Status Sensors
- DHW Charge Percentage (enabled)
- WiFi RSSI (disabled)
- Error Code (enabled)
- Sub Error Code (disabled)
- Operation Mode (enabled)
- DHW Operation Setting (enabled)

#### Flow Rate & Performance
- Current DHW Flow Rate (disabled)
- Cumulated DHW Flow Rate (disabled)
- Target/Current Fan RPM (disabled)
- Fan PWM (disabled)
- Mixing Rate (disabled)

### Binary Sensors
Comprehensive boolean status indicators:

#### Primary Status (Enabled by Default)
- Operation Busy
- DHW In Use
- Compressor Running
- Upper Electric Element On
- Lower Electric Element On

#### Safety & Diagnostics (Disabled by Default)
- Freeze Protection Active
- Scald Protection Active
- Anti-Legionella Active
- Air Filter Alarm
- Error Buzzer Active

#### System Components (Disabled by Default)
- EEV Active
- Evaporator Fan Running
- Current Heat Use
- Eco Mode Active
- Program Reservation Active

## Installation

### HACS (Recommended)
1. Add this repository to HACS as a custom repository
2. Install "Navien NWP500" from HACS
3. Restart Home Assistant

### Manual Installation
1. Copy the `custom_components/nwp500` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

### Setup via UI
1. Go to Settings → Devices & Services
2. Click "Add Integration"
3. Search for "Navien NWP500"
4. Enter your Navien cloud account credentials

### Operation Mode Mapping

The integration maps nwp500-python library modes to Home Assistant water heater states:

| Library Mode | HA Water Heater State | Description |
|--------------|----------------------|-------------|
| ENERGY_SAVER (3) | eco | Energy efficient hybrid mode |
| HEAT_PUMP (1) | heat_pump | Heat pump only mode |
| HIGH_DEMAND (4) | high_demand | High performance hybrid mode |
| ELECTRIC (2) | electric | Electric elements only |

## Library Version 3.1.2

This integration uses nwp500-python v3.1.2 which includes:

### Improvements in v3.1.2
- **Authentication**: Fixes 401 authentication errors with automatic token refresh
- **Reliability**: Improved session management and token handling

### Improvements in v3.1.1
- **Documentation**: PEP 257 compliant docstrings for better IDE support
- **Code Quality**: 80 character line limit for improved readability
- **Comprehensive Documentation**: Enhanced API documentation

### Breaking Changes from v2.0.0 (in v3.0.0)
- **Removed**: Deprecated `OperationMode` enum (fully replaced by `DhwOperationSetting` and `CurrentOperationMode`)
- **Removed**: Migration helper functions from v2.x
- **Clean API**: Streamlined enum structure for better type safety

### Enhanced Type Safety
- **DhwOperationSetting**: User-configured mode preferences (Heat Pump, Electric, Energy Saver, High Demand, Vacation, Power Off)
- **CurrentOperationMode**: Real-time operational states (Standby, Heat Pump Mode, Hybrid Efficiency Mode, Hybrid Boost Mode)
- **Better IDE Support**: More specific enum types prevent accidental misuse

### Continued Features from v2.x

### Enhanced MQTT Reconnection and Reliability
- **Improved MQTT Reconnection**: Fixes connection interruption issues with AWS MQTT (AWS_ERROR_MQTT_UNEXPECTED_HANGUP)
- **Automatic Recovery**: Automatically handles AWS_ERROR_MQTT_CANCELLED_FOR_CLEAN_SESSION errors
- **Command Queuing**: Commands sent while disconnected are queued and sent automatically when reconnected
- **Exponential Backoff**: Robust reconnection with intelligent retry logic

### Anti-Legionella Protection Control
- **Monitoring**: Track periodic disinfection cycles (140°F heating)
- **Safety Compliance**: Monitor legionella prevention status
- **Operational Status**: Track when anti-legionella protection is active

### Reservation Management
- **Schedule Control**: Schedule automatic temperature and mode changes
- **Program Management**: Monitor and control reservation settings
- **Automated Operations**: Set up timed operations for energy efficiency

### Event Emitter Integration
The integration leverages the new event emitter functionality for:
- Real-time device status updates
- Connection status monitoring
- Better error handling and recovery

### Comprehensive Device Status Fields
All device status fields from the v1.1.1 library are mapped to entities:
- Temperature sensors with proper unit conversions
- Boolean status indicators
- Diagnostic and performance metrics
- Energy monitoring capabilities

## Entity Registry

Most sensors are **disabled by default** to avoid cluttering your entity list. You can enable any sensor you need through:

1. Settings → Devices & Services → Navien NWP500
2. Click on your device
3. Enable desired entities

**Enabled by Default:**
- Water heater entity
- Primary temperature sensors
- Error codes
- Power consumption
- DHW charge percentage
- Operation status sensors

## Advanced Features

### Real-time Updates
- MQTT-based real-time status updates
- Event-driven architecture for immediate state changes
- Automatic reconnection handling

### Diagnostics
- Comprehensive diagnostic sensors for troubleshooting
- Error code mapping and monitoring
- System performance metrics

### Energy Monitoring
- Real-time power consumption
- Energy capacity tracking
- Efficiency monitoring

## Troubleshooting

### Common Issues

**"No devices found" error during setup:**

This error means authentication succeeded, but the Navien cloud API returned an empty device list. To resolve:

1. **Verify device in NaviLink app**: 
   - Open the NaviLink Smart Control mobile app
   - Ensure your NWP500 device appears in the app
   - Verify you can control the device from the app

2. **Check device online status**:
   - Ensure the device is powered on
   - Verify WiFi connection is active on the device
   - Check the device's WiFi indicator light

3. **Re-register device if necessary**:
   - In the NaviLink app, try removing and re-adding the device
   - Follow the device setup wizard in the app
   - Wait a few minutes for registration to complete

4. **Verify account credentials**:
   - Ensure you're using the correct Navien account
   - If you have multiple accounts, verify which one has the device registered
   - Try logging out and back into the NaviLink app

5. **Check for multiple users**:
   - Only the account that registered the device may see it in the API
   - Contact the device owner if you're using a shared device

**Integration won't load:**
- Ensure nwp500-python==3.1.2 is installed
- Check Home Assistant logs for specific errors

**No device status updates:**
- Verify MQTT connection in logs
- Check device connectivity to Navien cloud
- Ensure proper credentials

**Operation mode control not working:**
- Verify device supports DHW mode control
- Check MQTT connection status
- Review device error codes

### Diagnostic Logging

Add to `configuration.yaml` for detailed logging:

```yaml
logger:
  logs:
    custom_components.nwp500: debug
    nwp500: debug
```

## Version History

### v1.1.1 Integration Update
- Updated to nwp500-python v1.1.1
- Added comprehensive sensor coverage (40+ sensors)
- Added binary sensor platform
- Implemented proper DHW mode control
- Added event emitter integration
- Improved operation mode mapping
- Enhanced error handling and diagnostics

## Development

### Development Container (Recommended)

The easiest way to start developing is using VS Code Dev Containers:

1. Install [VS Code](https://code.visualstudio.com/) and the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
2. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
3. Open this repository in VS Code
4. Click "Reopen in Container" when prompted (or use Command Palette → `Dev Containers: Reopen in Container`)
5. VS Code will build a complete development environment with all dependencies

**What's included:**
- Python 3.12 with all project dependencies
- Type checkers (mypy, pyright)
- Testing tools (pytest, tox)
- Linters and formatters (ruff, black)
- Docker-in-Docker for running Home Assistant
- Pre-configured VS Code extensions and settings

See [.devcontainer/README.md](.devcontainer/README.md) for more details.

### Local Development

If not using devcontainer:

```bash
# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run type checking (required before commits)
tox -e mypy

# Run tests
pytest
```

### Type Checking

**Required before committing:** All code must pass mypy type checking with zero errors.

```bash
# Run mypy (from project root with virtual environment active)
tox -e mypy
```

See project instructions for detailed type checking requirements and setup.

## Contributing

This integration is actively maintained. Please report issues or contribute improvements through GitHub.

### Contribution Guidelines

1. Use the devcontainer for consistent development environment
2. Ensure all tests pass: `pytest`
3. **Required:** Pass type checking: `tox -e mypy`
4. Follow existing code style and conventions
5. Add tests for new features
6. Update documentation as needed

### Releases

This project follows [Semantic Versioning](https://semver.org/). For details on creating releases, see [RELEASING.md](RELEASING.md).

To create a release:
1. Update version in `manifest.json` and `CHANGELOG.md`
2. Commit changes
3. Create and push a tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z`
4. GitHub Actions will automatically create the release and build artifacts

## License

This integration is released under the MIT License.