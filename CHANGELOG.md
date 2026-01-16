# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.6] - 2026-01-16

### Fixed
- **Authentication Error Handling**: Fixed false reauth prompts when network errors occur during authentication
  - Network errors during startup no longer trigger unnecessary reauthentication flows
  - Only genuine credential failures now prompt users to re-authenticate
  - Leverages `retriable` flag from nwp500-python v7.2.3 for intelligent error differentiation
- **Type Checking Configuration**: Fixed JSON syntax errors and editor configuration issues
  - Removed trailing commas from `pyrightconfig.json` for valid JSON
  - Added `stubPath` configuration pointing to valid directory
  - Created `.zed/settings.json` for proper Zed editor integration
  - Resolves editor complaints about missing typings directory

### Changed
- **Code Quality**: Simplified error handling logic by removing unnecessary hasattr checks
  - Direct access to `retriable` attribute makes code intention clearer
  - Improved maintainability with explicit dependency on nwp500-python v7.2.3+
- **Type Checking**: Migrated from Pyright to Basedpyright for faster, more robust type checking
  - Updated `.github/workflows/ci.yml` to use basedpyright instead of pyright
  - Faster type checking in CI pipeline without sacrificing accuracy
  - Better alignment with modern Python tooling ecosystem

## [0.1.5] - 2025-12-28

### Added
- **Full HACS Validation**: Enabled strict `hacsjson` and `integration_manifest` checks in CI pipeline
- **Enhanced Metadata**: Added `loggers` to `manifest.json` and improved `hacs.json` for better store visibility

### Changed
- **Updated to Python 3.13+**: Minimum Python version is now 3.13 (supports 3.13 and 3.14)
- **Updated to Home Assistant 2025.1.0+**: Aligned with latest Home Assistant requirements
- **Repository Visibility**: Switched to Public repository to support HACS validation and publication
- **Cleaned up Metadata**: Standardized `manifest.json` key order and removed unsupported fields to pass Hassfest validation

### Removed
- **Legacy Diagnostics**: Removed periodic background file writing to config directory
  - Background I/O removed to reduce disk wear and follow modern integration standards
  - Diagnostic data remains fully accessible via Home Assistant's native "Download Diagnostics" feature

## [0.1.4] - 2025-12-27

## [0.1.3] - 2025-12-27

### Added
- **Tank Volume Sensor**: New sensor displays tank capacity in gallons
  - Shows 50, 65, or 80 gallons based on device model
  - Enabled by default for easy visibility
  - No entity category (static device characteristic, not config/diagnostic)
  - Uses VolumeCode enum from nwp500-python v7.2.0
- **New Recirculation Sensors**: Added missing recirculation system sensors from nwp500-python v7.2.0
  - Recirculation Model Type Code - identifies installed recirculation hardware
  - Recirculation Software Version - recirculation controller firmware version
  - Recirculation Minimum Temperature - lower temperature limit for recirculation loop
  - Recirculation Maximum Temperature - upper temperature limit for recirculation loop
  - All recirculation sensors are disabled by default

### Changed
- **Updated to nwp500-python v7.2.0**: Adopted latest library version
  - No breaking changes affecting this integration (class renames were internal to library)
  - Enhanced VolumeCode enum provides better tank capacity identification
  - New temperature conversion classes improve type safety (internal to library)
  - See [nwp500-python v7.2.0 release notes](https://github.com/eman/nwp500-python/releases/tag/v7.2.0)

## Previous Releases

### Added (from v7.1.0)
- **New v7.1.0 Control Services**: Exposed new device control commands from nwp500-python v7.1.0
  - `nwp500.enable_demand_response` / `nwp500.disable_demand_response` - Utility demand response participation
  - `nwp500.reset_air_filter` - Reset air filter maintenance timer
  - `nwp500.set_vacation_days` - Configure vacation mode duration (1-365 days)
  - `nwp500.set_recirculation_mode` - Control recirculation pump mode (1-4)
  - `nwp500.trigger_recirculation` - Manual recirculation pump hot button trigger
  - All services support device selector for easy automation

### Changed
- **BREAKING: nwp500-python v7.1.0 API changes**: Updated MQTT control method calls to use `.control` property
  - All device control methods now accessed via `mqtt_client.control.method_name()`
  - Updated `request_device_status()`, `request_device_info()`, and all control commands
  - Periodic request methods consolidated to `start_periodic_requests()` with `PeriodicRequestType` enum
  - Required to support new capability checking system in library v7.1.0
- **Python 3.13+ match/case statements**: Refactored command dispatcher to use modern pattern matching
  - Replaced long if/elif chains with match/case for cleaner code
  - Leverages Python 3.13 structural pattern matching (PEP 634)
- **Python 3.13-3.14 optimizations**: Updated to leverage latest Python performance improvements
  - Dictionary operations benefit from 10-15% faster lookups and comprehensions
  - Improved function call performance reduces coordinator overhead
  - Native type hints (`X | Y`) instead of `Union[X, Y]`
  - `datetime.UTC` instead of `timezone.utc`
- **Updated Python target**: Ruff target-version set to `py313`
- **Dropped Python 3.12**: Removed py312 from test matrix, focusing on Python 3.13+
- **Added Python 3.14**: Added py314 to test matrix for forward compatibility

## [0.1.2] - 2025-12-18

### Added
- **MQTT Diagnostics**: New diagnostics support for troubleshooting connection issues
  - `MqttDiagnosticsCollector` integration for connection drop tracking
  - Home Assistant native diagnostics protocol support via `diagnostics.py`
  - Periodic diagnostic exports to Home Assistant config directory
  - Connection state tracking and event recording
- **Reservation Scheduling**: New services for managing programmed temperature/mode schedules
  - `nwp500.set_reservation`: Create a single reservation entry with user-friendly parameters
  - `nwp500.update_reservations`: Replace all reservations with a new set (advanced)
  - `nwp500.clear_reservations`: Remove all reservation schedules
  - `nwp500.request_reservations`: Request current reservation data from device
- Reservations allow automatic mode and temperature changes at scheduled times
- Supports up to 7 reservation entries per device
- Comprehensive test coverage for diagnostics module (10 new tests)

### Changed
- **BREAKING**: Minimum Home Assistant version now 2025.1.0 (Python 3.12+ required)
- **BREAKING**: Dropped support for Python 3.10 and 3.11
- Updated nwp500-python dependency to 6.1.1
- Updated awsiotsdk minimum version to 1.27.0
- Modernized codebase with Python 3.12 features (match/case statements)
- CI now runs on Python 3.12 and 3.13
- Test coverage increased to 82.71%

## Library Dependency: nwp500-python

This section tracks changes in the nwp500-python library that this integration depends on.

### v7.2.3 (2026-01-16)

#### Fixed
- **Network Errors Triggering False Reauth**: Fixed issue where network errors during authentication startup were incorrectly triggering reauth prompts
  - Root cause: Network errors and invalid credentials were both raised as `AuthenticationError`, making them indistinguishable
  - Solution: Network errors in `sign_in()` and `refresh_token()` now set `retriable=True` flag
  - Impact: Integration can now distinguish transient network failures from actual credential failures
  - Home Assistant will retry automatically without prompting for reauthentication when network is unavailable

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.2.3

### v7.2.2 (2025-12-26)

#### Fixed
- **TOU Status Always Showing False**: Fixed `touStatus` field always reporting `False` regardless of actual device state
  - Root cause: Version 7.2.1 incorrectly changed `touStatus` to use device-specific 1/2 encoding, but the device uses standard 0/1 encoding
  - Solution: Use Python's built-in `bool()` for `touStatus` field (handles 0=False, 1=True naturally)
  - Device encoding: 0=OFF/disabled, 1=ON/enabled (standard Python truthiness)

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.2.2

### v7.2.0 (2025-12-23)

#### Breaking Changes
- **Class Renames**: `DeviceCapabilityChecker` → `MqttDeviceCapabilityChecker`, `DeviceInfoCache` → `MqttDeviceInfoCache`
  - These classes are MQTT-specific implementations
  - **No impact on this integration** - we don't use these classes directly

#### Added
- **VolumeCode Enum**: Tank capacity identification with human-readable text
  - `VOLUME_50GAL = 65`, `VOLUME_65GAL = 66`, `VOLUME_80GAL = 67`
  - `VOLUME_CODE_TEXT` dict provides display text (e.g., "50 gallons")
  - Used in `DeviceFeature.volume_code` field
- **Temperature Conversion Classes**: Type-safe temperature handling (`HalfCelsius`, `DeciCelsius`)
- **Protocol Converters Module**: Centralized device protocol conversion logic
- **Recirculation Fields**: Additional recirculation system sensors
  - `recirc_model_type_code`: Identifies installed recirculation hardware
  - `recirc_sw_version`: Recirculation controller firmware version
  - `recirc_temperature_min` / `recirc_temperature_max`: Temperature limits
- **Factory Function**: New `create_navien_clients()` for streamlined initialization

#### Changed
- **MQTT Module Reorganization**: Consolidated into cohesive `mqtt` package
- **CLI Framework**: Migrated from argparse to Click framework
- **Examples Reorganization**: Structured into beginner/intermediate/advanced categories

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.2.0

### v7.1.0 (2025-12-22)

#### Added
- **Device Capability System**: New device capability detection and validation framework
  - `DeviceCapabilityChecker`: Validates device feature support based on device models
  - `DeviceInfoCache`: Efficient caching of device information with configurable update intervals
  - `@requires_capability` decorator: Automatic capability validation for MQTT commands
  - `DeviceCapabilityError`: New exception for unsupported device features
- **Advanced Control Commands**: New MQTT commands for advanced device features
  - Demand response participation control
  - Air filter maintenance tracking reset
  - Vacation mode duration configuration
  - Water program reservation management
  - Recirculation pump control and scheduling
- **CLI Documentation Updates**: Comprehensive documentation updates for subcommand-based CLI
- **Model Field Factory Pattern**: New field factory to reduce boilerplate in model definitions

#### Changed
- **CLI Output**: Numeric values in status output now rounded to one decimal place for better readability
- `MqttDeviceController` now integrates device capability checking with auto-caching of device info
- **MQTT Control Refactoring**: Centralized device control via `.control` namespace
- **Logging Security**: Enhanced sensitive data redaction (MAC addresses consistently redacted)

#### Fixed
- Type annotation consistency: Optional parameters now properly annotated
- Multiple type annotation issues for CI compatibility
- Mixing valve field: Corrected alias field name
- Vacation days validation: Enforced maximum value validation
- CI linting: Fixed line length violations and import sorting issues
- Parser regressions: Fixed data parsing issues introduced in MQTT refactoring

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.1.0

### v7.0.1 (2025-12-18)

#### Fixed
- Minor bug fixes and improvements
- Fixed DREvent enum integration for DR Event Status sensor

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.0.1

### v7.0.0 (2025-12-18)

#### Key Changes
- **Python 3.13 minimum**: Library now requires Python 3.13+
- **Comprehensive enumerations module**: New type-safe enums for device control and status
  - `DhwOperationSetting`, `CurrentOperationMode`, `TemperatureType`, `CommandCode`, `ErrorCode`, etc.
  - Enums automatically serialize to human-readable names
- **Python 3.13 features**: PEP 695 type syntax, native `datetime.UTC`, native union syntax

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v7.0.0

### v6.1.1 (2025-12-08)

#### Added
- `MqttDiagnosticsCollector` class for detailed MQTT diagnostics
  - Track connection drops with error information
  - Record connection recovery events
  - Export diagnostic data as JSON for analysis
  - Helps diagnose and troubleshoot MQTT connection issues

#### Features
- Captures connection interruption events with error details
- Records connection success events with return codes and session state
- Provides JSON export functionality for offline analysis
- Designed for Home Assistant integration diagnostics

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.1.1

### v6.1.0 (2025-12-03)

**BREAKING CHANGES**: Temperature API simplified with Fahrenheit input

#### Changed
- `build_reservation_entry()` now accepts `temperature_f` (Fahrenheit) instead of raw `param` value
- `set_dhw_temperature()` now accepts Fahrenheit directly instead of raw integer
- Temperature conversion to half-degrees Celsius handled automatically by the library

#### Removed
- `set_dhw_temperature_display()` removed (was using incorrect conversion formula)

#### Added
- `fahrenheit_to_half_celsius()` utility function for advanced use cases

#### Fixed
- Temperature encoding bug in `set_dhw_temperature()` - was using incorrect "subtract 20" formula

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.1.0

### v6.0.8 (2025-12-02)

#### Changed
- Maintenance release, version bump for PyPI

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.8

### v6.0.7 (2025-11-30)

#### Features
- Added TOU (Time-of-Use) override support:
  - New binary sensor entity for TOU override status
  - New switch entity to control TOU override

#### Changed
- Updated nwp500-python dependency to 6.0.7

#### Fixed
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.7

### v6.0.6 (2025-11-24)

#### Bug Fixes
- Updated nwp500-python dependency to 6.0.6
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.6

### v6.0.5 (2025-11-21)

#### Bug Fixes
- Updated nwp500-python dependency to 6.0.5
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.5

### v6.0.4 (2025-11-21)

#### Bug Fixes
- Updated nwp500-python dependency to 6.0.4
- Minor bug fixes and improvements

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.4

### v6.0.3 (2025-11-20)

**BREAKING CHANGES**: Migration from custom dataclass-based models to Pydantic BaseModel implementations.

#### Removed
- Removed legacy dataclass implementations for models. All models now inherit from `NavienBaseModel` (Pydantic).
- Removed manual `from_dict` constructors.
- Removed field metadata conversion system.

#### Changed
- Models now use snake_case attribute names consistently; camelCase keys from API/MQTT are mapped automatically via Pydantic.

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.3

### v6.0.2 (2025-11-15)

#### Bug Fixes
- Fixed issue with MQTT connection stability

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.2

### v6.0.1 (2025-11-08)

#### Bug Fixes
- Fixed `DatetimeFormatError` when parsing device timestamps with fractional seconds
- Improved datetime parsing robustness

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.1

### v6.0.0 (2025-11-02)

**BREAKING CHANGES** - However, this integration was already compatible and required no code changes.

#### What Changed in the Library
- **Constructor Callbacks Removed**: `on_connection_interrupted` and `on_connection_resumed` constructor parameters removed from `NavienMqttClient`
  - Migration: Use event emitter pattern instead: `mqtt_client.on("connection_interrupted", handler)`
- **Exception Import Changes**: Backward compatibility re-exports removed from `api_client` and `auth` modules
  - Migration: Import exceptions from `nwp500.exceptions` or package root

#### Migration Status for this Integration
- Already using event emitter pattern (not constructor callbacks)
- All exception imports use correct module (`nwp500.exceptions`)
- No code changes required for v6.0.0 compatibility
- Full compatibility with new architecture

#### Benefits
- Multiple event listeners per event (not limited to one callback)
- Consistent API across all events
- Dynamic listener management (add/remove at runtime)
- Async handler support
- Cleaner architecture and imports

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v6.0.0

### v5.0.2 (2025)

#### Improvements
- **Bug Fix**: Fixed `InvalidStateError` when cancelling MQTT futures during disconnect
  - Prevents race condition when MQTT connection is being torn down
  - Improves stability during reconnection scenarios
  - Better handling of connection state transitions

**Full release notes**: https://github.com/eman/nwp500-python/releases/tag/v5.0.2

### v5.0.0 (2025)

**BREAKING CHANGES** - This integration was updated to handle all changes.

#### What Changed in the Library
- **Exception Handling**: Library now uses specific exception types (`MqttNotConnectedError`, `MqttConnectionError`, `RangeValidationError`, etc.) instead of generic `RuntimeError` and `ValueError`
- **Python Version**: Minimum Python version is now 3.9 (was 3.8)
- **Type Hints**: Migrated to native type hints (PEP 585): `dict[str, Any]` instead of `Dict[str, Any]`

#### Migration Status for this Integration
- All exception handling updated to use new specific exception types
- Integration already uses Python 3.9+ minimum (via Home Assistant requirements)
- Type hints already use PEP 585 native syntax
- All imports and error handling patterns updated
- Full compatibility with new exception architecture

#### Improvements
- **Enterprise Exception Architecture**: Complete exception hierarchy for better error handling
  - Added `Nwp500Error` as base exception for all library errors
  - MQTT-specific exceptions: `MqttError`, `MqttConnectionError`, `MqttNotConnectedError`, `MqttPublishError`, `MqttSubscriptionError`, `MqttCredentialsError`
  - Validation exceptions: `ValidationError`, `ParameterValidationError`, `RangeValidationError`
  - Device exceptions: `DeviceError`, `DeviceNotFoundError`, `DeviceOfflineError`, `DeviceOperationError`
  - All exceptions include `error_code`, `details`, and `retriable` attributes

- **Exception Handling Improvements**:
  - All exception wrapping now uses exception chaining (`raise ... from e`) to preserve stack traces
  - Replaced 19+ instances of generic exceptions with specific types
  - Better error messages and user guidance
  - Structured logging support with `to_dict()` method on all exceptions

- **Critical Bug Fixes**:
  - Fixed thread-safe reconnection task creation from MQTT callbacks (prevents `RuntimeError: no running event loop`)
  - Fixed thread-safe event emission from MQTT callbacks
  - Fixed device control command codes (power-off/on now use correct command codes)
  - Fixed MQTT topic pattern matching with wildcards
  - Fixed missing `OperationMode.STANDBY` enum value
  - Robust enum conversion with fallbacks for unknown values

- **Code Quality**:
  - Modern Python type hints (PEP 585)
  - Better debugging capabilities
  - Cleaner, more maintainable codebase
  - Comprehensive test suite for exceptions

### v4.8.0 (2025)

#### Improvements
- **Token Persistence**: Added `stored_tokens` parameter to `NavienAuthClient.__init__()` for restoring previously saved tokens
- **Session Continuity**: Reduces API load and improves startup time by reusing valid authentication tokens across application restarts
- **Smart Authentication**: Automatically skips authentication when valid stored tokens are provided
- **Auto-Refresh**: Automatically refreshes expired JWT tokens or re-authenticates if AWS credentials expired
- **Rate Limit Prevention**: Avoids hitting API rate limits from frequent restarts

### v4.7.1 (2025)

#### Improvements
- **Bug Fixes**: Minor improvements and bug fixes from v4.7

### v4.7 (2025)

#### Improvements
- **Two-Tier MQTT Reconnection Strategy**: 
  - Quick reconnection (attempts 1-9) for fast recovery from transient network issues
  - Deep reconnection (every 10th attempt) with full credential refresh and subscription recovery
  - Unlimited retries - never gives up permanently
- **Enhanced Error Handling**: Replaced 25 catch-all exception handlers with specific exception types
- **New Public API**:
  - `NavienAuthClient.has_stored_credentials` property
  - `NavienAuthClient.re_authenticate()` method
  - `MqttSubscriptionManager.resubscribe_all()` method
- **Production-Ready MQTT Reconnection**: Never loses connection permanently, handles expired AWS credentials gracefully
- **Code Quality**: Improved error messages, better debugging capabilities, cleaner maintainable codebase

### v3.1.4 (2025)

#### Improvements
- **MQTT Reconnection**: Fixed MQTT reconnection failures due to expired AWS credentials
- **Connection Recovery**: Improved automatic recovery from connection interruptions

### v3.1.3 (2025)

#### Improvements
- **MQTT Reliability**: Improved MQTT reconnection reliability with active reconnection
- **Connection Stability**: Better handling of connection interruptions and recovery

### v3.1.2 (2025)

#### Improvements
- **Authentication**: Fixes 401 authentication errors with automatic token refresh
- **Reliability**: Improved session management and token handling

### v3.1.1 (2025)

#### Improvements
- **Documentation**: PEP 257 compliant docstrings for better IDE support
- **Code Quality**: 80 character line limit for improved readability
- **Comprehensive Documentation**: Enhanced API documentation

### v3.0.0 (2025)

**BREAKING CHANGES**

#### What Changed
- **Removed**: Deprecated `OperationMode` enum (fully replaced by `DhwOperationSetting` and `CurrentOperationMode`)
- **Removed**: Migration helper functions from v2.x
- **Clean API**: Streamlined enum structure for better type safety

#### Enhanced Type Safety
- **DhwOperationSetting**: User-configured mode preferences (Heat Pump, Electric, Energy Saver, High Demand, Vacation, Power Off)
- **CurrentOperationMode**: Real-time operational states (Standby, Heat Pump Mode, Hybrid Efficiency Mode, Hybrid Boost Mode)
- **Better IDE Support**: More specific enum types prevent accidental misuse

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

[Unreleased]: https://github.com/eman/ha_nwp500/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/eman/ha_nwp500/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/eman/ha_nwp500/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/eman/ha_nwp500/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/eman/ha_nwp500/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/eman/ha_nwp500/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/eman/ha_nwp500/releases/tag/v0.1.0
