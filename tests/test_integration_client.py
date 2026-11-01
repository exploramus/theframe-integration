"""The integration's API client against the real add-on app (served over HTTP) and the fake TV."""

import asyncio
import threading
import time

import aiohttp
import pytest
import uvicorn

from test_addon_scenarios import write_image


@pytest.fixture
def served(addon):
    """the add-on app with the fake TV on a free local port, monitor checking every 50 ms"""
    addon.controller.monitor.interval = 0.05
    addon.controller.monitor.interval_max = 0.2
    addon.controller.monitor.interval_events = 0.05
    server = uvicorn.Server(uvicorn.Config(addon.controller.app, host="127.0.0.1", port=0,
                                           log_level="warning", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "add-on did not start"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield addon, "http://127.0.0.1:{}".format(port)
    server.should_exit = True
    thread.join(5)


async def next_status(events, condition):
    async with asyncio.timeout(10):
        async for event, data in events:
            if event == "status" and condition(data):
                return data


def test_client_drives_the_addon_over_http(served, client_api):
    addon, base_url = served
    write_image(addon.images, "a.jpg", ["Sea"])
    write_image(addon.images, "b.jpg", ["Forest"])

    async def scenario():
        async with aiohttp.ClientSession() as session:
            api = client_api.TheFrameApi(session, base_url)
            events = api.events()
            await next_status(events, lambda s: s["power_state"] == "art")

            job = await api.sync()
            done = await next_status(events, lambda s: (s["job"] or {}).get("state") == "done")
            assert done["job"]["id"] == job["id"]
            assert {f["file_name"] for f in await api.files()} == {"a.jpg", "b.jpg"}
            assert await api.keywords() == ["Forest", "Sea"]

            slideshow = await api.slideshow_update(mode="files", files=["b.jpg", "a.jpg"], interval_minutes=5)
            assert (slideshow["count"], slideshow["interval_minutes"]) == (2, 5)
            assert (await api.slideshow_next())["file_name"] == "b.jpg"
            pushed = await next_status(events, lambda s: s["current_file"] == "b.jpg")
            assert pushed["slideshow"]["position"] == 0

            addon.tv.power = "standby"
            await next_status(events, lambda s: s["power_state"] == "standby")
            with pytest.raises(client_api.TheFrameApiError) as error:
                await api.show_file("a.jpg")
            assert (error.value.code, error.value.status) == ("tv_not_in_art_mode", 409)

            async with session.get(api.thumbnail_url("a.jpg")) as response:
                assert response.status == 422           # routed to the add-on; the stand-in image is not a real JPEG
            await events.aclose()

    asyncio.run(scenario())


def test_unreachable_addon_raises_api_error(client_api):
    async def scenario():
        async with aiohttp.ClientSession() as session:
            api = client_api.TheFrameApi(session, "http://127.0.0.1:9")
            with pytest.raises(client_api.TheFrameApiError) as error:
                await api.status()
            assert error.value.code is None

    asyncio.run(scenario())
