"""Common entity base for The Frame."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TheFrameCoordinator


class TheFrameEntity(CoordinatorEntity[TheFrameCoordinator]):
    """All entities share the same logical device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="The Frame",
            manufacturer="Samsung",
            model="The Frame",
            configuration_url=coordinator.api.base_url,
        )
