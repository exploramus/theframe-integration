"""Button platform for The Frame."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import TheFrameApiError
from .const import DOMAIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            SyncButton(coordinator),
            NextButton(coordinator),
            PreviousButton(coordinator),
        ]
    )


class _Base(TheFrameEntity, ButtonEntity):
    pass


class SyncButton(_Base):
    _attr_name = "Sync Files"
    _attr_icon = "mdi:cloud-sync"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_sync"

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.sync()
        except TheFrameApiError as err:
            if err.code == "job_conflict":
                _LOGGER.info("A sync is already queued or running")
            else:
                _LOGGER.error("sync failed: %s", err)


class NextButton(_Base):
    _attr_name = "Next Image"
    _attr_icon = "mdi:skip-next"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_next"

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.slideshow_next()
        except TheFrameApiError as err:
            _LOGGER.error("next failed: %s", err)


class PreviousButton(_Base):
    _attr_name = "Previous Image"
    _attr_icon = "mdi:skip-previous"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_previous"

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.slideshow_previous()
        except TheFrameApiError as err:
            _LOGGER.error("previous failed: %s", err)
