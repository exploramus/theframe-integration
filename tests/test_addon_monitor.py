"""Add-on starts without the TV and answers from the monitor's cached power state."""

import asyncio
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def theframe(tmp_path_factory):
    # the module creates a controller at import, so give it options first
    options = tmp_path_factory.mktemp("import") / "options.json"
    options.write_text(json.dumps({"tv_ip": "192.0.2.1"}))
    os.environ["THEFRAME_OPTIONS"] = str(options)
    spec = importlib.util.spec_from_file_location("theframe", ROOT / "theframe_controller" / "app" / "theframe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTV:
    def __init__(self, *states):
        self.states = list(states)
        self.reset_calls = 0
        self.selected = []
        self.art_service_available = True

    async def get_power_state(self, ip_control=None):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def reset_art_service(self):
        self.reset_calls += 1

    async def get_current(self):
        return {"content_id": "MY_F1"}

    async def select_image(self, content_id, category=None, show=True):
        self.selected.append(content_id)

    async def close(self):
        pass


def make_controller(theframe, tmp_path, **options):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({
        "tv_ip": "192.0.2.1",
        "token_file": str(tmp_path / "token.txt"),
        "json_path": str(tmp_path / "uploaded_files.json"),
        "image_path": str(tmp_path),
        **options,
    }))
    return theframe.TheFrameController(options_file=str(path))


def test_status_answers_while_tv_unreachable(theframe, tmp_path):
    controller = make_controller(theframe, tmp_path)

    def unreachable():
        raise OSError("no route to host")

    controller.create_tv = unreachable
    asyncio.run(controller.monitor.check())

    started = time.monotonic()
    data = TestClient(controller.app).get("/status").json()
    assert time.monotonic() - started < 1
    assert data["status"] == "success"
    assert data["power_state"] == "off"
    assert data["in_artmode"] is False
    assert "no route to host" in data["error"]


def test_tv_coming_back_resets_art_service_and_runs_pending_sync(theframe, tmp_path):
    controller = make_controller(theframe, tmp_path)
    tv = FakeTV("off", "art")
    controller.create_tv = lambda: tv
    synced = []

    async def run_pending_sync():
        synced.append(True)

    controller._run_pending_sync = run_pending_sync
    controller._sync_pending = True

    async def two_checks():
        await controller.monitor.check()
        assert controller.monitor.state == "off"
        await controller.monitor.check()
        await asyncio.sleep(0)      # let the pending sync task run

    asyncio.run(two_checks())
    assert controller.monitor.state == "art"
    assert tv.reset_calls == 1
    assert synced == [True]
    assert controller.monitor.current_content_id == "MY_F1"


def test_art_commands_fail_fast_outside_art_mode(theframe, tmp_path):
    controller = make_controller(theframe, tmp_path)
    tv = FakeTV("tv")
    controller.create_tv = lambda: tv
    asyncio.run(controller.monitor.check())

    data = TestClient(controller.app).post("/show_image", json={"content_id": "MY_F2"}).json()
    assert data == {"status": "error", "error": "TV is not in art mode"}
    assert tv.selected == []


def test_show_image_in_art_mode_updates_current_artwork(theframe, tmp_path):
    controller = make_controller(theframe, tmp_path)
    tv = FakeTV("art")
    controller.create_tv = lambda: tv
    asyncio.run(controller.monitor.check())

    data = TestClient(controller.app).post("/show_image", json={"content_id": "MY_F2"}).json()
    assert data == {"status": "success", "shown": "MY_F2"}
    assert tv.selected == ["MY_F2"]
    assert TestClient(controller.app).get("/status").json()["current_content_id"] == "MY_F2"


def test_missing_tv_ip_is_reported_instead_of_exiting(theframe, tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"token_file": str(tmp_path / "token.txt")}))
    controller = theframe.TheFrameController(options_file=str(path))

    with TestClient(controller.app) as client:     # runs the lifespan; no monitor is started
        data = client.get("/status").json()
    assert data["status"] == "error"
    assert "tv_ip" in data["error"]


def test_power_on_needs_ip_control_or_mac(theframe, tmp_path):
    controller = make_controller(theframe, tmp_path)
    data = TestClient(controller.app).post("/power_on").json()
    assert data["status"] == "error"
    assert "tv_mac" in data["error"]


def test_power_check_that_never_answers_counts_as_off(theframe, tmp_path, monkeypatch):
    monkeypatch.setattr(theframe, "TV_CHECK_TIMEOUT", 0.1)
    controller = make_controller(theframe, tmp_path)

    class HangingTV(FakeTV):
        async def get_power_state(self, ip_control=None):
            await asyncio.sleep(10)

    controller.create_tv = lambda: HangingTV("off")
    asyncio.run(controller.monitor.check())
    assert controller.monitor.state == "off"
    assert "did not answer" in controller.monitor.error
