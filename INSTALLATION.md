# Installation Guide - Navien NWP500 Integration

## Prerequisites

1. **Navien NWP500 Heat Pump Water Heater** installed and operational
2. **Navilink Smart Control Account** with your device registered
   - Download the Navilink app and set up your account
   - Register your NWP500 device in the app
   - Verify you can control the device via the mobile app
3. **Home Assistant** version 2023.1 or later

## Method 1: HACS Installation (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed in your Home Assistant
2. Open HACS in Home Assistant UI
3. Go to **Integrations** tab
4. Click the **⋮** menu (three dots) in top right corner
5. Select **Custom repositories**
6. Add repository URL: `https://github.com/eman/ha_nwp500`
7. Select category: **Integration**
8. Click **Add**
9. Find "Navien NWP500" in the integrations list
10. Click **Download** and then **Download** again to confirm
11. **Restart Home Assistant**

## Method 2: Manual Installation

1. Download the integration files:
   ```bash
   cd /config
   git clone https://github.com/eman/ha_nwp500.git
   ```

2. Copy the integration to custom_components:
   ```bash
   cp -r ha_nwp500/custom_components/nwp500 /config/custom_components/
   ```

3. Verify the structure:
   ```
   /config/custom_components/nwp500/
   ├── __init__.py
   ├── config_flow.py
   ├── const.py
   ├── coordinator.py
   ├── entity.py
   ├── manifest.json
   ├── number.py
   ├── sensor.py
   ├── switch.py
   ├── translations/
   │   └── en.json
   └── water_heater.py
   ```

4. **Restart Home Assistant**

## Configuration

1. After restart, go to **Settings** → **Devices & Services**
2. Click **Add Integration** (+ button)
3. Search for **"Navien NWP500"**
4. Click on the integration when found
5. Enter your **Navilink Smart Control credentials**:
   - Email: Your account email
   - Password: Your account password
6. Click **Submit**

The integration will:
- Authenticate with the Navilink cloud service
- Discover your NWP500 devices
- Create entities for monitoring and control

## Entities Created

After successful setup, you'll see entities like:
- `water_heater.navien_nwp500_water_heater` - Main control
- `sensor.navien_nwp500_tank_temperature` - Current temperature
- `sensor.navien_nwp500_power_consumption` - Power usage
- `switch.navien_nwp500_power` - Power on/off
- `number.navien_nwp500_target_temperature` - Temperature control

## Troubleshooting

### "Cannot Connect" Error
- Verify your Navilink credentials by logging into the mobile app
- Check Home Assistant has internet connectivity
- Ensure firewall allows outbound HTTPS (port 443)

### "Invalid Auth" Error
- Double-check your email and password
- Try logging out and back into the Navilink mobile app
- Wait a few minutes and try again (rate limiting)

### No Entities Appear
- Check **Settings** → **System** → **Logs** for errors
- Verify your NWP500 is online in the Navilink app
- Restart the integration: **Settings** → **Devices & Services** → **Navien NWP500** → **⋮** → **Reload**

### MQTT Connection Issues
- The integration falls back to REST API if MQTT fails
- Check Home Assistant logs for AWS IoT connection errors
- Ensure ports 443 and 8883 are not blocked

## Uninstallation

1. Go to **Settings** → **Devices & Services**
2. Find **Navien NWP500** integration
3. Click **⋮** menu → **Delete**
4. If using HACS: Go to HACS → Integrations → Find "Navien NWP500" → **Remove**
5. If manual install: Delete `/config/custom_components/nwp500/`
6. **Restart Home Assistant**

## Getting Help

- Check the [troubleshooting section](README.md#troubleshooting) in README
- Review Home Assistant logs for error messages
- Open an issue on [GitHub](https://github.com/eman/ha_nwp500/issues) with:
  - Home Assistant version
  - Integration version
  - Relevant log entries
  - Steps to reproduce the issue