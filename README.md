# Navien NWP500 Heat Pump Water Heater Integration for Home Assistant

This custom integration provides support for Navien NWP500 heat pump water heaters in Home Assistant through the Navilink Smart Control cloud service.

## Features

- **Water Heater Control**: Full water heater entity with temperature control and operation modes
- **Real-time Monitoring**: Live status updates every 30 seconds via MQTT
- **Comprehensive Sensors**: Temperature readings, power consumption, energy usage, and more
- **Device Controls**: Power on/off, temperature adjustment, and freeze protection status
- **Error Monitoring**: Error codes and operational status

## Prerequisites

- Navien NWP500 heat pump water heater
- Navilink Smart Control account with your device registered
- Internet connection for cloud communication

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations" tab
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/eman/ha_nwp500`
6. Select "Integration" as the category
7. Click "Add"
8. Find "Navien NWP500" in the integrations list and install

### Manual Installation

1. Copy the `custom_components/nwp500` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Go to Settings > Devices & Services
4. Click "Add Integration"
5. Search for "Navien NWP500"

## Configuration

1. After installation, go to Settings > Devices & Services
2. Click "Add Integration" and search for "Navien NWP500"
3. Enter your Navilink Smart Control email and password
4. The integration will discover your NWP500 devices automatically

## Entities Created

### Water Heater
- Main water heater entity with temperature control
- Operation modes: Heat Pump, Energy Saver (Hybrid), High Demand (Hybrid), Electric, Vacation
- Target temperature control (80°F - 140°F)

### Sensors
- Tank Temperature
- Outside Temperature  
- Heat Pump Inlet/Outlet Temperatures
- DHW Target Temperature
- Power Consumption
- Energy Usage Today
- Compressor Frequency
- Coefficient of Performance (COP)
- Error Code
- Operation Mode

### Switches
- Power Switch (on/off control)
- Freeze Protection Status (read-only)

### Numbers
- Target Temperature (alternative to water heater control)

## Data Updates

The integration uses both REST API and MQTT for optimal performance:

- **Initial Setup**: REST API for device discovery and authentication
- **Real-time Updates**: MQTT requests every 30 seconds for current status
- **Commands**: MQTT for immediate device control
- **Fallback**: REST API polling if MQTT unavailable

## Authentication

The integration uses JWT-based authentication with the Navilink Smart Control service:
- Access tokens automatically refresh every hour
- Secure credential storage in Home Assistant
- Automatic reconnection on network issues

## Troubleshooting

### Connection Issues
- Verify your Navilink Smart Control credentials
- Check that your NWP500 is online and registered to your account
- Ensure Home Assistant has internet connectivity

### Missing Entities
- Some sensors may not appear if the device doesn't support them
- Check the Home Assistant logs for any error messages
- Restart the integration from Settings > Devices & Services

### MQTT Issues
- The integration will fall back to REST API if MQTT fails
- Check network connectivity and firewall settings  
- MQTT uses AWS IoT Core WebSocket connections on port 443

## Known Limitations

### AWS IoT SDK Blocking I/O Warnings
The integration may generate warnings about "blocking call" operations during MQTT connection. These warnings are caused by the underlying AWS IoT SDK library and are unavoidable while maintaining compatibility with the Navien cloud service. The warnings do not affect functionality and can be safely ignored.

### Operation Mode 32
Some NWP500 devices report operation mode 32, which is not recognized by the nwp500-python library and defaults to "STANDBY". This does not affect other functionality.

### Control Command Verification  
While the integration sends control commands (temperature changes, power on/off) via MQTT, the actual device response may take several minutes to reflect in the status due to the nature of heat pump water heater operation.

## Support

For issues and feature requests, please use the [GitHub repository](https://github.com/eman/ha_nwp500/issues).

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Built using the [nwp500-python](https://github.com/eman/nwp500-python) library
- Thanks to the Home Assistant community for integration patterns and best practices