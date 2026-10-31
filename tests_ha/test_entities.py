"""Entities mirror the add-on's state and change it only through /api/v1."""

import asyncio
import logging
from unittest.mock import patch

from conftest import BASE, STATUS


def calls(addon, method, path):
    """JSON bodies of the mocked requests to one endpoint"""
    return [data for m, url, data, _ in addon.mock_calls if m == method and str(url) == BASE + path]


async def test_entities_show_the_addon_state(hass, setup_entry):
    assert hass.states.get("switch.the_frame_slideshow").state == "on"
    assert hass.states.get("number.the_frame_slideshow_interval").state == "7.0"
    assert hass.states.get("switch.the_frame_art_sea").state == "on"
    assert hass.states.get("switch.the_frame_art_forest").state == "off"
    assert hass.states.get("sensor.the_frame_sync").state == "queued"
    assert hass.states.get("binary_sensor.the_frame_in_art_mode").state == "on"


async def test_switches_and_number_set_the_addon_slideshow(hass, setup_entry, addon):
    addon.put(f"{BASE}/api/v1/slideshow", json=dict(STATUS["slideshow"], enabled=False))

    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.the_frame_slideshow"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.the_frame_art_forest"}, blocking=True)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.the_frame_slideshow_interval", "value": 5}, blocking=True)

    assert calls(addon, "PUT", "/api/v1/slideshow") == [
        {"enabled": False},
        {"mode": "keywords", "keywords": ["Forest", "Sea"]},
        {"interval_minutes": 5},
    ]
    # the add-on's answer is mirrored without waiting for the next poll
    assert hass.states.get("switch.the_frame_slideshow").state == "off"


async def test_play_files_sets_a_playlist_and_shows_the_first_image(hass, setup_entry, addon):
    addon.put(f"{BASE}/api/v1/slideshow", json=dict(STATUS["slideshow"], mode="files", files=["b.jpg", "a.jpg"]))
    addon.post(f"{BASE}/api/v1/slideshow/next", json={"shown": "MY_F2", "position": 0, "file_name": "b.jpg"})

    await hass.services.async_call("theframe", "play_files", {"file_names": ["b.jpg", "a.jpg"]}, blocking=True)

    assert calls(addon, "PUT", "/api/v1/slideshow") == [{"mode": "files", "files": ["b.jpg", "a.jpg"], "position": -1}]
    assert len(calls(addon, "POST", "/api/v1/slideshow/next")) == 1
    # keyword switches are off while a playlist plays
    assert hass.states.get("switch.the_frame_art_sea").state == "off"


async def test_sync_conflict_is_not_an_error(hass, setup_entry, addon, caplog):
    addon.post(f"{BASE}/api/v1/sync", status=409,
               json={"error": {"code": "job_conflict", "message": "a sync is already queued"}, "job": STATUS["job"]})
    with caplog.at_level(logging.INFO):
        await hass.services.async_call("button", "press", {"entity_id": "button.the_frame_sync_files"}, blocking=True)
    assert "already queued or running" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_status_events_update_entities(hass, addon):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    pushed = dict(STATUS, current_content_id="MY_F2", current_file="b.jpg",
                  job=dict(STATUS["job"], state="running", total=2, done=1))

    async def events(self):
        yield "status", pushed
        await asyncio.Event().wait()        # stream stays open

    with patch("custom_components.theframe.api.TheFrameApi.events", events):
        entry = MockConfigEntry(domain="theframe", data={"base_url": BASE})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for _ in range(10):
            await asyncio.sleep(0)

        assert hass.states.get("sensor.the_frame_current_image").state == "MY_F2"
        sync = hass.states.get("sensor.the_frame_sync")
        assert (sync.state, sync.attributes["done"], sync.attributes["total"]) == ("running", 1, 2)
        assert await hass.config_entries.async_unload(entry.entry_id)
