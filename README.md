# Navien NWP500 Heat Pump Water Heater - Home Assistant Integration

[![CI](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml/badge.svg)](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml)

Home Assistant integration for Navien NWP500 Heat Pump Water Heaters.

## Features

- Set target temperature and switch operation modes (Heat Pump, Energy Saver, High Demand, Electric)
- Power control (on/off)
- 40+ temperature, power, energy, and status sensors
- 15+ binary sensors for system status and diagnostics
- Real-time updates via MQTT
- Automatic reconnection handling

## Installation

### HACS (Recommended)
1. Add this repository to HACS as a custom repository
2. Install "Navien NWP500" from HACS
3. Restart Home Assistant

### Manual Installation
1. Copy the `custom_components/nwp500` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. Go to Settings → Devices & Services
2. Click "Add Integration"
3. Search for "Navien NWP500"
4. Enter your Navien cloud account credentials

## Operation Modes

| Mode | Description |
|------|-------------|
| **Heat Pump** | Heat pump only - most efficient |
| **Energy Saver** | Hybrid mode for balance |
| **High Demand** | Hybrid mode for fast heating |
| **Electric** | Electric elements only |

## Entity Management

## Library Version

This integration currently uses **nwp500-python v6.0.0**.

For detailed changelog of the nwp500-python library versions, see [CHANGELOG.md](CHANGELOG.md#library-dependency-nwp500-python).

Key library features leveraged by this integration:
- **Event Emitter Pattern**: Real-time device status updates and connection monitoring
- **MQTT Reconnection**: Automatic recovery from connection interruptions
- **Token Persistence**: Faster startups with cached authentication
- **Exception Architecture**: Comprehensive error handling with specific exception types
- **Command Queuing**: Commands sent while disconnected are queued automatically

## Entity Registry

Most sensors are **disabled by default** to avoid cluttering your entity list. You can enable any sensor you need:

1. Settings → Devices & Services → Navien NWP500
2. Click on your device
3. Enable desired entities

**Enabled by Default:**
- Water heater entity
- Primary temperature sensors (tank, DHW)
- Error codes
- Power consumption
- Operation status

## Requirements

- Home Assistant 2024.10.0 or newer
- Navien NWP500 Heat Pump Water Heater
- Active NaviLink cloud account
- Device registered in NaviLink mobile app

## Support

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
- Ensure nwp500-python==6.0.0 is installed
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

# Run all checks
tox

# Run specific checks
tox -e mypy      # Type checking with mypy
tox -e pyright   # Type checking with pyright
tox -e coverage  # Tests with coverage (requires ≥80%)
tox -e hacs      # HACS validation
```

### Continuous Integration

All code changes are automatically validated through GitHub Actions CI pipeline:

- **Type Checking**: Both mypy and pyright must pass with zero errors
- **Unit Tests**: Comprehensive test suite with 80%+ coverage
- **Python Versions**: Tests run on Python 3.12 and 3.13

The CI workflow runs on:
- Push to `main` and `develop` branches
- All pull requests
- Manual workflow dispatch

View the CI status and detailed results in the [Actions tab](https://github.com/eman/ha_nwp500/actions).

### Type Checking

**Required before committing:** All code must pass both mypy and pyright type checking with zero errors.

```bash
# Run type checks (from project root with virtual environment active)
tox -e mypy      # Check with mypy
tox -e pyright   # Check with pyright
tox              # Run all checks including type checking
```

See `.github/copilot-instructions.md` for detailed type checking requirements and patterns.

## Contributing

This integration is actively maintained. Please report issues or contribute improvements through GitHub.

### Contribution Guidelines

1. Use the devcontainer for consistent development environment
2. **Required:** Pass all tox checks: `tox`
   - mypy and pyright type checking (0 errors)
   - Unit tests with ≥80% coverage
3. Follow existing code style and conventions
4. Add tests for new features
5. Update documentation as needed

All pull requests are automatically validated by CI. Ensure all checks pass before requesting review.

### Releases

This project follows [Semantic Versioning](https://semver.org/). For details on creating releases, see [RELEASING.md](RELEASING.md).

To create a release:
1. Update version in `manifest.json` and `CHANGELOG.md`
2. Commit changes
3. Create and push a tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z`
4. GitHub Actions will automatically create the release and build artifacts
=======
- [GitHub Issues](https://github.com/eman/ha_nwp500/issues)
- [Library Documentation](https://nwp500-python.readthedocs.io/)
>>>>>>> main

## License

This integration is released under the MIT License.