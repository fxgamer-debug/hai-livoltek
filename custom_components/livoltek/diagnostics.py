"""Diagnostics for the Livoltek integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_USER_TOKEN,
    COORDINATOR_FAST,
    COORDINATOR_MEDIUM,
    COORDINATOR_WEEKLY,
    DOMAIN,
)


_REDACT = {CONF_API_KEY, CONF_USER_TOKEN, CONF_ACCESS_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Livoltek config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    fast_coord = data[COORDINATOR_FAST]
    medium_coord = data[COORDINATOR_MEDIUM]
    weekly_coord = data[COORDINATOR_WEEKLY]

    fast_data = fast_coord.data or {}
    medium_data = medium_coord.data or {}
    weekly_data = weekly_coord.data or {}

    return {
        "config_entry": async_redact_data(dict(entry.data), _REDACT),
        "fast_coordinator_data": async_redact_data(fast_data, set()),
        "medium_coordinator_data": async_redact_data(medium_data, set()),
        "weekly_coordinator_data": weekly_data,
        "coordinator_status": {
            "fast_last_update_success": fast_coord.last_update_success,
            "medium_last_update_success": medium_coord.last_update_success,
            "weekly_last_update_success": weekly_coord.last_update_success,
            "using_fallback": getattr(fast_coord, "using_fallback", False),
            "consecutive_failures_fast": getattr(fast_coord, "_consecutive_failures", 0),
            "consecutive_failures_medium": getattr(medium_coord, "_consecutive_failures", 0),
            "consecutive_failures_weekly": getattr(weekly_coord, "_consecutive_failures", 0),
        },
    }
