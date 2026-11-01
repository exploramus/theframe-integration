"""Data coordinator for The Frame."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

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

EVENTS_RETRY_MIN = 5
EVENTS_RETRY_MAX = 300


class TheFrameCoordinator(DataUpdateCoordinator[dict]):
    """Mirrors the add-on: status pushed over its event stream, files and keywords polled.

    The add-on owns all state, including the slideshow and its timer; the
    entities only read it here and change it through the API.
    """

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

    async def _async_update_data(self) -> dict:
        try:
            status = await self.api.status()
            keywords = await self.api.keywords()
            files = await self.api.files()
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

    @property
    def status(self) -> dict:
        return (self.data or {}).get(DATA_STATUS) or {}

    @property
    def slideshow(self) -> dict:
        return self.status.get("slideshow") or {}

    async def set_slideshow(self, **changes: Any) -> None:
        """Change slideshow settings in the add-on and mirror the result at once."""
        try:
            slideshow = await self.api.slideshow_update(**changes)
        except TheFrameApiError as err:
            _LOGGER.error("Changing the slideshow failed: %s", err)
            return
        if self.data is not None:
            self.async_set_updated_data(
                {**self.data, DATA_STATUS: {**self.status, "slideshow": slideshow}}
            )

    async def listen_for_events(self) -> None:
        """Keep the add-on's event stream open; reconnect with back-off when it drops."""
        delay = EVENTS_RETRY_MIN
        while True:
            connected = False
            try:
                async for event, data in self.api.events():
                    if not connected:
                        _LOGGER.debug("Event stream connected")
                        connected = True
                    delay = EVENTS_RETRY_MIN
                    if event == "status" and self.data is not None:
                        self.async_set_updated_data({**self.data, DATA_STATUS: data})
                    elif event == "files":
                        await self.async_request_refresh()
                _LOGGER.debug("Event stream ended")
            except TheFrameApiError as err:
                _LOGGER.debug("Event stream failed: %s", err)
            await asyncio.sleep(delay)
            delay = min(delay * 2, EVENTS_RETRY_MAX)
