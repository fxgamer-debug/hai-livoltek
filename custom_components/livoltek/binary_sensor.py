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

from .const import COORDINATOR_FAST, DOMAIN
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
