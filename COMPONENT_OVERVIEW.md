# Navien NWP500 Component Technical Overview

This document provides a technical overview of the Home Assistant custom component for Navien NWP500 heat pump water heaters.

## Architecture

The component follows Home Assistant best practices and uses the `nwp500-python` library for communication with the Navilink Smart Control cloud service.

### Core Components

1. **Config Flow** (`config_flow.py`)
   - User credential collection (email/password)
   - Authentication validation against Navien API
   - Device discovery and account verification

2. **Data Coordinator** (`coordinator.py`)
   - Manages authentication with NavienAuthClient
   - Handles device discovery via NavienAPIClient
   - Establishes MQTT connection for real-time updates
   - Coordinates periodic status requests every 30 seconds
   - Manages device control commands

3. **Base Entity** (`entity.py`)
   - Common functionality for all NWP500 entities
   - Device information and identification
   - Availability and state management

### Platform Entities

4. **Water Heater** (`water_heater.py`)
   - Main control interface following Home Assistant water heater standards
   - Temperature control (80°F - 140°F range)
   - Operation mode management
   - Power on/off functionality

5. **Sensors** (`sensor.py`)
   - Temperature sensors (tank, outside, heat pump inlet/outlet)
   - Power consumption and energy monitoring
   - Operational status and error reporting
   - Compressor frequency and COP

6. **Switches** (`switch.py`)
   - Power control switch
   - Freeze protection status (read-only)

7. **Numbers** (`number.py`)
   - Target temperature slider control
   - Alternative to water heater temperature interface

## Communication Flow

### Authentication
```
User Credentials → NavienAuthClient → JWT Tokens → API Access
                                   ↓
                            Automatic Refresh
```

### Data Updates
```
MQTT Request (30s) → Device Status → Home Assistant Entities
        ↓
REST API Fallback ← Connection Issues
```

### Device Control
```
Home Assistant Command → MQTT Control Message → Device Response → Status Update
```

## Data Sources

The component utilizes multiple data sources from the nwp500-python library:

### REST API (NavienAPIClient)
- Device discovery and listing
- Initial authentication
- Fallback for status updates
- Account information

### MQTT (NavienMqttClient)
- Real-time status updates
- Device control commands
- Status request messaging
- AWS IoT Core WebSocket connection

## Key Features Implementation

### Real-time Updates
- Uses `request_device_status()` utility every 30 seconds
- MQTT command queue ensures no lost requests during disconnections
- Automatic reconnection with queued command replay

### Authentication Management
- JWT token automatic refresh
- Secure credential storage in Home Assistant
- Session persistence across restarts

### Error Handling
- Graceful fallback from MQTT to REST API
- Connection retry logic
- Error code monitoring and reporting

### Device Control
The component supports these control operations:
- **Power Control**: `send_power_control(device, power_on)`
- **Temperature**: `send_dhw_temperature_control(device, temperature)`
- **DHW Mode**: `send_dhw_mode_control(device, mode)`

## Configuration Schema

```python
STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
})
```

## Status Fields Mapping

Based on the nwp500-python documentation, the component maps these key fields:

| Library Field | Home Assistant Entity | Description |
|---------------|----------------------|-------------|
| `tankTemperature` | Current Temperature | Tank water temperature |
| `dhwTargetTemperature` | Target Temperature | Desired water temperature |
| `operationMode` | Operation Mode | Heat pump operating mode (1-5) |
| `powerStatus` | Power Switch | Device on/off status |
| `powerConsumption` | Power Sensor | Current power usage |
| `errorCode` | Error Code Sensor | Fault detection |

## Integration Benefits

1. **Cloud-based**: No local network requirements
2. **Real-time**: 30-second update intervals via MQTT
3. **Reliable**: Automatic reconnection and command queuing
4. **Comprehensive**: Full device monitoring and control
5. **Standard**: Follows Home Assistant integration patterns

## Dependencies

- `nwp500>=1.0.0` - Core library for Navien communication
- `awsiotsdk>=1.20.0` - MQTT client for AWS IoT Core
- Home Assistant 2023.1+ - Integration framework

## Future Enhancements

Potential areas for expansion:
- Energy usage history and statistics
- Schedule-based operation modes
- Integration with Home Assistant energy dashboard
- Multi-device support for larger installations
- Advanced error diagnosis and notifications