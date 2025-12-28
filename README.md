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

#### Safety & System Status (Disabled by Default)
- Freeze Protection Active
- Scald Protection Active
- Anti-Legionella Active
- Air Filter Alarm
- Error Buzzer Active

#### System Components (Disabled by Default)
- EEV Active
- Evaporator Fan Running
- Current Heat Use
- Overheat Protection Enabled
- Program Reservation Active

## Installation

### Prerequisites
- Navien NWP500 device installed and operational
- [NaviLink Smart Control](https://www.navien.com/en/navilink) account with device registered
- Home Assistant 2025.1+ (requires Python 3.13-3.14)

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

### Reservation Scheduling

The integration provides services for programming automatic mode and temperature changes:

#### Services

| Service | Description |
|---------|-------------|
| `nwp500.set_reservation` | Create a single reservation with user-friendly parameters |
| `nwp500.update_reservations` | Replace all reservations (advanced) |
| `nwp500.clear_reservations` | Remove all reservation schedules |
| `nwp500.request_reservations` | Request current reservation data from device |

#### Example: Set a Weekday Morning Reservation

```yaml
service: nwp500.set_reservation
target:
  device_id: your_device_id
data:
  enabled: true
  days:
    - Monday
    - Tuesday
    - Wednesday
    - Thursday
    - Friday
  hour: 6
  minute: 30
  mode: high_demand
  temperature: 140
```

This creates a reservation that activates High Demand mode at 6:30 AM on weekdays with a target temperature of 140°F.

## Library Version

This integration currently uses **nwp500-python v7.2.2**.

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

## Energy Monitoring
- Real-time power consumption
- Energy capacity tracking
- Efficiency monitoring



## License

This integration is released under the MIT License.