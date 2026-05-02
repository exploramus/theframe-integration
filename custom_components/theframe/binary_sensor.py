"""Binary sensor platform for The Frame."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_STATUS, DOMAIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            InArtModeBinarySensor(coordinator),
            SyncPendingBinarySensor(coordinator),
        ]
    )


class InArtModeBinarySensor(TheFrameEntity, BinarySensorEntity):
    """True when the TV reports it is in art mode."""

    _attr_name = "In Art Mode"
    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_icon = "mdi:image-frame"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_in_artmode"

    @property
    def is_on(self) -> bool | None:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        value = status.get("in_artmode")
        return None if value is None else bool(value)


class SyncPendingBinarySensor(TheFrameEntity, BinarySensorEntity):
    """True while a deferred sync is queued waiting for the TV."""

    _attr_name = "Sync Pending"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:cloud-sync-outline"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_sync_pending"

    @property
    def is_on(self) -> bool:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        return bool(status.get("sync_pending"))
