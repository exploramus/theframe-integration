"""Number platform for The Frame."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MAX_SLIDESHOW_INTERVAL_MIN,
    MIN_SLIDESHOW_INTERVAL_MIN,
)
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([SlideshowIntervalNumber(coordinator)])


class SlideshowIntervalNumber(TheFrameEntity, NumberEntity):
    """Minutes between slideshow advances."""

    _attr_name = "Slideshow Interval"
    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_SLIDESHOW_INTERVAL_MIN
    _attr_native_max_value = MAX_SLIDESHOW_INTERVAL_MIN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_interval"

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.slideshow.get("interval_minutes")
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.set_slideshow(
            interval_minutes=max(
                MIN_SLIDESHOW_INTERVAL_MIN,
                min(MAX_SLIDESHOW_INTERVAL_MIN, int(value)),
            )
        )
