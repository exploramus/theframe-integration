"""Image entity exposing the currently displayed artwork.

Lets dashboards use a plain picture-entity card without any URL templating
or entry-id substitution.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DATA_FILES, DATA_STATUS, DOMAIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([CurrentImage(hass, coordinator)])


class CurrentImage(TheFrameEntity, ImageEntity):
    """Serves the currently displayed image as a normal HA image entity."""

    _attr_name = "Current Image"
    _attr_icon = "mdi:image-frame"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coordinator: TheFrameCoordinator) -> None:
        TheFrameEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_current_image_picture"
        self._last_content_id: str | None = None
        self._attr_image_last_updated = dt_util.utcnow()

    def _current_file_name(self) -> str | None:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        files = (self.coordinator.data or {}).get(DATA_FILES) or []
        cid = status.get("current_content_id")
        if not cid:
            return None
        if cid != self._last_content_id:
            self._last_content_id = cid
            self._attr_image_last_updated = dt_util.utcnow()
        match = next((f for f in files if f.get("content_id") == cid), None)
        return match.get("file_name") if match else None

    async def async_image(self) -> bytes | None:
        file_name = self._current_file_name()
        if not file_name:
            return None
        url = self.coordinator.api.thumbnail_url(file_name, size=1024)
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Fetching current image failed: %s", err)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        files = (self.coordinator.data or {}).get(DATA_FILES) or []
        cid = status.get("current_content_id")
        match = next((f for f in files if f.get("content_id") == cid), None) if cid else None
        if not match:
            return {}
        return {
            "content_id": cid,
            "file_name": match.get("file_name"),
            "keywords": match.get("keywords", []),
        }
