"""Diagnostics for the Livoltek integration."""
from __future__ import annotations

from datetime import datetime, timezone
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
_LEVEL_NAMES = {1: "Tips", 2: "Secondary", 3: "Important", 4: "Urgent"}


def _ms_to_iso(ms: Any) -> str | None:
    if ms in (None, 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


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

    alarm_log: dict[str, list[dict[str, Any]]] = {}
    for level, name in _LEVEL_NAMES.items():
        bucket = sorted(
            medium_coord.alarm_log.get(level, []),
            key=lambda a: a.get("actingTime") or 0,
        )
        alarm_log[name] = [
            {
                "time": a.get("actingTimeString") or _ms_to_iso(a.get("actingTime")),
                "code": a.get("alarmCode"),
                "description": a.get("content"),
                "status": "active" if a.get("actionId") == 0 else "cleared",
            }
            for a in bucket
        ]

    return {
        "config_entry": async_redact_data(dict(entry.data), _REDACT),
        "fast_coordinator_data": async_redact_data(fast_data, set()),
        "medium_coordinator_data": async_redact_data(medium_data, set()),
        "weekly_coordinator_data": weekly_data,
        "alarm_log_30_days": alarm_log,
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
