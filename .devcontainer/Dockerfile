# Development container for Navien NWP500 Home Assistant Integration
FROM mcr.microsoft.com/devcontainers/python:3.12

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

# Upgrade pip and install build tools
RUN pip install --upgrade pip setuptools wheel

# Set working directory
WORKDIR /workspaces/ha_nwp500

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install -r requirements.txt

# Install additional development tools
RUN pip install \
    tox \
    black \
    ruff \
    ipython

# Create a directory for Home Assistant config (for docker-compose)
RUN mkdir -p /config

# Set up git safe directory (for devcontainer)
RUN git config --global --add safe.directory /workspaces/ha_nwp500

# The actual source code will be mounted at runtime
