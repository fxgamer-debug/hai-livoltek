"""Diagnostics for the Livoltek integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_LOGIN_ACCOUNT,
    CONF_PASSWORD_HASH,
    CONF_SITE_ID,
    COORDINATOR_FAST,
    COORDINATOR_MEDIUM,
    COORDINATOR_WEEKLY,
    DOMAIN,
    ALARM_LEVEL_IMPORTANT,
    ALARM_LEVEL_SECONDARY,
    ALARM_LEVEL_TIPS,
    ALARM_LEVEL_URGENT,
)


_REDACT = {CONF_PASSWORD_HASH, CONF_ACCESS_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Livoltek config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    fast_coord = data[COORDINATOR_FAST]
    medium_coord = data[COORDINATOR_MEDIUM]
    weekly_coord = data[COORDINATOR_WEEKLY]
    api = data.get("api")

    fast_data = fast_coord.data or {}
    medium_data = medium_coord.data or {}
    weekly_data = weekly_coord.data or {}

    # Fetch full 30-day alarm log (best effort).
    full_alarms: list[dict[str, Any]] | None = None
    try:
        if api is not None:
            full_alarms = await api.get_alarms_full_log(
                entry.data[CONF_SITE_ID],
                login_account=entry.data[CONF_LOGIN_ACCOUNT],
                password_hash=entry.data[CONF_PASSWORD_HASH],
            )
    except Exception:  # noqa: BLE001
        full_alarms = None

    level_names = [ALARM_LEVEL_TIPS, ALARM_LEVEL_SECONDARY, ALARM_LEVEL_IMPORTANT, ALARM_LEVEL_URGENT]
    alarm_log_grouped: dict[str, list[dict[str, Any]]] = {}
    for level in level_names:
        alarms = [a for a in (full_alarms or []) if a.get("level") == level]
        alarms.sort(key=lambda a: a.get("actingTime", 0))
        alarm_log_grouped[level] = [
            {
                "time": a.get("actingTimeString"),
                "code": a.get("alarmCode"),
                "description": a.get("content"),
                "status": "active" if a.get("actionId") == 0 else "cleared",
            }
            for a in alarms
        ]

    return {
        "config_entry": async_redact_data(dict(entry.data), _REDACT),
        "fast_coordinator_data": fast_data,
        "medium_coordinator_data": medium_data,
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
        "alarm_log_30_days": alarm_log_grouped if full_alarms is not None else getattr(medium_coord, "alarm_log", {}),
    }
