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

## Support

- [GitHub Issues](https://github.com/eman/ha_nwp500/issues)
- [Library Documentation](https://nwp500-python.readthedocs.io/)

## License

This integration is released under the MIT License.