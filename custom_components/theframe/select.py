"""Select platform: pick a single keyword as the active slideshow filter.

Replaces input_select.theframe_slideshow_keyword_select from DOCS.md.
Selecting an option resets active_keywords to just that keyword and
re-initialises the slideshow on the add-on.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_KEYWORDS, DOMAIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([KeywordPresetSelect(coordinator)])


class KeywordPresetSelect(TheFrameEntity, SelectEntity):
    """Single-keyword preset picker. Useful for HomeKit / mobile UI."""

    _attr_name = "Keyword"
    _attr_icon = "mdi:tag-multiple"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_keyword_select"

    @property
    def options(self) -> list[str]:
        return list((self.coordinator.data or {}).get(DATA_KEYWORDS, []) or [])

    @property
    def current_option(self) -> str | None:
        # Only report a value when exactly one keyword is active — otherwise
        # the select would silently misrepresent the multi-selection state.
        active = self.coordinator.active_keywords
        if len(active) == 1:
            return next(iter(active))
        return None

    async def async_select_option(self, option: str) -> None:
        self.coordinator.active_keywords = {option}
        await self.coordinator.push_slideshow_keywords()
        self.async_write_ha_state()
        # Push fresh state to the per-keyword switches as well.
        self.coordinator.async_update_listeners()
