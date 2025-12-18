"""Diagnostics support for Navien NWP500 integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles  # type: ignore[import-untyped]
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
                diagnostics_data["mqtt_diagnostics"] = json.loads(diags_json)
            except Exception as err:
                _LOGGER.warning(
                    "Failed to export MQTT diagnostics: %s", err,
                    exc_info=True
                )
                diagnostics_data["mqtt_diagnostics_error"] = str(err)
        else:
            diagnostics_data["mqtt_diagnostics_status"] = (
                "Diagnostics collector not initialized"
            )
    else:
        diagnostics_data["mqtt_manager_status"] = "MQTT manager not available"

    # Add coordinator telemetry
    diagnostics_data["coordinator_telemetry"] = (
        coordinator.get_mqtt_telemetry()
    )

    # Add performance statistics
    diagnostics_data["performance_stats"] = (
        coordinator.get_performance_stats()
    )

    return diagnostics_data


async def async_setup_diagnostics_export(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Set up periodic diagnostic exports to Home Assistant config directory.

    Exports MQTT diagnostics to a JSON file every 5 minutes for offline
    analysis and troubleshooting.
    """
    import asyncio

    coordinator: NWP500DataUpdateCoordinator | None = hass.data.get(
        DOMAIN, {}
    ).get(config_entry.entry_id)

    if not coordinator or not coordinator.mqtt_manager:
        return

    diagnostics_path = (
        Path(hass.config.path())
        / f"nwp500_diagnostics_{config_entry.entry_id}.json"
    )

    async def export_once() -> None:
        """Export diagnostics once."""
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
                    diagnostics_data["mqtt_diagnostics"] = json.loads(
                        diags_json
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "Failed to export MQTT diagnostics: %s", err,
                        exc_info=True
                    )
                    diagnostics_data["mqtt_diagnostics_error"] = str(err)
            else:
                diagnostics_data["mqtt_diagnostics_status"] = (
                    "Diagnostics collector not initialized"
                )
        else:
            diagnostics_data["mqtt_manager_status"] = (
                "MQTT manager not available"
            )

        # Add coordinator telemetry
        diagnostics_data["coordinator_telemetry"] = (
            coordinator.get_mqtt_telemetry()
        )

        # Add performance statistics
        diagnostics_data["performance_stats"] = (
            coordinator.get_performance_stats()
        )

        json_data = json.dumps(diagnostics_data, indent=2)
        async with aiofiles.open(
            diagnostics_path, "w", encoding="utf-8"
        ) as f:
            await f.write(json_data)

        _LOGGER.debug(
            "Exported diagnostics to %s",
            diagnostics_path,
        )

    async def export_task() -> None:
        """Export diagnostics every 5 minutes."""
        while True:
            try:
                await asyncio.sleep(300)
                await export_once()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error(
                    "Error exporting diagnostics: %s", err, exc_info=True
                )

    # Export immediately on startup
    try:
        await export_once()
    except Exception as err:
        _LOGGER.error(
            "Error exporting diagnostics on startup: %s", err, exc_info=True
        )

    task = asyncio.create_task(export_task())

    async def cancel_task() -> None:
        """Cancel the export task on unload."""
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    config_entry.async_on_unload(cancel_task)
