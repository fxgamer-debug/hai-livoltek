"""Binary sensor platform for Livoltek."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALARM_ACTIVE_LEVELS,
    ALARM_LEVEL_IMPORTANT,
    ALARM_LEVEL_URGENT,
    COORDINATOR_FAST,
    COORDINATOR_MEDIUM,
    DOMAIN,
)
from .entity import LivoltekEntity


@dataclass(frozen=True, kw_only=True)
class LivoltekBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Livoltek binary sensor."""

    is_on_fn: Callable[[dict[str, Any]], bool | None]
    coordinator_key: str
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _online_is_on(data: dict[str, Any]) -> bool | None:
    """`pcsStatus == 3` is the API's "offline" code."""
    if not isinstance(data, dict):
        return None
    pcs = data.get("pcsStatus")
    if pcs is None:
        return None
    try:
        return int(float(pcs)) != 3
    except (TypeError, ValueError):
        return None


BINARY_SENSORS: tuple[LivoltekBinarySensorEntityDescription, ...] = (
    LivoltekBinarySensorEntityDescription(
        key="online",
        translation_key="online",
        name="Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        coordinator_key=COORDINATOR_FAST,
        is_on_fn=_online_is_on,
    ),
    LivoltekBinarySensorEntityDescription(
        key="active_alarm",
        translation_key="active_alarm",
        name="Active alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        coordinator_key=COORDINATOR_MEDIUM,
        is_on_fn=lambda d: any(
            a.get("level") in ALARM_ACTIVE_LEVELS and a.get("actionId") == 0
            for a in (d or {}).get("alarms", [])
        ),
        extra_attrs_fn=lambda d: {
            "important_count": sum(
                1
                for a in (d or {}).get("alarms", [])
                if a.get("level") == ALARM_LEVEL_IMPORTANT and a.get("actionId") == 0
            ),
            "urgent_count": sum(
                1
                for a in (d or {}).get("alarms", [])
                if a.get("level") == ALARM_LEVEL_URGENT and a.get("actionId") == 0
            ),
            "last_alarm": ((d or {}).get("alarms") or [{}])[0].get("content")
            if (d or {}).get("alarms")
            else None,
        },
    ),
)


class LivoltekBinarySensor(LivoltekEntity, BinarySensorEntity):
    """Binary sensor backed by an is_on_fn."""

    entity_description: LivoltekBinarySensorEntityDescription

    @property
    def is_on(self) -> bool | None:
        try:
            return self.entity_description.is_on_fn(self.coordinator.data or {})
        except Exception:  # noqa: BLE001
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.extra_attrs_fn is None:
            return None
        try:
            return self.entity_description.extra_attrs_fn(self.coordinator.data or {})
        except Exception:  # noqa: BLE001
            return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register Livoltek binary sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    entities = [
        LivoltekBinarySensor(data[desc.coordinator_key], entry, desc)
        for desc in BINARY_SENSORS
    ]
    async_add_entities(entities)
