"""/api/v1: sync jobs, error codes, persisted slideshow and its timer, event stream."""

import asyncio
import json
import time

from fastapi.testclient import TestClient

from conftest import write_options
from test_addon_scenarios import write_image


def listen(controller):
    """collect events published by the add-on"""
    queue = asyncio.Queue()
    controller.events.queues.add(queue)

    def events():
        out = []
        while not queue.empty():
            out.append(queue.get_nowait())
        return out

    return events


def test_sync_job_waits_for_art_mode_and_reports_progress(addon):
    write_image(addon.images, "a.jpg")
    write_image(addon.images, "b.jpg")
    addon.tv.power = "off"
    addon.check()
    events = listen(addon.controller)

    response = addon.client.post("/api/v1/sync")
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["state"] == "queued"

    conflict = addon.client.post("/api/v1/sync")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "job_conflict"
    assert conflict.json()["job"]["id"] == job["id"]

    addon.tv.power = "art"
    addon.check()
    status = addon.client.get("/api/v1/status").json()
    assert status["job"]["id"] == job["id"]
    assert (status["job"]["state"], status["job"]["total"], status["job"]["done"]) == ("done", 2, 2)
    assert status["sync_pending"] is False

    published = events()
    job_states = [data["job"]["state"] for event, data in published if event == "status" and data["job"]]
    assert job_states[0] == "queued" and "running" in job_states and job_states[-1] == "done"
    assert ("files", {"file_count": 2}) in published


def test_new_sync_job_after_the_last_one_finished(addon):
    addon.check()
    first = addon.client.post("/api/v1/sync").json()["job"]
    addon.check()
    second = addon.client.post("/api/v1/sync")
    assert second.status_code == 202
    assert second.json()["job"]["id"] != first["id"]


def test_error_codes(addon, theframe, tmp_path):
    addon.tv.power = "standby"
    addon.check()
    response = addon.client.post("/api/v1/show", json={"file_name": "a.jpg"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tv_not_in_art_mode"
    assert addon.client.get("/api/v1/settings").json()["error"]["code"] == "tv_not_on"

    addon.tv.power = "art"
    addon.check()
    response = addon.client.post("/api/v1/show", json={"file_name": "missing.jpg"})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")
    response = addon.client.put("/api/v1/slideshow", json={"interval_minutes": 0})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_request")
    response = addon.client.post("/api/v1/power_on")
    assert (response.status_code, response.json()["error"]["code"]) == (409, "wake_unavailable")

    path = tmp_path / "broken.json"
    path.write_text(json.dumps({}))
    broken = theframe.TheFrameController(options_file=str(path))
    response = TestClient(broken.app).get("/api/v1/status")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "not_configured")


def test_show_file_by_name(addon):
    write_image(addon.images, "a.jpg")
    addon.check()
    addon.client.post("/api/v1/sync")
    addon.check()

    data = addon.client.post("/api/v1/show", json={"file_name": "a.jpg"}).json()
    assert data["file_name"] == "a.jpg"
    assert addon.tv.current == data["shown"]
    assert addon.client.get("/api/v1/status").json()["current_file"] == "a.jpg"


def synced(addon, *files):
    for name, keywords in files:
        write_image(addon.images, name, keywords)
    addon.check()
    addon.client.post("/api/v1/sync")
    addon.check()


def test_keyword_slideshow_steps_through_matching_files(addon):
    synced(addon, ("a.jpg", ["Sea"]), ("b.jpg", ["Forest"]), ("c.jpg", ["Sea", "Sky"]))
    slideshow = addon.client.put("/api/v1/slideshow", json={"mode": "keywords", "keywords": ["Sea"]}).json()
    assert slideshow["count"] == 2

    shown = [addon.client.post("/api/v1/slideshow/next").json()["file_name"] for _ in range(3)]
    assert shown[0] == shown[2] != shown[1]
    assert set(shown) == {"a.jpg", "c.jpg"}
    assert addon.client.post("/api/v1/slideshow/previous").json()["file_name"] == shown[1]


def test_empty_slideshow(addon):
    addon.check()
    response = addon.client.post("/api/v1/slideshow/next")
    assert (response.status_code, response.json()["error"]["code"]) == (409, "empty_slideshow")


def test_slideshow_settings_survive_a_restart(addon, theframe, tmp_path):
    synced(addon, ("a.jpg", []), ("b.jpg", []))
    addon.client.put("/api/v1/slideshow", json={
        "enabled": True, "interval_minutes": 7, "mode": "files", "files": ["b.jpg", "a.jpg", "b.jpg"]})
    addon.client.post("/api/v1/slideshow/show", json={"position": 1})

    restarted = theframe.TheFrameController(options_file=str(write_options(tmp_path)))
    slideshow = TestClient(restarted.app).get("/api/v1/slideshow").json()
    assert slideshow == {"enabled": True, "interval_minutes": 7, "mode": "files",
                         "keywords": [], "files": ["b.jpg", "a.jpg"], "position": 1, "count": 2}


def test_slideshow_timer_advances_and_pauses(addon):
    synced(addon, ("a.jpg", []), ("b.jpg", []))
    addon.client.put("/api/v1/slideshow", json={"enabled": True, "interval_minutes": 1, "mode": "files",
                                                "files": ["a.jpg", "b.jpg"]})
    controller = addon.controller

    def tick(seconds_since_last_advance):
        controller._last_advance = time.monotonic() - seconds_since_last_advance
        before = len(addon.tv.selected)
        asyncio.run(controller.slideshow_tick())
        return len(addon.tv.selected) > before

    assert not tick(30)                     # interval not up yet
    assert tick(61)
    assert controller.slideshow.position == 0

    controller.job.state = "running"        # paused while a sync runs
    assert not tick(61)
    controller.job.state = "done"

    addon.tv.power = "tv"                   # paused outside art mode
    addon.check()
    assert not tick(61)

    addon.tv.power = "art"
    addon.check()
    controller.update_slideshow(enabled=False)
    assert not tick(61)


def test_event_stream_starts_with_the_status(addon):
    addon.check()
    with addon.client.stream("GET", "/api/v1/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        assert next(lines) == "event: status"
        data = json.loads(next(lines).removeprefix("data: "))
    assert data["power_state"] == "art"
