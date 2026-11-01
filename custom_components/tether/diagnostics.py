"""Diagnostics support for Tether."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CALLBACK_URL,
    CONF_DEVELOPER_TOKEN,
    CONF_LOCAL_HOST,
    CONF_USER_ID,
    CONF_USER_NAME,
    CONF_UTOKEN,
)
from .coordinator import TetherConfigEntry

TO_REDACT = {
    # Credentials
    CONF_DEVELOPER_TOKEN,
    CONF_UTOKEN,
    "token",
    "authToken",
    # Account identity
    CONF_USER_ID,
    CONF_USER_NAME,
    "uid",
    "uname",
    # Network location
    CONF_CALLBACK_URL,
    CONF_LOCAL_HOST,
    "domain",
    "ip",
    "local_ip",
    # Toy hardware identity
    "id",
    "mac",
    "nickName",
    "deviceId",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TetherConfigEntry
) -> dict[str, Any]:
    """Return redacted connection and coordinator state for troubleshooting."""
    coordinator = entry.runtime_data
    toys = (coordinator.data or {}).get("toys", {})
    # Toy IDs are hardware identifiers, so the mapping keys are replaced too.
    anonymized_toys = {
        f"toy_{index}": toy for index, toy in enumerate(toys.values(), start=1)
    }
    return async_redact_data(
        {
            "entry": {
                "version": entry.version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "connection": dict(coordinator.device_info),
            "coordinator": {
                "status": (coordinator.data or {}).get("status"),
                "last_update_success": coordinator.last_update_success,
                "toys": anonymized_toys,
            },
        },
        TO_REDACT,
    )
