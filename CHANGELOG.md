# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-10-23

### Added
- Initial release of Navien NWP500 Heat Pump Water Heater integration
- Support for water heater platform with operation mode control
- 40+ sensor entities for monitoring temperature, power, status, and diagnostics
- 15+ binary sensor entities for boolean status indicators
- Switch entities for power control
- Number entities for temperature setpoint control
- Real-time updates via MQTT connection
- Cloud authentication and device discovery
- Support for operation modes: Energy Saver, Heat Pump, High Demand, Electric
- Configuration flow for easy UI-based setup
- Device-based integration with proper device registry support
- Integration with nwp500-python library v3.1.2

[Unreleased]: https://github.com/eman/ha_nwp500/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eman/ha_nwp500/releases/tag/v0.1.0
