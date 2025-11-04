# Navien NWP500 Heat Pump Water Heater - Home Assistant Integration

[![CI](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml/badge.svg)](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

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

### Prerequisites
- Navien NWP500 device installed and operational
- [NaviLink Smart Control](https://www.navien.com/en/navilink) account with device registered
- Home Assistant 2024.10+

### HACS (Recommended)
1. Install [HACS](https://hacs.xyz/) if not already installed
2. Go to HACS → Integrations → ⋮ menu → Custom repositories
3. Add: `https://github.com/eman/ha_nwp500` (Category: Integration)
4. Find "Navien NWP500" and install
5. Restart Home Assistant

### Manual Installation
1. Clone: `git clone https://github.com/eman/ha_nwp500.git`
2. Copy `ha_nwp500/custom_components/nwp500` to `/config/custom_components/`
3. Restart Home Assistant

## Configuration

1. Settings → Devices & Services → Create Integration
2. Search "Navien NWP500"
3. Enter NaviLink email and password
4. Entities will be created automatically

### Operation Modes

The integration supports these DHW operation modes:

- **Heat Pump**: Heat pump only - most efficient
- **Electric**: Electric elements only
- **Energy Saver**: Hybrid mode for balance
- **High Demand**: Hybrid mode for fast heating

## Library Version

This integration currently uses **nwp500-python v6.0.0**.

For version history and changelog, see [CHANGELOG.md](CHANGELOG.md#library-dependency-nwp500-python).

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

## Contributing

This integration is actively maintained. Contributions are welcome!

For development setup, testing, releases, and detailed contribution guidelines, see [DEVELOPMENT.md](DEVELOPMENT.md).

**Quick start for contributors:**
1. Clone the repo
2. Follow setup in DEVELOPMENT.md
3. Run `tox` to validate changes
4. Submit a pull request

All pull requests are automatically validated by CI and must pass all checks before merge.

## License

This integration is released under the MIT License.