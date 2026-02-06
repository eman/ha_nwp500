# Development container for Navien NWP500 Home Assistant Integration
FROM mcr.microsoft.com/devcontainers/python:3.13

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    git \
    curl \
    ca-certificates \
    build-essential \
    libffi-dev \
    libssl-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /workspaces/ha_nwp500

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies and tools using uv
RUN uv pip install --system -r requirements.txt \
    && uv pip install --system \
    tox \
    tox-uv \
    black \
    ruff \
    ipython

# Create a directory for Home Assistant config (for docker-compose)
RUN mkdir -p /config

# Set up git safe directory (for devcontainer)
RUN git config --global --add safe.directory /workspaces/ha_nwp500

# The actual source code will be mounted at runtime
