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

This integration currently uses **nwp500-python v6.1.1**.

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
- MQTT diagnostics collection and export
- Connection tracking and event recording
- See [Diagnostics section](#diagnostics) for detailed information

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
- Ensure nwp500-python==6.1.1 is installed
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

## Diagnostics

The integration provides comprehensive MQTT diagnostics to help troubleshoot connection issues.

### Enabling Diagnostics

Diagnostics are automatically enabled and exported when the integration is set up. There are two ways to access them:

#### 1. Home Assistant Native Diagnostics (Recommended)

Access diagnostics directly in Home Assistant UI:

1. Go to **Settings → System → System Health**
2. Scroll to "Integrations" section
3. Find "Navien NWP500" integration
4. Click the three-dot menu (⋮)
5. Select "Download Diagnostics"

This downloads a JSON file with:
- MQTT connection state and events
- Coordinator performance statistics
- Message request/response tracking
- Connection drop events with error details

#### 2. Manual File Access

Diagnostics are automatically exported to Home Assistant config directory every 5 minutes:

```
.homeassistant/nwp500_diagnostics_<entry_id>.json
```

**Location by OS:**
- **Linux/Docker**: `/config/nwp500_diagnostics_<entry_id>.json`
- **Windows**: `C:\Users\<user>\AppData\Roaming\.homeassistant\nwp500_diagnostics_<entry_id>.json`
- **macOS**: `~/.homeassistant/nwp500_diagnostics_<entry_id>.json`

### Reading Diagnostics Data

The diagnostics JSON contains three main sections:

#### MQTT Diagnostics

Tracks connection events over time:

```json
{
  "mqtt_diagnostics": {
    "connection_drops": [
      {
        "timestamp": "2025-12-09T03:15:22.123Z",
        "error": "Connection timeout",
        "event_type": "interrupted"
      }
    ],
    "connection_success_events": [
      {
        "timestamp": "2025-12-09T03:15:25.456Z",
        "event_type": "resumed",
        "session_present": true,
        "return_code": 0
      }
    ],
    "summary": {
      "total_drops": 2,
      "total_recoveries": 2,
      "first_event": "2025-12-09T03:10:00.000Z",
      "last_event": "2025-12-09T03:15:25.456Z"
    }
  }
}
```

#### Coordinator Telemetry

Real-time MQTT communication metrics:

```json
{
  "coordinator_telemetry": {
    "last_request_id": "AA:BB:CC:DD:EE:FF_1733703322123",
    "last_request_time": 1733703322.123,
    "last_response_id": "AA:BB:CC:DD:EE:FF_1733703325456",
    "last_response_time": 1733703325.456,
    "total_requests_sent": 42,
    "total_responses_received": 41,
    "mqtt_connected": true,
    "mqtt_connected_since": 1733703300.000,
    "consecutive_timeouts": 0
  }
}
```

**Key fields:**
- `last_request_id`: Unique ID of most recent status request
- `total_requests_sent`: Count of all status requests
- `total_responses_received`: Count of successful responses
- `consecutive_timeouts`: Number of timeouts in a row (resets to 0 on success)
- `mqtt_connected_since`: Unix timestamp when MQTT reconnected

#### Performance Statistics

Integration update cycle performance:

```json
{
  "performance_stats": {
    "update_count": 125,
    "average_time": 0.342,
    "slowest_time": 1.234,
    "total_time": 42.75
  }
}
```

**Interpretation:**
- All times in seconds
- `average_time > 1.0`: Slow updates, may indicate network issues
- `slowest_time > 2.0`: Check device connectivity to Navien cloud
- `update_count`: Number of coordinator cycles completed

### Troubleshooting with Diagnostics

**Problem: Frequent Connection Drops**

1. Check `mqtt_diagnostics.connection_drops` for error patterns
2. Look for regular intervals (indicates NAT timeout or keep-alive issue)
3. Check `total_drops` vs `total_recoveries` ratio
4. Compare `consecutive_timeouts` - if > 0, MQTT may be stuck

**Example:** If drops occur exactly every 5 minutes, likely a network/NAT timeout. Try:
```yaml
# In Home Assistant configuration (if exposed by library)
# Usually requires library configuration in coordinator
```

**Problem: No Status Updates**

1. Verify `mqtt_connected: true`
2. Check `total_responses_received` - should match `total_requests_sent`
3. If `consecutive_timeouts > 3`, integration will attempt forced reconnection
4. Look for error messages in `mqtt_diagnostics.connection_drops`

**Problem: Slow Updates**

1. Check `performance_stats.average_time` and `slowest_time`
2. If average > 1.0 second, network is slow
3. Check `mqtt_connected_since` - recent reconnect may indicate instability
4. Review coordinator logs: `logger.logs.custom_components.nwp500: debug`

### Exporting Diagnostics for Support

To share diagnostics with integration developers:

1. Download diagnostics from Home Assistant UI (Settings → System → System Health → Download Diagnostics)
2. Or copy the JSON file from `.homeassistant/nwp500_diagnostics_<entry_id>.json`
3. Include when creating GitHub issues
4. **Optional:** Edit to remove timestamps if desired for privacy

### Understanding Connection Drop Recovery

Normal MQTT operation includes brief disconnections:

```
✓ Connection established
  ↓
  ✗ Connection interrupted (brief drop, network blip)
  ↓
  ✓ Connection resumed (auto-recovery)
  ↓
  ✓ Status updates resume
```

**This is normal** if:
- `total_drops ≈ total_recoveries` (balanced recovery)
- Drops are brief (< 30 seconds)
- No consecutive_timeouts (recovered before next request)

**This needs attention** if:
- `total_drops >> total_recoveries` (not recovering)
- Long periods without updates (check logs)
- `consecutive_timeouts` increasing (3+ triggers forced reconnect)



**Quick start for contributors:**
1. Clone the repo
2. Follow setup in DEVELOPMENT.md
3. Run `tox` to validate changes
4. Submit a pull request

All pull requests are automatically validated by CI and must pass all checks before merge.

## License

This integration is released under the MIT License.