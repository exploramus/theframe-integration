"""Data coordinator for The Frame."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import TheFrameApi, TheFrameApiError
from .const import (
    CONF_IGNORE_KEYWORDS,
    DATA_FILES,
    DATA_KEYWORDS,
    DATA_STATUS,
    DOMAIN,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class TheFrameCoordinator(DataUpdateCoordinator[dict]):
    """Polls the add-on for status, keywords and file list."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: TheFrameApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.api = api
        self.entry = entry
        # Slideshow state lives client-side now (replaces the input_text/input_select
        # combo from DOCS.md). The set is the source of truth; whenever it changes
        # we tell the add-on to re-initialise its slideshow.
        self.active_keywords: set[str] = set()
        self.show_enabled: bool = False
        self.interval_minutes: int = 2

    async def _async_update_data(self) -> dict:
        try:
            status = await self.api.status()
            keywords = await self.api.available_keywords()
            files = await self.api.list_files()
        except TheFrameApiError as err:
            raise UpdateFailed(str(err)) from err

        ignored = set(self.entry.options.get(CONF_IGNORE_KEYWORDS, []) or [])
        if ignored:
            keywords = [k for k in keywords if k not in ignored]
            files = [
                {
                    **f,
                    "keywords": [
                        k for k in (f.get("keywords") or []) if k not in ignored
                    ],
                }
                for f in files
            ]

        return {
            DATA_STATUS: status,
            DATA_KEYWORDS: keywords,
            DATA_FILES: files,
        }

    async def push_slideshow_keywords(self) -> None:
        """Tell the add-on which keywords are currently active."""
        keywords = sorted(self.active_keywords)
        if not keywords:
            return
        try:
            await self.api.initialize_slideshow(keywords)
        except TheFrameApiError as err:
            _LOGGER.warning("initialize_slideshow failed: %s", err)
