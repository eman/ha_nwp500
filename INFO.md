# Navien NWP500 Heat Pump Water Heater

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
- Event-driven architecture
- Automatic reconnection handling

🔧 **Comprehensive Status**
- 15+ binary sensors for boolean indicators
- Error code monitoring
- Component status tracking (compressor, heating elements, fans)
- Safety features (freeze protection, scald protection, anti-legionella)

## Quick Start

1. Install through HACS
2. Add integration via Settings → Devices & Services
3. Enter your Navien cloud credentials
4. Start monitoring and controlling your water heater!

## Operation Modes

| Mode | Description |
|------|-------------|
| **Heat Pump** | Heat pump only - most efficient |
| **Energy Saver** | Hybrid mode for balance |
| **High Demand** | Hybrid mode for fast heating |
| **Electric** | Electric elements only |

## Entity Management

Most sensors are **disabled by default** to keep your entity list clean. Enable the ones you need through the device page in Home Assistant.

**Enabled by Default:**
- Water heater entity
- Primary temperature sensors
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
- [Documentation](https://github.com/eman/ha_nwp500)
- [Library Documentation](https://nwp500-python.readthedocs.io/)
