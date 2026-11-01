"""Constants for The Frame integration."""

from __future__ import annotations

DOMAIN = "theframe"

CONF_BASE_URL = "base_url"
DEFAULT_BASE_URL = "http://homeassistant.local:5000"

CONF_IGNORE_KEYWORDS = "ignore_keywords"

UPDATE_INTERVAL_SECONDS = 60

# Slideshow interval bounds for the number entity; the add-on runs the timer.
MIN_SLIDESHOW_INTERVAL_MIN = 1
MAX_SLIDESHOW_INTERVAL_MIN = 30
# Choices for the interval select entity (shown in Apple Home as one switch each)
SLIDESHOW_INTERVAL_CHOICES_MIN = [1, 2, 3, 5, 10, 15, 20, 30]

# Service names exposed by the integration.
SERVICE_SHOW_FILE = "show_file"
SERVICE_SHOW_IMAGE = "show_image"
SERVICE_SYNC_FILES = "sync_files"
SERVICE_NEXT = "next"
SERVICE_PREVIOUS = "previous"
SERVICE_PLAY_FILES = "play_files"
SERVICE_SAVE_COLLECTION = "save_collection"
SERVICE_DELETE_COLLECTION = "delete_collection"

# Coordinator data keys.
DATA_STATUS = "status"
DATA_KEYWORDS = "keywords"
DATA_FILES = "files"

PLATFORMS = [
    "switch",
    "number",
    "select",
    "button",
    "sensor",
    "binary_sensor",
    "image",
    "media_player",
]
