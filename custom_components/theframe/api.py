"""Thin async client for the theframe-controller add-on."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class TheFrameApiError(Exception):
    """Raised when the add-on returns an error or is unreachable."""


class TheFrameApi:
    """Wraps the FastAPI endpoints exposed by the add-on."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base = URL(base_url.rstrip("/"))

    @property
    def base_url(self) -> str:
        return str(self._base)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = self._base / path.lstrip("/")
        try:
            async with self._session.request(
                method, url, timeout=REQUEST_TIMEOUT, **kwargs
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise TheFrameApiError(f"{method} {url}: {err}") from err

    async def status(self) -> dict:
        return await self._request("GET", "/status")

    async def list_files(self) -> list[dict]:
        data = await self._request("GET", "/list_files")
        if data.get("status") != "success":
            raise TheFrameApiError(data.get("error", "list_files failed"))
        return data.get("files", [])

    async def available_keywords(self) -> list[str]:
        data = await self._request("GET", "/available_keywords")
        if data.get("status") != "success":
            raise TheFrameApiError(data.get("error", "available_keywords failed"))
        return data.get("keywords", [])

    async def sync_files(self) -> dict:
        return await self._request("POST", "/sync_files")

    async def show_file(self, file_name: str) -> dict:
        return await self._request(
            "POST", "/show_file", json={"file_name": file_name}
        )

    async def show_image(self, content_id: str) -> dict:
        return await self._request(
            "POST", "/show_image", json={"content_id": content_id}
        )

    async def initialize_slideshow(
        self, keywords: list[str], index: int = 0
    ) -> dict:
        return await self._request(
            "POST",
            "/initialize_slideshow",
            json={"keyword_list": keywords, "slideshow_index": index},
        )

    async def initialize_slideshow_files(
        self, file_names: list[str], index: int = 0
    ) -> dict:
        return await self._request(
            "POST",
            "/initialize_slideshow_files",
            json={"file_names": file_names, "slideshow_index": index},
        )

    async def show_slideshow_next(self) -> dict:
        return await self._request("POST", "/show_slideshow_next")

    async def show_slideshow_previous(self) -> dict:
        return await self._request("POST", "/show_slideshow_previous")

    def thumbnail_url(self, file_name: str, size: int = 320) -> str:
        return str(
            (self._base / "thumbnail" / file_name).with_query({"size": str(size)})
        )
