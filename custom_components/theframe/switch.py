"""Switch platform for The Frame."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_KEYWORDS, DOMAIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "kw"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    async_add_entities([ShowSlideshowSwitch(coordinator)])

    known: set[str] = set()

    @callback
    def _sync_keywords() -> None:
        keywords = (coordinator.data or {}).get(DATA_KEYWORDS, []) or []
        new = [kw for kw in keywords if kw not in known]
        if not new:
            return
        async_add_entities([KeywordSwitch(coordinator, kw) for kw in new])
        known.update(new)

    _sync_keywords()
    entry.async_on_unload(coordinator.async_add_listener(_sync_keywords))


class ShowSlideshowSwitch(TheFrameEntity, SwitchEntity):
    """Master toggle for the timed slideshow advance."""

    _attr_name = "Slideshow"
    _attr_icon = "mdi:play-box-multiple"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_show_slideshow"

    @property
    def is_on(self) -> bool:
        return self.coordinator.show_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.show_enabled = True
        if self.coordinator.active_keywords:
            await self.coordinator.push_slideshow_keywords()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.show_enabled = False
        self.async_write_ha_state()


class KeywordSwitch(TheFrameEntity, SwitchEntity):
    """One switch per keyword. Replaces the template switches from DOCS.md."""

    _attr_icon = "mdi:tag"

    def __init__(self, coordinator: TheFrameCoordinator, keyword: str) -> None:
        super().__init__(coordinator)
        self._keyword = keyword
        self._attr_name = f"Art {keyword}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_kw_{_slug(keyword)}"

    @property
    def available(self) -> bool:
        keywords = (self.coordinator.data or {}).get(DATA_KEYWORDS, []) or []
        return self._keyword in keywords

    @property
    def is_on(self) -> bool:
        return self._keyword in self.coordinator.active_keywords

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.active_keywords.add(self._keyword)
        await self.coordinator.push_slideshow_keywords()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.active_keywords.discard(self._keyword)
        if self.coordinator.active_keywords:
            await self.coordinator.push_slideshow_keywords()
        self.async_write_ha_state()
