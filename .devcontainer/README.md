# Development Container Configuration

This directory contains the configuration for developing the Navien NWP500 Home Assistant integration in a containerized development environment using VS Code Dev Containers.

## Prerequisites

- [Visual Studio Code](https://code.visualstudio.com/)
- [Dev Containers Extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine

## Quick Start

1. Open this project in VS Code
2. When prompted, click "Reopen in Container" (or run `Dev Containers: Reopen in Container` from the command palette)
3. VS Code will build the container and set up the development environment
4. Once ready, you'll have a full development environment with all dependencies installed

## What's Included

### Base Environment
- **Python 3.12**: Matches the project's Python version requirement
- **Git**: For version control
- **Docker-in-Docker**: Enables running docker-compose for Home Assistant testing

### Python Packages
All packages from `requirements.txt` are pre-installed:
- `nwp500-python==7.4.6` - Core library for Navien device communication
- `awsiotsdk>=1.25.0` - AWS IoT SDK for MQTT
- `homeassistant>=2024.1.0` - Home Assistant core
- `mypy`, `pyright` - Type checkers
- `pytest` and related testing tools
- `tox` - Test automation

### VS Code Extensions
The following extensions are automatically installed:
- **Python** - Core Python support
- **Pylance** - Advanced Python language server
- **Mypy Type Checker** - Real-time type checking
- **Ruff** - Fast Python linter
- **YAML** - YAML language support
- **Black Formatter** - Code formatting
- **Even Better TOML** - TOML file support
- **GitLens** - Enhanced Git capabilities
- **GitHub Copilot** - AI-powered code completion

### Development Tools
- **tox** - Run test suites in isolated environments
- **black** - Code formatter
- **ruff** - Linter
- **ipython** - Enhanced Python REPL

## Usage

### Running Tests
```bash
# Run all tests
pytest

# Run with coverage
tox -e coverage

# Run type checking
tox -e mypy
tox -e pyright
```

### Type Checking
```bash
# MyPy (enforced before commits)
mypy --config-file mypy.ini custom_components/nwp500

# Pyright (alternative type checker)
pyright custom_components/nwp500
```

### Running Home Assistant
The devcontainer includes Docker-in-Docker support, so you can run the Home Assistant container:

```bash
# Start Home Assistant with the integration
docker compose up -d

# View logs
docker compose logs -f

# Stop Home Assistant
docker compose down
```

Home Assistant will be available at `http://localhost:8123`

### Port Forwarding
- **Port 8123**: Home Assistant web interface (automatically forwarded)

## Configuration Details

### devcontainer.json
- Defines the container configuration
- Specifies VS Code extensions and settings
- Configures port forwarding
- Sets up post-creation commands

### Dockerfile
- Based on Microsoft's official Python 3.12 devcontainer image
- Installs system dependencies
- Pre-installs all Python requirements
- Sets up development tools

## Customization

### Adding VS Code Extensions
Edit `.devcontainer/devcontainer.json` and add extension IDs to the `extensions` array:

```json
"extensions": [
  "ms-python.python",
  "your.extension-id"
]
```

### Adding Python Packages
Add packages to `requirements.txt` in the project root, then rebuild the container:
- Command Palette → `Dev Containers: Rebuild Container`

### Modifying Settings
Edit the `settings` section in `devcontainer.json` to change VS Code behavior.

## Troubleshooting

### Container Build Fails
1. Ensure Docker is running
2. Check Docker has sufficient resources (4GB RAM minimum)
3. Try: `Dev Containers: Rebuild Container Without Cache`

### Port 8123 Already in Use
Stop any running Home Assistant instances:
```bash
docker compose down
# or
docker stop $(docker ps -q --filter name=homeassistant)
```

### Python Packages Not Found
Rebuild the container:
```bash
# Command Palette → Dev Containers: Rebuild Container
```

### Git Safe Directory Warning
The Dockerfile includes a fix, but if issues persist:
```bash
git config --global --add safe.directory /workspaces/ha_nwp500
```

## Benefits

- **Consistent Environment**: Everyone uses the same development setup  
- **No Local Setup**: No need to install Python, dependencies, or tools locally  
- **Isolated**: Doesn't interfere with other Python projects  
- **Pre-configured**: All extensions and settings ready to go  
- **Fast Onboarding**: New contributors can start coding immediately  

## Alternative: GitHub Codespaces

This devcontainer configuration also works with GitHub Codespaces for cloud-based development:

1. Go to the repository on GitHub
2. Click "Code" → "Codespaces" → "Create codespace on main"
3. Wait for the environment to build
4. Start coding in your browser!

## Learn More

- [VS Code Dev Containers Documentation](https://code.visualstudio.com/docs/devcontainers/containers)
- [Dev Containers Specification](https://containers.dev/)
- [Home Assistant Development](https://developers.home-assistant.io/)
