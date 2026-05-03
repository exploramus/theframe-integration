"""Media source: browse The Frame's image library with thumbnails."""

from __future__ import annotations

from datetime import timedelta
import logging
from urllib.parse import quote

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.http.auth import async_sign_path

from .const import DATA_FILES, DATA_STATUS, DOMAIN
from .coordinator import TheFrameCoordinator

_LOGGER = logging.getLogger(__name__)

# How long signed thumbnail URLs stay valid. Long enough to survive a media
# browser session and a clicked-through preview, short enough that a leaked
# URL is not useful indefinitely.
_THUMBNAIL_SIGNATURE_TTL = timedelta(hours=12)

MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


def _mime_for(file_name: str) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return MIME_TYPES.get(ext, "image/jpeg")


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    return TheFrameMediaSource(hass)


class TheFrameMediaSource(MediaSource):
    """Lists images grouped by config entry, then by keyword."""

    name = "The Frame"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    # ---------- helpers ----------

    def _entries(self) -> dict[str, dict]:
        return {
            eid: bucket
            for eid, bucket in self.hass.data.get(DOMAIN, {}).items()
            if isinstance(bucket, dict) and "coordinator" in bucket
        }

    def _coordinator(self, entry_id: str) -> TheFrameCoordinator | None:
        bucket = self.hass.data.get(DOMAIN, {}).get(entry_id)
        if not bucket or "coordinator" not in bucket:
            return None
        return bucket["coordinator"]

    def _entry_thumbnail(self, entry_id: str, coordinator: TheFrameCoordinator) -> str | None:
        """Pick a representative thumbnail for an entry.

        Prefers the image currently shown on the TV (so the source icon
        doubles as a live preview); falls back to the first known file,
        and ultimately to None which lets HA show its generic folder icon.
        """
        status = (coordinator.data or {}).get(DATA_STATUS) or {}
        files = (coordinator.data or {}).get(DATA_FILES, []) or []
        if not files:
            return None
        cid = status.get("current_content_id")
        if cid:
            match = next((f for f in files if f.get("content_id") == cid), None)
            if match:
                return self._thumb_url(entry_id, match["file_name"])
        return self._thumb_url(entry_id, files[0]["file_name"])

    def _root_thumbnail(self) -> str | None:
        """Thumbnail for the source-picker entry — first entry that has data."""
        for eid, bucket in self._entries().items():
            thumb = self._entry_thumbnail(eid, bucket["coordinator"])
            if thumb:
                return thumb
        return None

    def _signed(self, path: str) -> str:
        """Sign an /api/theframe/... path so the frontend can fetch it.

        Uses the refresh token from the current websocket / HTTP request
        context. Signing with HA's content user is rejected by HA's auth
        middleware for views with requires_auth=True, so we deliberately
        do NOT pass use_content_user here.

        Falls back to the unsigned path when there's no usable context.
        Grid thumbnails still load fine in that case via the browser
        session cookie; only the resolved media URL (clicked image in
        the media browser) really depends on the signature.
        """
        try:
            return async_sign_path(self.hass, path, _THUMBNAIL_SIGNATURE_TTL)
        except Exception as err:  # noqa: BLE001 — defensive, log and fall back
            _LOGGER.warning(
                "async_sign_path failed for %s, returning unsigned URL: %s",
                path,
                err,
            )
            return path

    def _thumb_url(self, entry_id: str, file_name: str, size: int = 320) -> str:
        path = f"/api/theframe/{entry_id}/thumbnail/{quote(file_name, safe='')}"
        if size != 320:
            path = f"{path}?size={size}"
        return self._signed(path)

    # ---------- media-source API ----------

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        # identifier shape: "<entry_id>/file/<file_name>"
        parts = (item.identifier or "").split("/", 2)
        if len(parts) != 3 or parts[1] != "file":
            raise Unresolvable(f"unsupported identifier: {item.identifier}")
        entry_id, _, file_name = parts
        coordinator = self._coordinator(entry_id)
        if not coordinator:
            raise Unresolvable("entry not loaded")
        # Bypass the HA-side proxy here. Returning the add-on's own URL
        # (e.g. http://homeassistant.local:5000/thumbnail/<file>?size=1024)
        # avoids two failure modes for external players (DLNA, Cast):
        #   - they don't carry an HA auth context, so signed proxy URLs
        #     don't help them
        #   - some renderers strip query strings or can't resolve the
        #     same hostnames the browser uses
        # The add-on has no auth and lives on the LAN, which is exactly
        # what DLNA / Cast expect to find at the other end of a media URL.
        # Grid thumbnails still go through the proxy, where browser
        # cookies / signed URLs do the right thing.
        url = coordinator.api.thumbnail_url(file_name, size=1024)
        return PlayMedia(url=url, mime_type=_mime_for(file_name))

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        ident = item.identifier or ""

        if not ident:
            return self._browse_root()

        parts = ident.split("/", 3)
        entry_id = parts[0]
        coordinator = self._coordinator(entry_id)
        if not coordinator:
            raise Unresolvable("entry not loaded")

        if len(parts) == 1:
            return self._browse_entry(entry_id, coordinator)
        if len(parts) >= 3 and parts[1] == "keyword":
            return self._browse_keyword(entry_id, coordinator, parts[2])
        if len(parts) >= 3 and parts[1] == "all":
            return self._browse_all(entry_id, coordinator)

        raise Unresolvable(f"unknown path: {ident}")

    # ---------- browse helpers ----------

    def _browse_root(self) -> BrowseMediaSource:
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=eid,
                media_class=MediaClass.DIRECTORY,
                media_content_type="",
                title=bucket["coordinator"].entry.title or "The Frame",
                can_play=False,
                can_expand=True,
                thumbnail=self._entry_thumbnail(eid, bucket["coordinator"]),
            )
            for eid, bucket in self._entries().items()
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title="The Frame",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
            children=children,
            thumbnail=self._root_thumbnail(),
        )

    def _browse_entry(
        self, entry_id: str, coordinator: TheFrameCoordinator
    ) -> BrowseMediaSource:
        files = (coordinator.data or {}).get(DATA_FILES, []) or []
        keywords = sorted({kw for f in files for kw in (f.get("keywords") or [])})

        children: list[BrowseMediaSource] = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/all",
                media_class=MediaClass.DIRECTORY,
                media_content_type="",
                title=f"All images ({len(files)})",
                can_play=False,
                can_expand=True,
            )
        ]
        for kw in keywords:
            count = sum(1 for f in files if kw in (f.get("keywords") or []))
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/keyword/{kw}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type="",
                    title=f"{kw} ({count})",
                    can_play=False,
                    can_expand=True,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=entry_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=coordinator.entry.title or "The Frame",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
            children=children,
            thumbnail=self._entry_thumbnail(entry_id, coordinator),
        )

    def _browse_all(
        self, entry_id: str, coordinator: TheFrameCoordinator
    ) -> BrowseMediaSource:
        files = (coordinator.data or {}).get(DATA_FILES, []) or []
        return self._make_image_listing(entry_id, "All images", files)

    def _browse_keyword(
        self, entry_id: str, coordinator: TheFrameCoordinator, keyword: str
    ) -> BrowseMediaSource:
        files = (coordinator.data or {}).get(DATA_FILES, []) or []
        filtered = [f for f in files if keyword in (f.get("keywords") or [])]
        return self._make_image_listing(entry_id, keyword, filtered, identifier_suffix=f"keyword/{keyword}")

    def _make_image_listing(
        self,
        entry_id: str,
        title: str,
        files: list[dict],
        identifier_suffix: str = "all",
    ) -> BrowseMediaSource:
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/file/{f['file_name']}",
                media_class=MediaClass.IMAGE,
                media_content_type=_mime_for(f["file_name"]),
                title=f["file_name"],
                can_play=True,
                can_expand=False,
                thumbnail=self._thumb_url(entry_id, f["file_name"]),
            )
            for f in files
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{identifier_suffix}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.IMAGE,
            title=title,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.IMAGE,
            children=children,
        )
