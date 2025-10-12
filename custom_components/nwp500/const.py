"""Constants for the Navien NWP500 integration."""
from typing import Final

DOMAIN: Final = "nwp500"

# Configuration
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"

# Default values
DEFAULT_NAME: Final = "Navien NWP500"
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds

# Device types and models
DEVICE_TYPE_WATER_HEATER: Final = 52

# Operation modes (from nwp500-python documentation)
OPERATION_MODES: Final = {
    1: "heat_pump",
    2: "energy_saver",  # Hybrid: Efficiency (default mode)
    3: "high_demand",   # Hybrid: Boost  
    4: "electric",
    5: "vacation",
}

# DHW modes from the documentation
DHW_MODES: Final = {
    "dhw_eco": "Eco mode",
    "dhw_normal": "Normal mode", 
    "dhw_high": "High efficiency mode",
}

# Temperature ranges (from documentation)
MIN_TEMPERATURE: Final = 80  # °F
MAX_TEMPERATURE: Final = 140  # °F