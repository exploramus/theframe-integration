"""Add-on starts without the TV and answers from the monitor's cached power state."""

import asyncio
import json
import time

from fastapi.testclient import TestClient


def test_status_answers_while_tv_unreachable(addon):
    def unreachable():
        raise OSError("no route to host")

    addon.controller.create_tv = unreachable
    addon.check()

    started = time.monotonic()
    data = addon.client.get("/status").json()
    assert time.monotonic() - started < 1
    assert data["status"] == "success"
    assert data["power_state"] == "off"
    assert data["in_artmode"] is False
    assert "no route to host" in data["error"]


def test_tv_coming_back_resets_art_service_and_runs_pending_sync(addon):
    synced = []

    async def run_pending_sync():
        synced.append(True)

    addon.controller._run_pending_sync = run_pending_sync
    addon.controller._sync_pending = True
    addon.tv.power = "off"
    addon.check()
    assert addon.controller.monitor.state == "off"
    assert synced == []

    addon.tv.power = "art"
    addon.tv.current = addon.tv.add_photo("MY_F1")
    addon.check()
    assert addon.controller.monitor.state == "art"
    assert addon.tv.reset_calls == 1
    assert synced == [True]
    assert addon.controller.monitor.current_content_id == "MY_F1"


def test_art_commands_fail_fast_outside_art_mode(addon):
    addon.tv.power = "tv"
    addon.tv.add_photo("MY_F2")
    addon.check()

    data = addon.client.post("/show_image", json={"content_id": "MY_F2"}).json()
    assert data == {"status": "error", "error": "TV is not in art mode"}
    assert addon.tv.selected == []


def test_show_image_in_art_mode_updates_current_artwork(addon):
    addon.tv.add_photo("MY_F2")
    addon.check()

    data = addon.client.post("/show_image", json={"content_id": "MY_F2"}).json()
    assert data == {"status": "success", "shown": "MY_F2"}
    assert addon.tv.selected == ["MY_F2"]
    assert addon.client.get("/status").json()["current_content_id"] == "MY_F2"


def test_missing_tv_ip_is_reported_instead_of_exiting(theframe, tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"token_file": str(tmp_path / "token.txt")}))
    controller = theframe.TheFrameController(options_file=str(path))

    with TestClient(controller.app) as client:     # runs the lifespan; no monitor is started
        data = client.get("/status").json()
    assert data["status"] == "error"
    assert "tv_ip" in data["error"]


def test_power_on_needs_ip_control_or_mac(addon):
    data = addon.client.post("/power_on").json()
    assert data["status"] == "error"
    assert "tv_mac" in data["error"]


def test_power_check_that_never_answers_counts_as_off(theframe, addon, monkeypatch):
    monkeypatch.setattr(theframe, "TV_CHECK_TIMEOUT", 0.1)
    addon.tv.latency = 10
    asyncio.run(addon.controller.monitor.check())
    assert addon.controller.monitor.state == "off"
    assert "did not answer" in addon.controller.monitor.error
