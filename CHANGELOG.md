# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Updated nwp500-python dependency to 6.0.5

## Library Dependency: nwp500-python

This section tracks changes in the nwp500-python library that this integration depends on.

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

[Unreleased]: https://github.com/eman/ha_nwp500/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eman/ha_nwp500/releases/tag/v0.1.0
