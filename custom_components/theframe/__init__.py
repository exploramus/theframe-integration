"""The Frame Controller integration."""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import TheFrameApi, TheFrameApiError
from .const import (
    CONF_BASE_URL,
    DOMAIN,
    PLATFORMS,
    SERVICE_NEXT,
    SERVICE_PREVIOUS,
    SERVICE_SHOW_FILE,
    SERVICE_SHOW_IMAGE,
    SERVICE_SYNC_FILES,
)
from .coordinator import TheFrameCoordinator
from .view import register_views

_LOGGER = logging.getLogger(__name__)

SHOW_FILE_SCHEMA = vol.Schema({vol.Required("file_name"): cv.string})
SHOW_IMAGE_SCHEMA = vol.Schema({vol.Required("content_id"): cv.string})


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
        "unsub_timer": None,
    }

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(p) for p in PLATFORMS]
    )

    _register_services(hass, api, coordinator)
    _arm_slideshow_timer(hass, entry, coordinator)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, [Platform(p) for p in PLATFORMS]
    )
    if unloaded:
        bucket = hass.data[DOMAIN].pop(entry.entry_id, None)
        if bucket and bucket.get("unsub_timer"):
            bucket["unsub_timer"]()
        if not hass.data[DOMAIN]:
            for svc in (
                SERVICE_SHOW_FILE,
                SERVICE_SHOW_IMAGE,
                SERVICE_SYNC_FILES,
                SERVICE_NEXT,
                SERVICE_PREVIOUS,
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
            await api.sync_files()
        finally:
            await coordinator.async_request_refresh()

    async def _next(_: ServiceCall) -> None:
        try:
            await api.show_slideshow_next()
        except TheFrameApiError as err:
            _LOGGER.error("next failed: %s", err)

    async def _prev(_: ServiceCall) -> None:
        try:
            await api.show_slideshow_previous()
        except TheFrameApiError as err:
            _LOGGER.error("previous failed: %s", err)

    hass.services.async_register(DOMAIN, SERVICE_SHOW_FILE, _show_file, SHOW_FILE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SHOW_IMAGE, _show_image, SHOW_IMAGE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SYNC_FILES, _sync)
    hass.services.async_register(DOMAIN, SERVICE_NEXT, _next)
    hass.services.async_register(DOMAIN, SERVICE_PREVIOUS, _prev)


def _arm_slideshow_timer(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: TheFrameCoordinator
) -> None:
    """Replace the time_pattern automation with an internal 1-minute tick."""

    async def _tick(_now) -> None:
        if not coordinator.show_enabled:
            return
        interval = max(1, int(coordinator.interval_minutes))
        # Only advance on aligned minute boundaries, like the original automation.
        if _now.minute % interval != 0:
            return
        status = (coordinator.data or {}).get("status", {})
        if not status.get("in_artmode"):
            return
        try:
            await coordinator.api.show_slideshow_next()
        except TheFrameApiError as err:
            _LOGGER.debug("slideshow next failed: %s", err)

    unsub = async_track_time_interval(hass, _tick, timedelta(minutes=1))
    hass.data[DOMAIN][entry.entry_id]["unsub_timer"] = unsub
