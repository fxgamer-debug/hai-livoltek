"""Button platform for the Livoltek integration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COORDINATOR_MEDIUM, COORDINATOR_WEEKLY, DOMAIN
from .entity import LivoltekEntity


@dataclass(frozen=True, kw_only=True)
class LivoltekButtonEntityDescription(ButtonEntityDescription):
    """Describes a Livoltek button."""

    coordinator_key: str


BUTTONS: tuple[LivoltekButtonEntityDescription, ...] = (
    LivoltekButtonEntityDescription(
        key="refresh_settings",
        translation_key="refresh_settings",
        name="Refresh inverter settings",
        device_class=ButtonDeviceClass.RESTART,
        coordinator_key=COORDINATOR_WEEKLY,
    ),
    LivoltekButtonEntityDescription(
        key="refresh_status",
        translation_key="refresh_status",
        name="Refresh status",
        device_class=ButtonDeviceClass.RESTART,
        coordinator_key=COORDINATOR_MEDIUM,
    ),
)


class LivoltekRefreshButton(LivoltekEntity, ButtonEntity):
    """Forces a refresh on its underlying coordinator."""

    entity_description: LivoltekButtonEntityDescription

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register Livoltek buttons."""
    data = hass.data[DOMAIN][entry.entry_id]
    entities = [
        LivoltekRefreshButton(data[desc.coordinator_key], entry, desc)
        for desc in BUTTONS
    ]
    async_add_entities(entities)
