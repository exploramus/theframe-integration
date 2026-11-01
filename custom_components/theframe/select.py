"""Select platform for The Frame.

The slideshow interval as a choice of fixed values. Apple Home has no number
controls, but the HomeKit bridge exposes a select as one switch per option,
so this replaces an input_select helper plus sync automations.
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
    async_add_entities([SlideshowIntervalSelect(coordinator)])


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
