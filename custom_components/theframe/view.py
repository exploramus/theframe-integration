"""HTTP views proxying add-on thumbnails through Home Assistant."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ThumbnailView(HomeAssistantView):
    """Same-origin, authenticated thumbnail endpoint.

    Proxying lets the media browser fetch thumbnails regardless of how the
    user reaches Home Assistant (local, Nabu Casa, mobile companion).
    """

    url = "/api/theframe/{entry_id}/thumbnail/{file_name:.*}"
    name = "api:theframe:thumbnail"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(
        self, request: web.Request, entry_id: str, file_name: str
    ) -> web.Response:
        bucket = self.hass.data.get(DOMAIN, {}).get(entry_id)
        if not bucket:
            return web.Response(status=404, text="entry not found")
        api = bucket["api"]
        size = request.query.get("size", "320")
        url = api.thumbnail_url(file_name, size=int(size))
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                body = await resp.read()
                return web.Response(
                    body=body,
                    status=resp.status,
                    content_type=resp.headers.get("Content-Type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("thumbnail proxy failed for %s: %s", file_name, err)
            return web.Response(status=502, text=str(err))


def register_views(hass: HomeAssistant) -> None:
    """Register once. The view itself looks up the per-entry api at request time."""
    if hass.data.get(DOMAIN, {}).get("_view_registered"):
        return
    hass.http.register_view(ThumbnailView(hass))
    hass.data.setdefault(DOMAIN, {})["_view_registered"] = True
