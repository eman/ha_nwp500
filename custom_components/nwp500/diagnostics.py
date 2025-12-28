"""Diagnostics support for Navien NWP500 integration."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import NWP500DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for config entry.

    Provides MQTT diagnostics data from the coordinator's diagnostics
    collector, if available.
    """
    coordinator: NWP500DataUpdateCoordinator | None = hass.data.get(
        DOMAIN, {}
    ).get(config_entry.entry_id)

    if not coordinator:
        return {"error": "Integration not initialized"}

    diagnostics_data: dict[str, Any] = {
        "entry_id": config_entry.entry_id,
        "version": config_entry.version,
    }

    # Add MQTT manager diagnostics if available
    if coordinator.mqtt_manager:
        # Add connection state diagnostics
        diagnostics_data["mqtt_connection_state"] = (
            coordinator.mqtt_manager.get_connection_diagnostics()
        )

        if coordinator.mqtt_manager.diagnostics:
            try:
                mqtt_diags = coordinator.mqtt_manager.diagnostics
                diags_json = mqtt_diags.export_json()
                if isinstance(diags_json, str):
                    diagnostics_data["mqtt_diagnostics"] = json.loads(
                        diags_json
                    )
                else:
                    diagnostics_data["mqtt_diagnostics_error"] = (
                        f"Invalid diagnostics format: {type(diags_json)}"
                    )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to export MQTT diagnostics: %s", err, exc_info=True
                )
                diagnostics_data["mqtt_diagnostics_error"] = str(err)
        else:
            diagnostics_data["mqtt_diagnostics_status"] = (
                "Diagnostics collector not initialized"
            )
    else:
        diagnostics_data["mqtt_manager_status"] = "MQTT manager not available"

    # Add coordinator telemetry
    diagnostics_data["coordinator_telemetry"] = coordinator.get_mqtt_telemetry()

    # Add performance statistics
    diagnostics_data["performance_stats"] = coordinator.get_performance_stats()

    return diagnostics_data
