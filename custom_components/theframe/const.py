"""Constants for The Frame integration."""

from __future__ import annotations

DOMAIN = "theframe"

CONF_BASE_URL = "base_url"
DEFAULT_BASE_URL = "http://homeassistant.local:5000"

CONF_IGNORE_KEYWORDS = "ignore_keywords"

UPDATE_INTERVAL_SECONDS = 60

# Slideshow advance is driven from inside the integration so the user no
# longer needs the time_pattern automation from DOCS.md.
DEFAULT_SLIDESHOW_INTERVAL_MIN = 2
MIN_SLIDESHOW_INTERVAL_MIN = 1
MAX_SLIDESHOW_INTERVAL_MIN = 30

# Service names exposed by the integration.
SERVICE_SHOW_FILE = "show_file"
SERVICE_SHOW_IMAGE = "show_image"
SERVICE_SYNC_FILES = "sync_files"
SERVICE_NEXT = "next"
SERVICE_PREVIOUS = "previous"
SERVICE_PLAY_FILES = "play_files"

# Coordinator data keys.
DATA_STATUS = "status"
DATA_KEYWORDS = "keywords"
DATA_FILES = "files"

PLATFORMS = [
    "switch",
    "number",
    "button",
    "sensor",
    "binary_sensor",
    "image",
    "media_player",
]
