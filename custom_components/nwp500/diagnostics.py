"""Diagnostics support for Navien NWP500 integration."""

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .coordinator import NWP500ConfigEntry

_LOGGER = logging.getLogger(__name__)

# Fields whose values are always redacted in diagnostic output.
# Location fields are account PII: they identify where the user lives, so
# they are reported as structure only, never as values.
_TO_REDACT = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "address",
    "city",
    "state",
    "latitude",
    "longitude",
    "altitude",
}

_MAC_RE = re.compile(
    r"[0-9a-fA-F]{2}(?:[:\-][0-9a-fA-F]{2}){5}"  # colon/dash-delimited: AA:BB:CC:DD:EE:FF
    r"|[0-9a-fA-F]{12}",  # bare 12-hex: AABBCCDDEEFF
    re.IGNORECASE,
)


def _redact_macs(obj: Any) -> Any:
    """Recursively replace MAC addresses with '**REDACTED**'.

    Handles both bare (AABBCCDDEEFF) and delimited (AA:BB:CC:DD:EE:FF /
    AA-BB-CC-DD-EE-FF) formats, case-insensitively.
    """
    if isinstance(obj, str):
        return _MAC_RE.sub("**REDACTED**", obj)
    if isinstance(obj, dict):
        return {k: _redact_macs(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_macs(v) for v in obj]
    return obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: NWP500ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for config entry.

    Sensitive data (passwords, tokens, MAC addresses) is redacted before
    returning, in accordance with HA diagnostics requirements.
    """
    coordinator = getattr(config_entry, "runtime_data", None)

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

    # Per-device metadata, including the account location. Values that are
    # PII are redacted below; the keys are retained so maintainers can see
    # which fields the cloud actually populated.
    devices: list[dict[str, Any]] = []
    for device in coordinator.devices:
        entry: dict[str, Any] = {
            "device_name": device.device_info.device_name,
            "device_type": device.device_info.device_type,
            "mac_address": device.device_info.mac_address,
            "connected": device.device_info.connected,
        }
        location = getattr(device, "location", None)
        if location:
            # Only include fields the cloud actually populated. Emitting
            # every key unconditionally would redact each one to
            # "**REDACTED**", making an unset field indistinguishable from
            # a populated one and defeating the point of keeping the keys.
            populated = {
                key: getattr(location, key, None)
                for key in (
                    "address",
                    "city",
                    "state",
                    "latitude",
                    "longitude",
                    "altitude",
                )
            }
            entry["location"] = {
                key: value
                for key, value in populated.items()
                if value is not None
            }
        devices.append(entry)
    diagnostics_data["devices"] = devices

    # Add coordinator telemetry
    diagnostics_data["coordinator_telemetry"] = coordinator.get_mqtt_telemetry()

    # Add performance statistics
    diagnostics_data["performance_stats"] = coordinator.get_performance_stats()

    # Redact credentials and MAC addresses before returning
    redacted: dict[str, Any] = _redact_macs(
        async_redact_data(diagnostics_data, _TO_REDACT)
    )
    return redacted
