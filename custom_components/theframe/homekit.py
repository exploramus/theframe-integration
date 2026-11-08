"""Reload the HomeKit bridge when the collection list changes (option reload_homekit).

The HomeKit bridge builds a select's switches (one per option) when it starts, so new,
renamed or deleted collections reach Apple Home only after the bridge reloads. Only
bridges that expose the collection select are reloaded, once, RELOAD_DELAY seconds
after the last change, so a long editing session in the collections editor causes a
single reload.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entityfilter import FILTER_SCHEMA
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .coordinator import TheFrameCoordinator

_LOGGER = logging.getLogger(__name__)

HOMEKIT_DOMAIN = "homekit"
RELOAD_DELAY = 30


def bridge_exposes(options: dict, entity_id: str) -> bool:
    """Whether a HomeKit entry's entity filter lets the entity through (an empty filter exposes all)."""
    try:
        entity_filter = FILTER_SCHEMA(options.get("filter") or {})
    except Exception:  # noqa: BLE001 - an unreadable filter means we cannot tell
        return False
    return entity_filter(entity_id)


class HomeKitReloader:
    """Watches the add-on's collection names and reloads the bridges that show them."""

    def __init__(self, hass: HomeAssistant, coordinator: TheFrameCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._names: list[str] | None = None
        self._cancel_timer: Callable[[], None] | None = None

    @callback
    def start(self) -> Callable[[], None]:
        """Start watching; the returned callable stops it and drops a pending reload."""
        self._names = self._current_names()
        remove_listener = self.coordinator.async_add_listener(self._data_updated)

        @callback
        def stop() -> None:
            remove_listener()
            self._cancel()

        return stop

    def _current_names(self) -> list[str]:
        return list(self.coordinator.status.get("collections") or [])

    @callback
    def _cancel(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _data_updated(self) -> None:
        names = self._current_names()
        if names == self._names:
            return
        self._names = names
        self._cancel()
        self._cancel_timer = async_call_later(self.hass, RELOAD_DELAY, self._reload)

    async def _reload(self, _now: datetime) -> None:
        self._cancel_timer = None
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "select", DOMAIN, f"{self.coordinator.entry.entry_id}_collection"
        )
        if entity_id is None:
            return
        for bridge in self.hass.config_entries.async_entries(HOMEKIT_DOMAIN):
            if bridge.state is not ConfigEntryState.LOADED:
                continue
            if not bridge_exposes(dict(bridge.options), entity_id):
                continue
            _LOGGER.info("Collections changed, reloading HomeKit bridge %s", bridge.title)
            await self.hass.config_entries.async_reload(bridge.entry_id)
