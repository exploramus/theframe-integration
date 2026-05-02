"""Sensor platform for The Frame."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_FILES, DATA_STATUS, DOMAIN
from .coordinator import TheFrameCoordinator
from .entity import TheFrameEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TheFrameCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            CurrentImageSensor(coordinator),
            ImageCountSensor(coordinator),
            SlideshowIndexSensor(coordinator),
        ]
    )


class CurrentImageSensor(TheFrameEntity, SensorEntity):
    """Content ID of the image currently shown on the TV."""

    _attr_name = "Current Image"
    _attr_icon = "mdi:image"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_current_image"

    @property
    def native_value(self) -> str | None:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        return status.get("current_content_id")

    @property
    def extra_state_attributes(self) -> dict:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        files = (self.coordinator.data or {}).get(DATA_FILES) or []
        cid = status.get("current_content_id")
        if not cid:
            return {}
        match = next((f for f in files if f.get("content_id") == cid), None)
        if not match:
            return {"content_id": cid}
        return {
            "content_id": cid,
            "file_name": match.get("file_name"),
            "keywords": match.get("keywords", []),
        }


class ImageCountSensor(TheFrameEntity, SensorEntity):
    """Number of images known to the add-on."""

    _attr_name = "Image Count"
    _attr_icon = "mdi:image-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "images"

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_image_count"

    @property
    def native_value(self) -> int | None:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        return status.get("file_count")


class SlideshowIndexSensor(TheFrameEntity, SensorEntity):
    """Current position inside the running slideshow."""

    _attr_name = "Slideshow Position"
    _attr_icon = "mdi:counter"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TheFrameCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_slideshow_index"

    @property
    def native_value(self) -> int | None:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        return status.get("slideshow_index")

    @property
    def extra_state_attributes(self) -> dict:
        status = (self.coordinator.data or {}).get(DATA_STATUS) or {}
        return {"slideshow_count": status.get("slideshow_count")}
