"""Select platform for The Frame.

Apple Home has no list or number controls, but the HomeKit bridge shows a
select as one tile with one switch per option. The collection select puts all
slideshow presets into a single tile; the interval select replaces an
input_select helper plus sync automations.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SLIDESHOW_INTERVAL_CHOICES_MIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity


def _label(minutes: int) -> str:
    return f"{minutes} min"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([CollectionSelect(coordinator), SlideshowIntervalSelect(coordinator)])


class CollectionSelect(TheFrameEntity, SelectEntity):
    """The add-on's slideshow collections (named keyword sets or playlists); picking one loads it."""

    _attr_name = "Collection"
    _attr_icon = "mdi:image-multiple-outline"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_collection"

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.status.get("collections") or [])

    @property
    def available(self) -> bool:
        return super().available and bool(self.options)

    @property
    def current_option(self) -> str | None:
        """None while the slideshow was changed by hand (keyword switches, play_files)."""
        return self.coordinator.slideshow.get("collection")

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.set_slideshow(collection=option)


class SlideshowIntervalSelect(TheFrameEntity, SelectEntity):
    """Minutes between slideshow advances, as a choice for Apple Home."""

    _attr_name = "Slideshow Interval"
    _attr_icon = "mdi:timer-outline"
    _attr_options = [_label(m) for m in SLIDESHOW_INTERVAL_CHOICES_MIN]

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_interval_select"

    @property
    def current_option(self) -> str | None:
        """None when the interval was set to a value that is not a choice (via the number entity)."""
        minutes = self.coordinator.slideshow.get("interval_minutes")
        label = None if minutes is None else _label(int(minutes))
        return label if label in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.set_slideshow(interval_minutes=int(option.split()[0]))
