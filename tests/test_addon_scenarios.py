"""State-based scenarios from docs/DESIGN.md (section 5), run against the fake TV."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from conftest import write_options


def write_image(folder, name, keywords=()):
    """a stand-in image file; keywords go into an XMP dc:subject block like a Photos export"""
    items = "".join("<rdf:li>{}</rdf:li>".format(k) for k in keywords)
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:subject><rdf:Bag>{}</rdf:Bag>'
        "</dc:subject></rdf:Description></rdf:RDF></x:xmpmeta>"
    ).format(items)
    path = folder / name
    path.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg " + name.encode() + xmp.encode())
    return path


def recorded(addon):
    """file name -> content_id as stored in uploaded_files.json"""
    data = json.loads(addon.json_path.read_text())
    return {k: v["content_id"] for k, v in data["uploaded_files"].items()}


def sync(addon):
    return addon.client.post("/sync_files").json()


def test_starts_with_tv_off_and_answers_immediately(addon):
    addon.tv.power = "off"
    addon.check()

    started = time.monotonic()
    status = addon.client.get("/status").json()
    show = addon.client.post("/show_file", json={"file_name": "a.jpg"}).json()
    settings = addon.client.get("/get_artmode_settings").json()
    assert time.monotonic() - started < 1
    assert status["power_state"] == "off"
    assert status["in_artmode"] is False
    assert show == {"status": "error", "error": "TV is not in art mode"}
    assert settings == {"status": "error", "error": "TV is not on"}


def test_standby_is_reported_and_art_commands_refused(addon):
    addon.tv.power = "standby"
    addon.check()

    assert addon.client.get("/status").json()["power_state"] == "standby"
    assert addon.client.post("/show_slideshow_next").json()["error"] == "TV is not in art mode"


def test_art_settings_work_while_watching_tv(addon):
    addon.tv.power = "tv"
    addon.check()

    data = addon.client.get("/get_artmode_settings").json()
    assert data["status"] == "success"
    assert {s["item"] for s in data["settings"]} >= {"brightness", "motion_timer"}


def test_queued_sync_runs_once_art_mode_appears(addon):
    write_image(addon.images, "a.jpg", ["Architektur"])
    write_image(addon.images, "b.jpg", ["Blüte"])
    addon.tv.power = "off"
    addon.check()

    assert sync(addon)["status"] == "queued"
    assert addon.tv.uploaded == []

    addon.tv.power = "art"
    addon.check()
    assert len(addon.tv.uploaded) == 2
    assert set(recorded(addon)) == {"a.jpg", "b.jpg"}
    assert addon.client.get("/status").json()["sync_pending"] is False
    keywords = addon.client.get("/available_keywords").json()["keywords"]
    assert keywords == ["Architektur", "Blüte"]


def test_slow_tv_does_not_block_requests(theframe, addon, monkeypatch):
    write_image(addon.images, "a.jpg")
    addon.check()
    assert sync(addon)["status"] == "success"

    monkeypatch.setattr(theframe, "TV_CALL_TIMEOUT", 0.2)
    addon.tv.latency = 5
    started = time.monotonic()
    data = addon.client.post("/show_file", json={"file_name": "a.jpg"}).json()
    assert time.monotonic() - started < 2
    assert data == {"status": "error", "error": "Failed to display content ID"}


def test_connection_drop_mid_upload_is_retried(theframe, addon, monkeypatch):
    monkeypatch.setattr(theframe, "SYNC_RETRY_DELAY", 0)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        write_image(addon.images, name)
    addon.check()
    addon.tv.drop_connection_on_upload = 2

    assert sync(addon)["status"] == "queued"
    assert len(recorded(addon)) == 1           # folder order is arbitrary

    addon.tv.drop_connection_on_upload = None
    addon.check()
    assert set(recorded(addon)) == {"a.jpg", "b.jpg", "c.jpg"}
    assert len(addon.tv.photos) == 3           # nothing uploaded twice
    assert addon.controller._sync_pending is False


def test_leaving_art_mode_during_sync_keeps_it_queued(addon):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        write_image(addon.images, name)
    addon.check()
    addon.tv.leave_art_after_upload = 1

    assert sync(addon)["status"] == "queued"
    assert len(recorded(addon)) == 1           # folder order is arbitrary

    addon.check()                               # monitor sees 'tv': still queued
    assert addon.controller._sync_pending is True

    addon.tv.power = "art"
    addon.check()
    assert set(recorded(addon)) == {"a.jpg", "b.jpg", "c.jpg"}
    assert len(addon.tv.photos) == 3


def test_unconfirmed_upload_is_found_on_the_tv_instead_of_uploaded_again(addon):
    addon.tv.add_photo("MY_F1")                 # already on the TV, not ours
    write_image(addon.images, "a.jpg")
    write_image(addon.images, "b.jpg")
    addon.check()
    addon.tv.unconfirmed_uploads = True

    sync(addon)
    ids = recorded(addon)
    assert set(ids) == {"a.jpg", "b.jpg"}
    assert set(ids.values()) == set(addon.tv.photos) - {"MY_F1"}

    sync(addon)
    assert len(addon.tv.uploaded) == 2


def test_sync_deletes_only_what_the_addon_uploaded(addon):
    addon.tv.add_photo("MY_F1")                 # uploaded by someone else
    write_image(addon.images, "a.jpg")
    write_image(addon.images, "b.jpg")
    addon.check()
    sync(addon)

    os.remove(addon.images / "a.jpg")
    sync(addon)
    assert set(recorded(addon)) == {"b.jpg"}
    assert set(addon.tv.photos) == {"MY_F1", recorded(addon)["b.jpg"]}


def test_modified_file_is_reuploaded(addon):
    path = write_image(addon.images, "a.jpg")
    addon.check()
    sync(addon)
    old = recorded(addon)["a.jpg"]

    later = time.time() + 60
    os.utime(path, (later, later))
    sync(addon)
    new = recorded(addon)["a.jpg"]
    assert new != old
    assert set(addon.tv.photos) == {new}


@pytest.mark.xfail(strict=True, reason="step 4: slideshow stores file names and resolves content IDs at display time")
def test_slideshow_survives_a_reupload(addon):
    path = write_image(addon.images, "a.jpg")
    write_image(addon.images, "b.jpg")
    addon.check()
    sync(addon)
    addon.client.post("/initialize_slideshow_files", json={"file_names": ["a.jpg", "b.jpg"]})

    later = time.time() + 60
    os.utime(path, (later, later))
    sync(addon)
    data = addon.client.post("/show_slideshow_index", json={"slideshow_index": 0}).json()
    assert data == {"status": "success", "shown": recorded(addon)["a.jpg"]}


@pytest.mark.xfail(strict=True, reason="step 4: slideshow state is persisted in /data")
def test_slideshow_survives_an_addon_restart(theframe, addon, tmp_path):
    write_image(addon.images, "a.jpg")
    write_image(addon.images, "b.jpg")
    addon.check()
    sync(addon)
    addon.client.post("/initialize_slideshow_files", json={"file_names": ["a.jpg", "b.jpg"], "slideshow_index": 1})

    restarted = theframe.TheFrameController(options_file=str(write_options(tmp_path)))
    status = TestClient(restarted.app).get("/status").json()
    assert (status["slideshow_count"], status["slideshow_index"]) == (2, 1)
