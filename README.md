# Navien NWP500 Heat Pump Water Heater - Home Assistant Integration

[![CI](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml/badge.svg)](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml)

Comprehensive monitoring and control of Navien NWP500 Heat Pump Water Heaters through Home Assistant.

## Features

✨ **Water Heater Control**
- Set target temperature
- Switch between Heat Pump, Energy Saver, High Demand, and Electric modes
- Power control (on/off)

📊 **40+ Sensors**
- Temperature monitoring (tank, DHW, ambient, etc.)
- Power consumption and energy tracking
- System status and diagnostics
- Performance metrics

🔄 **Real-time Updates**
- MQTT-based instant status updates
- Automatic reconnection handling

🔧 **15+ Binary Sensors**
- System status indicators
- Safety features (freeze protection, scald protection, anti-legionella)
- Component status tracking (compressor, heating elements, fans)

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

Most sensors are **disabled by default** to keep your entity list clean. You can enable any sensor you need:

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

## Troubleshooting

### "No devices found" error during setup

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

### Integration won't load

- Ensure all dependencies are properly installed
- Check Home Assistant logs for specific errors
- Restart Home Assistant

### No device status updates

- Verify device connectivity to Navien cloud
- Ensure proper credentials were entered
- Check the device's WiFi connection

## Support

- [GitHub Issues](https://github.com/eman/ha_nwp500/issues)
- [Library Documentation](https://nwp500-python.readthedocs.io/)

## License

This integration is released under the MIT License.