"""Base entity for the Livoltek integration."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import (
    ATTRIBUTION,
    CONF_INVERTER_SN,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DOMAIN,
)


class LivoltekEntity(CoordinatorEntity[DataUpdateCoordinator[Any]]):
    """Base entity that ties every sensor/binary/button to the same device."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[Any],
        entry: ConfigEntry,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_SITE_ID]}_{description.key}"

        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        model = data.get("productTypeName") if data else None
        sw_version = data.get("armVersion") if data else None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_SITE_ID])},
            manufacturer="Livoltek",
            name=entry.data.get(CONF_SITE_NAME) or "Livoltek",
            model=model,
            serial_number=entry.data.get(CONF_INVERTER_SN),
            sw_version=sw_version,
        )
