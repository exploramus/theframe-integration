"""The Frame Controller integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TheFrameApi, TheFrameApiError
from .const import (
    CONF_BASE_URL,
    DATA_FILES,
    DOMAIN,
    PLATFORMS,
    SERVICE_NEXT,
    SERVICE_PLAY_FILES,
    SERVICE_PREVIOUS,
    SERVICE_SHOW_FILE,
    SERVICE_SHOW_IMAGE,
    SERVICE_SYNC_FILES,
)
from .coordinator import TheFrameCoordinator
from .view import register_views

# Eager import so HA's media_source discovery finds the module already in
# sys.modules and skips the blocking importlib.import_module call from the
# event loop. The actual MediaSource is registered by HA via the platform
# protocol; this line just primes the cache.
from . import media_source as _media_source  # noqa: F401

_LOGGER = logging.getLogger(__name__)

SHOW_FILE_SCHEMA = vol.Schema({vol.Required("file_name"): cv.string})
SHOW_IMAGE_SCHEMA = vol.Schema({vol.Required("content_id"): cv.string})
PLAY_FILES_SCHEMA = vol.Schema(
    {
        vol.Required("file_names"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("slideshow_index", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
        vol.Optional("start", default=True): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up The Frame from a config entry."""
    session = async_get_clientsession(hass)
    api = TheFrameApi(session, entry.data[CONF_BASE_URL])

    coordinator = TheFrameCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    register_views(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(p) for p in PLATFORMS]
    )

    _register_services(hass, api, coordinator)
    # Status changes arrive over the add-on's event stream; polling stays as fallback.
    entry.async_create_background_task(
        hass, coordinator.listen_for_events(), f"{DOMAIN}_events_{entry.entry_id}"
    )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, [Platform(p) for p in PLATFORMS]
    )
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for svc in (
                SERVICE_SHOW_FILE,
                SERVICE_SHOW_IMAGE,
                SERVICE_SYNC_FILES,
                SERVICE_NEXT,
                SERVICE_PREVIOUS,
                SERVICE_PLAY_FILES,
            ):
                hass.services.async_remove(DOMAIN, svc)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(
    hass: HomeAssistant, api: TheFrameApi, coordinator: TheFrameCoordinator
) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SHOW_FILE):
        return

    async def _show_file(call: ServiceCall) -> None:
        try:
            await api.show_file(call.data["file_name"])
        except TheFrameApiError as err:
            _LOGGER.error("show_file failed: %s", err)

    async def _show_image(call: ServiceCall) -> None:
        try:
            await api.show_image(call.data["content_id"])
        except TheFrameApiError as err:
            _LOGGER.error("show_image failed: %s", err)

    async def _sync(_: ServiceCall) -> None:
        try:
            job = await api.sync()
            _LOGGER.debug("sync job %s started", job.get("id"))
        except TheFrameApiError as err:
            if err.code == "job_conflict":
                _LOGGER.info("A sync is already queued or running")
            else:
                _LOGGER.error("sync failed: %s", err)

    async def _next(_: ServiceCall) -> None:
        try:
            await api.slideshow_next()
        except TheFrameApiError as err:
            _LOGGER.error("next failed: %s", err)

    async def _prev(_: ServiceCall) -> None:
        try:
            await api.slideshow_previous()
        except TheFrameApiError as err:
            _LOGGER.error("previous failed: %s", err)

    async def _play_files(call: ServiceCall) -> None:
        """Switch the add-on's slideshow to an explicit, ordered playlist."""
        file_names = call.data["file_names"]
        index = call.data.get("slideshow_index", 0)
        uploaded = {f.get("file_name") for f in (coordinator.data or {}).get(DATA_FILES, [])}
        if missing := [f for f in file_names if f not in uploaded]:
            _LOGGER.warning(
                "play_files: %d file(s) skipped because not uploaded: %s",
                len(missing),
                ", ".join(missing),
            )
        # position is the image shown last, so the next advance shows `index`
        await coordinator.set_slideshow(mode="files", files=file_names, position=index - 1)
        if call.data.get("start", True):
            try:
                await api.slideshow_next()
            except TheFrameApiError as err:
                _LOGGER.error("play_files: initial show failed: %s", err)

    hass.services.async_register(DOMAIN, SERVICE_SHOW_FILE, _show_file, SHOW_FILE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SHOW_IMAGE, _show_image, SHOW_IMAGE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SYNC_FILES, _sync)
    hass.services.async_register(DOMAIN, SERVICE_NEXT, _next)
    hass.services.async_register(DOMAIN, SERVICE_PREVIOUS, _prev)
    hass.services.async_register(DOMAIN, SERVICE_PLAY_FILES, _play_files, PLAY_FILES_SCHEMA)
