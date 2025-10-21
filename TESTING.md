# Testing Guide for Navien NWP500 Integration

This document describes the testing framework and how to run tests for the NWP500 Home Assistant custom component.

## Quick Start

### Run All Tests

```bash
# Run all tests with tox (recommended)
.venv/bin/tox

# Or run tests directly with pytest
.venv/bin/pytest
```

### Run Specific Test Categories

```bash
# Run only unit tests
.venv/bin/pytest tests/unit/

# Run a specific test file
.venv/bin/pytest tests/unit/test_const.py

# Run a specific test function
.venv/bin/pytest tests/unit/test_const.py::test_domain

# Run tests matching a pattern
.venv/bin/pytest -k "temperature"
```

## Test Organization

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures and configuration
├── unit/                    # Unit tests
│   ├── __init__.py
│   ├── test_const.py       # Tests for constants and utilities
│   ├── test_entity.py      # Tests for base entity class
│   ├── test_water_heater.py # Tests for water heater entity
│   └── ...
└── fixtures/                # Test data fixtures
```

## Available Test Environments

### Default Test Environment (`tox` or `tox -e py312`)

Runs all unit tests with Python 3.12.

```bash
.venv/bin/tox
```

### Coverage Testing (`tox -e coverage`)

Runs tests with coverage reporting. Fails if coverage is below 80%.

```bash
.venv/bin/tox -e coverage
```

Reports are generated in:
- Terminal: Immediate feedback with missing line numbers
- HTML: `htmlcov/index.html` - Browse detailed coverage
- XML: `coverage.xml` - For CI/CD integration

### Type Checking

```bash
# Run mypy
.venv/bin/tox -e mypy

# Run pyright
.venv/bin/tox -e pyright
```

### Run Everything

```bash
# Run tests and all type checkers
.venv/bin/tox -e py312,mypy,pyright,coverage
```

## Test Fixtures

Shared test fixtures are defined in `tests/conftest.py`:

### Core Fixtures

- **`mock_config_entry`**: A mock Home Assistant ConfigEntry
- **`mock_device`**: A mock NWP500 device
- **`mock_device_status`**: Mock device status with realistic data
- **`mock_coordinator`**: Mock coordinator with test data
- **`mock_nwp500_auth_client`**: Mock authentication client
- **`mock_nwp500_api_client`**: Mock API client
- **`mock_nwp500_mqtt_client`**: Mock MQTT client

### Using Fixtures

```python
def test_example(mock_device, mock_device_status):
    """Example test using fixtures."""
    assert mock_device.device_info.mac_address == "AA:BB:CC:DD:EE:FF"
    assert mock_device_status.dhwTemperature == 120.0
```

## Writing Tests

### Unit Test Example

```python
"""Tests for my_module.py"""

from __future__ import annotations

import pytest
from custom_components.nwp500.my_module import MyClass


class TestMyClass:
    """Tests for MyClass."""
    
    def test_basic_functionality(self):
        """Test basic functionality."""
        obj = MyClass()
        assert obj.method() == expected_value
    
    @pytest.mark.asyncio
    async def test_async_method(self, mock_coordinator):
        """Test async methods."""
        obj = MyClass(mock_coordinator)
        result = await obj.async_method()
        assert result is not None
```

### Test Markers

Use markers to organize tests:

```python
@pytest.mark.unit
def test_unit():
    """A unit test."""
    pass

@pytest.mark.slow
def test_slow():
    """A slow test."""
    pass

@pytest.mark.asyncio
async def test_async():
    """An async test."""
    pass
```

Run tests by marker:

```bash
# Run only unit tests
pytest -m unit

# Run everything except slow tests
pytest -m "not slow"
```

## Coverage Goals

- **Target**: 80% minimum coverage
- **Goal**: 90%+ coverage
- **Priority**: High coverage for core modules (const, entity, coordinator)

### Check Coverage

```bash
# Run with coverage report
.venv/bin/tox -e coverage

# Generate HTML report
.venv/bin/tox -e coverage-html

# View HTML report (opens in browser)
open htmlcov/index.html
```

### Coverage by Module

View coverage for specific modules:

```bash
pytest --cov=custom_components.nwp500.const \
       --cov-report=term-missing \
       tests/unit/test_const.py
```

## Continuous Integration

Tests are designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    pip install tox
    tox -e py312

- name: Check coverage
  run: tox -e coverage

- name: Type check
  run: |
    tox -e mypy
    tox -e pyright
```

## Debugging Tests

### Run with Verbose Output

```bash
pytest -vv
```

### Show Local Variables on Failure

```bash
pytest -l
```

### Drop into Debugger on Failure

```bash
pytest --pdb
```

### Run Only Failed Tests

```bash
# Run tests and save failures
pytest

# Re-run only failed tests
pytest --lf

# Re-run failed tests first, then all
pytest --ff
```

## Test Configuration

### pytest.ini

Main pytest configuration in `pytest.ini`:
- Test discovery patterns
- Coverage thresholds
- Output formatting
- Async support
- Markers

### .coveragerc

Coverage configuration in `.coveragerc`:
- Source paths
- Exclusions
- Report formatting

## Common Issues

### Import Errors

If you see import errors, ensure all dependencies are installed:

```bash
pip install -r requirements.txt
```

### Async Test Warnings

Use `@pytest.mark.asyncio` for async tests:

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result is not None
```

### Fixture Not Found

Ensure fixtures are defined in `conftest.py` or imported properly.

### Coverage Too Low

Add tests for uncovered code or adjust the threshold in `pytest.ini`.

## Best Practices

1. **One assertion per test** - Makes failures easier to diagnose
2. **Descriptive test names** - Test names should describe what's being tested
3. **Use fixtures** - Share setup code via fixtures
4. **Test edge cases** - Test boundary conditions and error cases
5. **Mock external dependencies** - Don't call real APIs in tests
6. **Fast tests** - Keep tests fast; mark slow tests with `@pytest.mark.slow`
7. **Independent tests** - Tests should not depend on each other

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [pytest-cov](https://pytest-cov.readthedocs.io/)
- [Home Assistant testing](https://developers.home-assistant.io/docs/development_testing/)

## Getting Help

- Check test output for specific error messages
- Review existing tests for examples
- Check `tests/conftest.py` for available fixtures
- See `TESTING.md` for comprehensive documentation
