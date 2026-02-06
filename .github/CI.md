# Continuous Integration (CI) Setup

This project uses GitHub Actions for automated testing and validation of all code changes.

## Workflow Overview

The CI pipeline is defined in `.github/workflows/ci.yml` and runs automatically on:
- Push to `main` or `develop` branches
- Pull requests targeting `main` or `develop`
- Manual workflow dispatch

## CI Jobs

### 1. Type Check (mypy)
- **Purpose**: Validate type hints using mypy
- **Requirement**: Must pass with 0 errors
- **Python Version**: 3.12
- **Command**: `tox -e mypy`

### 2. Type Check (pyright)
- **Purpose**: Validate type hints using pyright
- **Requirement**: Must pass with 0 errors (warnings acceptable)
- **Python Version**: 3.12
- **Command**: `tox -e pyright`

### 3. Tests
- **Purpose**: Run unit tests across Python versions
- **Python Versions**: 3.12 and 3.13
- **Command**: `tox -e py312` / `tox -e py313`
- **Note**: Python 3.12 allowed to fail (optional)

### 4. Coverage
- **Purpose**: Ensure test coverage meets requirements
- **Requirement**: ≥80% overall coverage
- **Python Version**: 3.13
- **Command**: `tox -e coverage`
- **Artifacts**:
  - Coverage report uploaded to Codecov
  - HTML coverage report saved as artifact (30-day retention)
  - XML coverage report for external tools

### 5. All Checks Passed
- **Purpose**: Summary job that requires all checks to pass
- **Fails if**: Any of the above jobs fail

## Status Badges

Add these badges to your README to show CI status:

```markdown
[![CI](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml/badge.svg)](https://github.com/eman/ha_nwp500/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/eman/ha_nwp500/branch/main/graph/badge.svg)](https://codecov.io/gh/eman/ha_nwp500)
```

## Running Checks Locally

Before pushing code, run all checks locally:

```bash
# Install tox
uv tool install tox --with tox-uv

# Run all checks (recommended)
tox

# Or run individual checks
tox -e mypy      # Type checking with mypy
tox -e pyright   # Type checking with pyright
tox -e py313     # Unit tests on Python 3.13
tox -e coverage  # Tests with coverage validation
```

## Troubleshooting

### Type Checking Failures

If mypy or pyright fails:
1. Review the error messages in the CI log
2. Fix type hints in the reported files
3. Run `tox -e mypy` and `tox -e pyright` locally to verify
4. Commit and push the fixes

### Coverage Failures

If coverage is below 80%:
1. Review the coverage report artifact
2. Add tests for uncovered code
3. Run `tox -e coverage` locally to verify
4. Ensure new code has corresponding tests

### Test Failures

If unit tests fail:
1. Review the test output in the CI log
2. Run failing tests locally: `tox -e py313 -- tests/path/to/test.py`
3. Fix the code or tests
4. Verify all tests pass before pushing

## Coverage Exclusions

The following are excluded from coverage:
- `coordinator.py` - Complex AWS IoT integration layer requiring extensive mocking

This is configured in `.coveragerc`.

## CI Configuration Files

- `.github/workflows/ci.yml` - Main CI workflow
- `tox.ini` - Tox environment configuration
- `.coveragerc` - Coverage configuration
- `mypy.ini` - Mypy configuration
- `pyrightconfig.json` - Pyright configuration

## Pull Request Checks

All PRs must pass CI checks before merging:
- mypy type checking
- pyright type checking
- Unit tests (Python 3.13)
- Coverage ≥80%

The "All Checks Passed" job provides a single status check that must be green.
