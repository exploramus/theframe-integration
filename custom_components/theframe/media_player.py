"""Media player platform for The Frame.

Exposes The Frame as a media player so that the standard "Play" button in
HA's media browser can route an image selection to the add-on's show_file
endpoint. The Frame's art-mode display is not a DLNA renderer — generic
DLNA pushes never land in art mode — so this entity is the correct target
for media-source items from this integration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import TheFrameApiError
from .const import DATA_FILES, DATA_STATUS, DOMAIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([TheFramePlayer(coordinator)])


class TheFramePlayer(TheFrameEntity, MediaPlayerEntity):
    """Routes media-source plays into the add-on's show_file API."""

    _attr_name = "Player"
    _attr_icon = "mdi:image-frame"
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_media_player"

    # ---------- state ----------

    @property
    def state(self) -> MediaPlayerState:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        in_artmode = status.get("in_artmode")
        if in_artmode is None:
            return MediaPlayerState.OFF
        return MediaPlayerState.ON if in_artmode else MediaPlayerState.OFF

    @property
    def media_content_type(self) -> str:
        return MediaType.IMAGE

    @property
    def media_content_id(self) -> str | None:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        return status.get("current_content_id")

    @property
    def media_title(self) -> str | None:
        match = self._current_file_entry()
        return match.get("file_name") if match else None

    @property
    def media_image_url(self) -> str | None:
        """Add-on thumbnail URL for the cover.

        Set primarily so HA derives a stable media_image_hash from it
        (the hash drives the cache-busting token in the proxy URL the
        frontend asks for). The actual byte fetch is overridden in
        async_get_media_image below for explicit error handling.
        """
        match = self._current_file_entry()
        if not match:
            return None
        return self.coordinator.api.thumbnail_url(match["file_name"], size=1024)

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Provide the now-playing cover for the player UI."""
        match = self._current_file_entry()
        if not match:
            return None, None
        url = self.coordinator.api.thumbnail_url(match["file_name"], size=1024)
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None, None
                return await resp.read(), "image/jpeg"
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Fetching media image failed: %s", err)
            return None, None

    # ---------- actions ----------

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        file_name = self._extract_file_name(media_id)
        if not file_name:
            _LOGGER.warning("Cannot play unsupported media_id on The Frame: %s", media_id)
            return
        try:
            await self.coordinator.api.show_file(file_name)
        except TheFrameApiError as err:
            _LOGGER.error("show_file(%s) failed: %s", file_name, err)

    async def async_media_next_track(self) -> None:
        try:
            await self.coordinator.api.slideshow_next()
        except TheFrameApiError as err:
            _LOGGER.error("next failed: %s", err)

    async def async_media_previous_track(self) -> None:
        try:
            await self.coordinator.api.slideshow_previous()
        except TheFrameApiError as err:
            _LOGGER.error("previous failed: %s", err)

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        """Restrict the player's browse view to images already uploaded to The Frame.

        Anything else cannot be displayed in art mode (the add-on is what
        manages the upload pipeline), so showing other media sources here
        would just lead to "play does nothing" surprises.
        """
        own_root = f"media-source://{DOMAIN}/{self.coordinator.entry.entry_id}"
        # The frontend can pass None, "", "media-source://" or our own URI
        # depending on entry-point and version. Treat all non-specific
        # values as "open at the root of our own source".
        if not media_content_id or media_content_id in (
            "media-source://",
            f"media-source://{DOMAIN}",
        ):
            media_content_id = own_root
        _LOGGER.debug("Player browse_media -> %s", media_content_id)
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: (item.media_content_type or "").startswith(
                "image/"
            ),
        )

    # ---------- helpers ----------

    def _current_file_entry(self) -> dict | None:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        files = (self.coordinator.data or {}).get(DATA_FILES) or []
        cid = status.get("current_content_id")
        if not cid:
            return None
        return next((f for f in files if f.get("content_id") == cid), None)

    def _extract_file_name(self, media_id: str) -> str | None:
        """Pull the add-on file name out of whatever URL form HA hands us.

        Three shapes show up in practice:
          - media-source://theframe/<entry_id>/file/<file_name>
            (direct from this integration's media source)
          - http(s)://<addon-host>/thumbnail/<file_name>?...
            (resolved URL from this integration; round-trips back here)
          - anything else — we punt.
        """
        if media_id.startswith("media-source://"):
            tail = media_id[len("media-source://") :]
            domain, _, identifier = tail.partition("/")
            if domain == DOMAIN:
                parts = identifier.split("/", 2)
                if len(parts) == 3 and parts[1] == "file":
                    return unquote(parts[2])
            return None

        parsed = urlparse(media_id)
        if "/thumbnail/" in parsed.path:
            return unquote(parsed.path.split("/thumbnail/", 1)[1])

        return None
