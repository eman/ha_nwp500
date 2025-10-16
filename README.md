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

## Library Version 1.1.5 Features

This integration uses nwp500-python v1.1.5 which includes:

### Enhanced DHW Mode Control
- Direct control via `mqtt.set_dhw_mode()`
- Improved mode mapping and reliability
- Better error handling and diagnostics

### Intuitive Temperature Control
- Uses `mqtt.set_dhw_temperature_display()` for direct display temperature setting
- No manual temperature conversion required
- What you set is what you see on the device

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

**Integration won't load:**
- Ensure nwp500-python==1.1.5 is installed
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

## Contributing

This integration is actively maintained. Please report issues or contribute improvements through GitHub.

## License

This integration is released under the MIT License.