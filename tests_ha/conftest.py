"""Home Assistant tests for the integration (pytest-homeassistant-custom-component, Python 3.13)."""

import copy

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"

# The hass fixture puts the harness's own testing config first on sys.path, whose
# custom_components package would shadow ours; importing ours first pins it.
import custom_components.theframe  # noqa: E402,F401

BASE = "http://addon.test:5000"

STATUS = {
    "in_artmode": True,
    "current_content_id": "MY_F1",
    "current_file": "a.jpg",
    "sync_pending": True,
    "file_count": 2,
    "slideshow_index": 1,
    "slideshow_count": 1,
    "slideshow": {"enabled": True, "interval_minutes": 7, "mode": "keywords",
                  "keywords": ["Sea"], "files": [], "position": 0, "collection": None, "count": 1},
    "collections": ["Ameland", "Sea and Sky"],
    "job": {"id": "abc", "kind": "sync", "state": "queued", "total": 0, "done": 0,
            "attempts": 0, "error": None, "created": 0, "started": None, "finished": None},
    "power_state": "art",
    "error": None,
}
FILES = [
    {"file_name": "a.jpg", "content_id": "MY_F1", "keywords": ["Sea"], "modified": 1},
    {"file_name": "b.jpg", "content_id": "MY_F2", "keywords": ["Forest"], "modified": 1},
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def addon(aioclient_mock):
    """the add-on's API as mocked HTTP responses; the event stream ends at once"""
    status = copy.deepcopy(STATUS)
    aioclient_mock.get(f"{BASE}/api/v1/status", json=status)
    aioclient_mock.get(f"{BASE}/api/v1/keywords", json={"keywords": ["Forest", "Sea"]})
    aioclient_mock.get(f"{BASE}/api/v1/files", json={"files": FILES})
    aioclient_mock.get(f"{BASE}/api/v1/events", text="")
    return aioclient_mock


@pytest.fixture
async def setup_entry(hass, addon):
    entry = MockConfigEntry(domain="theframe", data={"base_url": BASE})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
