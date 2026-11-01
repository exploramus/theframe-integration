"""Thin async client for the theframe-controller add-on's /api/v1."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
# The add-on sends a keep-alive comment every 15 s, so a silent stream is a dead one.
EVENTS_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60)

# Every endpoint the integration uses. tests/test_contract.py checks each one
# against the add-on's OpenAPI document, so the two cannot drift apart.
ENDPOINTS: dict[str, tuple[str, str]] = {
    "status": ("GET", "/api/v1/status"),
    "events": ("GET", "/api/v1/events"),
    "files": ("GET", "/api/v1/files"),
    "keywords": ("GET", "/api/v1/keywords"),
    "sync": ("POST", "/api/v1/sync"),
    "show": ("POST", "/api/v1/show"),
    "slideshow_update": ("PUT", "/api/v1/slideshow"),
    "slideshow_next": ("POST", "/api/v1/slideshow/next"),
    "slideshow_previous": ("POST", "/api/v1/slideshow/previous"),
    "slideshow_show": ("POST", "/api/v1/slideshow/show"),
    "thumbnail": ("GET", "/api/v1/thumbnail/{file_name}"),
    "collections": ("GET", "/api/v1/collections"),
    "collection_save": ("PUT", "/api/v1/collections/{name}"),
    "collection_delete": ("DELETE", "/api/v1/collections/{name}"),
}


class TheFrameApiError(Exception):
    """Raised when the add-on returns an error or is unreachable.

    ``code`` is the add-on's error code (e.g. ``tv_not_in_art_mode``),
    ``None`` when the add-on could not be reached.
    """

    def __init__(self, message: str, code: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class TheFrameApi:
    """Wraps the add-on's versioned API."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base = URL(base_url.rstrip("/"))

    @property
    def base_url(self) -> str:
        return str(self._base)

    def _url(self, endpoint: str, **params: str) -> URL:
        path = ENDPOINTS[endpoint][1]
        for key, value in params.items():
            path = path.replace("{" + key + "}", quote(str(value), safe=""))
        return URL(str(self._base) + path, encoded=True)

    async def _request(self, endpoint: str, json_body: Any = None, **params: str) -> dict:
        method = ENDPOINTS[endpoint][0]
        url = self._url(endpoint, **params)
        try:
            async with self._session.request(
                method, url, json=json_body, timeout=REQUEST_TIMEOUT
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    error = (data or {}).get("error") or {}
                    raise TheFrameApiError(
                        error.get("message") or f"{method} {url}: HTTP {resp.status}",
                        code=error.get("code"),
                        status=resp.status,
                    )
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise TheFrameApiError(f"{method} {url}: {err}") from err

    async def status(self) -> dict:
        return await self._request("status")

    async def files(self) -> list[dict]:
        return (await self._request("files")).get("files", [])

    async def keywords(self) -> list[str]:
        return (await self._request("keywords")).get("keywords", [])

    async def sync(self) -> dict:
        """Start a sync job. Raises with code ``job_conflict`` if one is queued or running."""
        return (await self._request("sync")).get("job", {})

    async def show_file(self, file_name: str) -> dict:
        return await self._request("show", {"file_name": file_name})

    async def show_image(self, content_id: str) -> dict:
        return await self._request("show", {"content_id": content_id})

    async def slideshow_update(self, **changes: Any) -> dict:
        """Change slideshow settings: enabled, interval_minutes, mode, keywords, files, position."""
        return await self._request("slideshow_update", changes)

    async def slideshow_next(self) -> dict:
        return await self._request("slideshow_next")

    async def slideshow_previous(self) -> dict:
        return await self._request("slideshow_previous")

    async def slideshow_show(self, position: int) -> dict:
        return await self._request("slideshow_show", {"position": position})

    async def collections(self) -> list[dict]:
        return (await self._request("collections")).get("collections", [])

    async def save_collection(self, name: str, **content: Any) -> dict:
        """Save a named slideshow preset: keywords=[...] or files=[...]; without content the current slideshow."""
        return await self._request("collection_save", content, name=name)

    async def delete_collection(self, name: str) -> dict:
        return await self._request("collection_delete", name=name)

    async def events(self) -> AsyncIterator[tuple[str, Any]]:
        """Yield (event, data) from the add-on's server-sent events until the stream ends."""
        url = self._url("events")
        try:
            async with self._session.get(url, timeout=EVENTS_TIMEOUT) as resp:
                resp.raise_for_status()
                event, data = "message", []
                async for raw in resp.content:
                    line = raw.decode().rstrip("\r\n")
                    if not line:
                        if data:
                            yield event, json.loads("\n".join(data))
                        event, data = "message", []
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise TheFrameApiError(f"event stream {url}: {err}") from err

    def thumbnail_url(self, file_name: str, size: int = 320) -> str:
        return str(self._url("thumbnail", file_name=file_name).with_query({"size": str(size)}))
