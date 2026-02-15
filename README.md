# Navien NWP500 Water Heater - Home Assistant Integration

**Version**: 0.2.1

[![CI](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml/badge.svg)](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

This custom integration provides comprehensive monitoring and control of Navien NWP500 Heat Pump Water Heaters through Home Assistant. It communicates with the device via the Navien cloud API and establishes a local MQTT connection for real-time updates.

## Key Features

*   **Full Control**: Set target temperature, change operation modes, and toggle power.
*   **Real-time Monitoring**: View current water temperature, power consumption, and device status.
*   **Energy Management**: Track energy usage and efficiency.
*   **Safety Alerts**: Monitor for errors, leaks, and system warnings.
*   **Scheduling**: Advanced reservation system for automated mode/temperature changes.

## Installation

### HACS (Recommended)
1.  Open HACS in Home Assistant.
2.  Go to **Integrations** > **⋮** > **Custom repositories**.
3.  Add `https://github.com/eman/ha_nwp500` with category **Integration**.
4.  Search for "Navien NWP500" and install.
5.  Restart Home Assistant.

### Manual Installation
1.  Clone this repository.
2.  Copy `custom_components/nwp500` to your `config/custom_components/` directory.
3.  Restart Home Assistant.

## Configuration

1.  Go to **Settings** > **Devices & Services**.
2.  Click **Add Integration** and search for "Navien NWP500".
3.  Enter your **NaviLink** email and password.
4.  The integration will discover your devices and create entities.

## Usage Guide

### Operation Modes
The integration maps the device's modes to Home Assistant's water heater operation modes:

*   **Heat Pump** (`heat_pump`): Uses only the heat pump. Most efficient.
*   **Energy Saver** (`eco`): Hybrid mode balancing efficiency and recovery time.
*   **High Demand** (`high_demand`): Uses heat pump and electric elements for fastest recovery.
*   **Electric** (`electric`): Uses only electric elements.

### Sensors & Entities
To keep your system clean, **most advanced sensors are disabled by default**.

**Enabled by Default:**
*   Water Heater entity (Control)
*   Tank & DHW Temperatures
*   Current Power & Energy Status
*   Error Codes & Basic Status

**Available to Enable:**
*   **Diagnostics**: Compressor temps, fan RPM, flow rates, refrigerant pressures.
*   **Internal Status**: EEV steps, mixing rates, specific component status.
*   **Safety**: Leak detection, freeze protection, scald warnings.

To enable these, go to the device page in Home Assistant, look for "Disabled" entities, and enable the ones you need.

### Reservation Scheduling
You can program the water heater to change modes or temperatures at specific times using the `nwp500.set_reservation` service.

**Example: Weekday Morning Boost**
Set the heater to "High Demand" at 140°F every weekday morning at 6:30 AM.

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

## Automation Examples

Here are some ways to automate your water heater:

### 1. Solar Energy Dump
Maximize self-consumption by overheating the water when you have excess solar power.

```yaml
alias: "Water Heater - Solar Boost"
trigger:
  - platform: numeric_state
    entity_id: sensor.solar_export_power
    above: 1000 # Watts
action:
  - service: water_heater.set_operation_mode
    target:
      entity_id: water_heater.navien_nwp500
    data:
      operation_mode: "high_demand"
  - service: water_heater.set_temperature
    target:
      entity_id: water_heater.navien_nwp500
    data:
      temperature: 140
```

### 2. Leak Detection Alert
Notify your phone immediately if the water heater detects a leak.
*Note: Ensure `binary_sensor.water_leak_detected` is enabled.*

```yaml
alias: "Water Heater - Leak Alert"
trigger:
  - platform: state
    entity_id: binary_sensor.navien_nwp500_water_leak_detected
    to: "on"
action:
  - service: notify.mobile_app_phone
    data:
      message: "CRITICAL: Water leak detected at Water Heater!"
      data:
        push:
          sound:
            name: default
            critical: 1
            volume: 1.0
```

### 3. Vacation Mode
Automatically set the water heater to a low energy state when you leave home.

```yaml
alias: "Water Heater - Away Mode"
trigger:
  - platform: state
    entity_id: group.family
    to: "not_home"
action:
  - service: water_heater.set_operation_mode
    target:
      entity_id: water_heater.navien_nwp500
    data:
      operation_mode: "eco" # or "off"
```

## Library Version
This integration uses **nwp500-python v7.4.6**.
For version history, see [CHANGELOG.md](CHANGELOG.md#library-dependency-nwp500-python).

## License
This integration is released under the MIT License.
