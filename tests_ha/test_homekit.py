"""Option reload_homekit: bridges that show the collection select reload after collection changes."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from conftest import BASE, STATUS
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.theframe.homekit import bridge_exposes

SELECT = "select.the_frame_collection"


@pytest.fixture
def bridges(hass):
    """fake HomeKit entries; marked not loaded at the end so teardown does not unload the real component"""
    made = []

    def bridge(title, filter_=None, state=ConfigEntryState.LOADED):
        entry = MockConfigEntry(domain="homekit", title=title, state=state,
                                options={"filter": filter_} if filter_ is not None else {})
        entry.add_to_hass(hass)
        made.append(entry)
        return entry

    yield bridge
    for entry in made:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def settle(hass):
    for _ in range(10):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


async def later(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def setup_with_events(hass, options):
    """set up the integration with an event stream the test feeds through the returned queue"""
    queue = asyncio.Queue()

    async def events(self):
        while True:
            yield "status", await queue.get()

    patcher = patch("custom_components.theframe.api.TheFrameApi.events", events)
    patcher.start()
    entry = MockConfigEntry(domain="theframe", data={"base_url": BASE}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry, queue, patcher


def test_bridge_filter():
    assert bridge_exposes({}, SELECT)                                              # empty filter: all
    assert bridge_exposes({"filter": {"include_entities": [SELECT]}}, SELECT)
    assert bridge_exposes({"filter": {"include_domains": ["select"]}}, SELECT)
    assert not bridge_exposes({"filter": {"include_domains": ["light"]}}, SELECT)
    assert not bridge_exposes({"filter": {"exclude_entities": [SELECT]}}, SELECT)
    assert not bridge_exposes({"filter": {"include_entities": "not a list"}}, SELECT)


async def test_bridges_showing_the_select_reload_once_after_the_last_change(hass, addon, bridges, freezer):
    shows = bridges("Frame bridge", {"include_entities": [SELECT]})
    everything = bridges("All", {})
    lights = bridges("Lights", {"include_domains": ["light"]})
    bridges("Stopped", {}, state=ConfigEntryState.NOT_LOADED)

    entry, queue, patcher = await setup_with_events(hass, {"reload_homekit": True})
    try:
        with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as reload:
            queue.put_nowait(dict(STATUS, current_file="b.jpg"))           # no collection change
            await settle(hass)
            await later(hass, freezer, 40)
            assert reload.await_count == 0

            queue.put_nowait(dict(STATUS, collections=["Ameland", "Sea and Sky", "Winter"]))
            await settle(hass)
            await later(hass, freezer, 20)
            queue.put_nowait(dict(STATUS, collections=["Ameland", "Sea and Sky", "Snow"]))   # renamed
            await settle(hass)
            await later(hass, freezer, 20)                                  # 20 s after the rename
            assert reload.await_count == 0

            await later(hass, freezer, 15)
            assert sorted(c.args[0] for c in reload.await_args_list) == sorted(
                [shows.entry_id, everything.entry_id])
            assert lights.entry_id not in [c.args[0] for c in reload.await_args_list]

            reload.reset_mock()
            queue.put_nowait(dict(STATUS, collections=["Ameland", "Sea and Sky", "Snow"]))   # unchanged
            await settle(hass)
            await later(hass, freezer, 60)
            assert reload.await_count == 0
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        patcher.stop()


async def test_no_reload_without_the_option(hass, addon, bridges, freezer):
    bridges("All", {})
    entry, queue, patcher = await setup_with_events(hass, {})
    try:
        with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as reload:
            queue.put_nowait(dict(STATUS, collections=["Winter"]))
            await settle(hass)
            await later(hass, freezer, 60)
            assert reload.await_count == 0
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        patcher.stop()


async def test_unload_drops_a_pending_reload(hass, addon, bridges, freezer):
    bridges("All", {})
    entry, queue, patcher = await setup_with_events(hass, {"reload_homekit": True})
    try:
        queue.put_nowait(dict(STATUS, collections=["Winter"]))
        await settle(hass)
        assert await hass.config_entries.async_unload(entry.entry_id)
        with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as reload:
            await later(hass, freezer, 60)
            assert reload.await_count == 0
    finally:
        patcher.stop()


async def test_options_flow_sets_the_option(hass, setup_entry):
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"ignore_keywords": "TheFrame, exportiert", "reload_homekit": True})
    assert result["type"] == "create_entry"
    assert setup_entry.options == {"ignore_keywords": ["TheFrame", "exportiert"], "reload_homekit": True}
