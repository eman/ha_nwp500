# Continuous Integration (CI) Setup

This project uses GitHub Actions for automated testing and validation of all code changes.

## Workflow Overview

The CI pipeline is defined in `.github/workflows/ci.yml` and runs automatically on:
- Push to `main` or `develop` branches
- Pull requests targeting `main` or `develop`
- Manual workflow dispatch

## CI Jobs

All jobs run on Python 3.14 unless noted — the integration uses Python
3.14-only syntax and will not import on older interpreters.

### 1. Lint (ruff)
- **Purpose**: Linting and formatting checks
- **Command**: `tox -e ruff`

### 2. Hassfest Validation
- **Purpose**: Official Home Assistant manifest/integration validation
- **Runs**: `home-assistant/actions/hassfest` (no local tox env)

### 3. Type Check (mypy)
- **Purpose**: Validate type hints using mypy
- **Requirement**: Must pass with 0 errors
- **Command**: `tox -e mypy`

### 4. Check Deprecated APIs
- **Purpose**: Flag deprecated Home Assistant API usage
- **Python Version**: 3.13 (the script scans source text and does not import it)
- **Command**: `python3 scripts/check_deprecated_apis.py`

### 5. Type Check (basedpyright)
- **Purpose**: Validate type hints using basedpyright
- **Requirement**: Must pass with 0 errors (warnings acceptable)
- **Command**: `tox -e basedpyright`

### 6. Tests
- **Purpose**: Run unit tests
- **Command**: `tox -e py314`

### 7. Test Coverage
- **Purpose**: Ensure test coverage meets requirements
- **Requirement**: ≥80% overall coverage
- **Command**: `tox -e coverage`
- **Artifacts**:
  - Coverage report uploaded to Codecov
  - HTML coverage report saved as artifact (30-day retention)
  - XML coverage report for external tools

### 8. HACS Validation
- **Purpose**: Validate the repository against HACS requirements
- **Defined in**: `.github/workflows/hacs.yaml`

### 9. All Checks Passed
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
tox -e basedpyright  # Type checking with basedpyright
tox -e py314     # Unit tests on Python 3.14
tox -e coverage  # Tests with coverage validation
```

## Troubleshooting

### Type Checking Failures

If mypy or basedpyright fails:
1. Review the error messages in the CI log
2. Fix type hints in the reported files
3. Run `tox -e mypy` and `tox -e basedpyright` locally to verify
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
2. Run failing tests locally: `tox -e py314 -- tests/path/to/test.py`
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
- basedpyright type checking
- Unit tests (Python 3.14)
- Coverage ≥80%

The "All Checks Passed" job provides a single status check that must be green.
