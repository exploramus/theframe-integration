"""Collection data model (design 3.8): stable IDs, kinds, revisions, missing files, the slideshow following
the active collection by file name, and the one-time conversion of version 1 state files."""

import json

from fastapi.testclient import TestClient

from conftest import write_options
from test_addon_api_v1 import synced


def state_file(tmp_path):
    return tmp_path / "state.json"


def test_saving_again_keeps_the_id_and_counts_revisions(addon):
    synced(addon, ("a.jpg", []), ("b.jpg", []))
    first = addon.client.put("/api/v1/collections/Trip", json={"files": ["a.jpg"]}).json()
    second = addon.client.put("/api/v1/collections/Trip", json={"files": ["b.jpg", "a.jpg"]},
                              headers={"X-Remote-User-Display-Name": "Anna"}).json()

    assert second["id"] == first["id"]
    assert (first["revision"], second["revision"]) == (1, 2)
    assert second["updated_by"] == "Anna"
    assert second["updated"] >= first["updated"]

    switched = addon.client.put("/api/v1/collections/Trip", json={"keywords": ["Sea"]}).json()
    assert (switched["id"], switched["kind"], switched["revision"]) == (first["id"], "keywords", 3)
    assert "files" not in switched and "missing" not in switched


def test_missing_files_are_kept_and_reported(addon):
    synced(addon, ("a.jpg", []), ("b.jpg", []))
    addon.client.put("/api/v1/collections/Trip", json={"files": ["a.jpg", "gone.jpg", "b.jpg"]})
    addon.client.put("/api/v1/slideshow", json={"collection": "Trip"})

    trip = addon.client.get("/api/v1/collections").json()["collections"][0]
    assert (trip["files"], trip["missing"], trip["count"]) == (["a.jpg", "gone.jpg", "b.jpg"], ["gone.jpg"], 2)
    shown = [addon.client.post("/api/v1/slideshow/next").json()["file_name"] for _ in range(3)]
    assert shown == ["a.jpg", "b.jpg", "a.jpg"]     # the slideshow skips the missing file


def test_editing_the_active_collection_keeps_the_current_image(addon):
    synced(addon, ("a.jpg", []), ("b.jpg", []), ("c.jpg", []))
    addon.client.put("/api/v1/collections/Trip", json={"files": ["a.jpg", "b.jpg", "c.jpg"]})
    addon.client.put("/api/v1/slideshow", json={"collection": "Trip"})
    addon.client.post("/api/v1/slideshow/next")
    assert addon.client.post("/api/v1/slideshow/next").json()["file_name"] == "b.jpg"

    # reordered while it plays: continue after b.jpg in the new order
    addon.client.put("/api/v1/collections/Trip", json={"files": ["c.jpg", "b.jpg", "a.jpg"]})
    slideshow = addon.client.get("/api/v1/slideshow").json()
    assert (slideshow["collection"], slideshow["current_file"], slideshow["position"]) == ("Trip", "b.jpg", 1)
    assert addon.client.post("/api/v1/slideshow/next").json()["file_name"] == "a.jpg"


def test_removed_current_image_continues_with_its_follower(addon):
    synced(addon, ("a.jpg", []), ("b.jpg", []), ("c.jpg", []), ("d.jpg", []))
    addon.client.put("/api/v1/collections/Trip", json={"files": ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]})
    addon.client.put("/api/v1/slideshow", json={"collection": "Trip"})
    for _ in range(2):
        addon.client.post("/api/v1/slideshow/next")         # a.jpg, b.jpg

    addon.client.put("/api/v1/collections/Trip", json={"files": ["a.jpg", "c.jpg", "d.jpg"]})
    assert addon.client.post("/api/v1/slideshow/next").json()["file_name"] == "c.jpg"

    addon.client.put("/api/v1/collections/Trip", json={"files": ["a.jpg", "d.jpg"]})
    assert addon.client.post("/api/v1/slideshow/previous").json()["file_name"] == "a.jpg"


def test_a_rename_by_id_does_not_change_the_active_collection(addon):
    synced(addon, ("a.jpg", ["Sea"]))
    addon.client.put("/api/v1/collections/Sea", json={"keywords": ["Sea"]})
    addon.client.put("/api/v1/slideshow", json={"collection": "Sea"})
    collection_id = addon.client.get("/api/v1/slideshow").json()["collection_id"]

    addon.controller.slideshow.collections[collection_id]["name"] = "Ocean"   # renames arrive with step 6.2
    slideshow = addon.client.get("/api/v1/slideshow").json()
    assert (slideshow["collection"], slideshow["collection_id"], slideshow["count"]) == ("Ocean", collection_id, 1)
    assert addon.client.get("/api/v1/status").json()["collections"] == ["Ocean"]


def test_version_1_state_is_converted_once_and_kept_as_backup(theframe, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (tmp_path / "uploaded_files.json").write_text(json.dumps({"uploaded_files": {
        "a.jpg": {"content_id": "MY_F1", "keywords": "[]"},
        "b.jpg": {"content_id": "MY_F2", "keywords": "[\"Sea\"]"},
    }}))
    old = {
        "slideshow": {"enabled": True, "interval_minutes": 5, "mode": "files", "keywords": [],
                      "files": ["b.jpg", "a.jpg"], "position": 1, "collection": "Ameland"},
        "collections": {"Ameland": {"mode": "files", "files": ["b.jpg", "a.jpg"]},
                        "Sea": {"mode": "keywords", "keywords": ["Sea"]}},
    }
    state_file(tmp_path).write_text(json.dumps(old))

    controller = theframe.TheFrameController(options_file=str(write_options(tmp_path)))
    client = TestClient(controller.app)

    slideshow = client.get("/api/v1/slideshow").json()
    assert (slideshow["collection"], slideshow["current_file"], slideshow["position"], slideshow["enabled"]) == \
        ("Ameland", "a.jpg", 1, True)
    collections = {c["name"]: c for c in client.get("/api/v1/collections").json()["collections"]}
    assert (collections["Ameland"]["kind"], collections["Ameland"]["files"]) == ("manual", ["b.jpg", "a.jpg"])
    assert (collections["Sea"]["kind"], collections["Sea"]["keywords"], collections["Sea"]["revision"]) == \
        ("keywords", ["Sea"], 1)
    assert json.loads((tmp_path / "state.json.v1").read_text()) == old
    saved = json.loads(state_file(tmp_path).read_text())
    assert saved["version"] == 2
    assert saved["slideshow"]["collection_id"] == collections["Ameland"]["id"]

    # a second start reads version 2 and keeps the ids
    again = TestClient(theframe.TheFrameController(options_file=str(write_options(tmp_path))).app)
    assert {c["name"]: c["id"] for c in again.get("/api/v1/collections").json()["collections"]} == \
        {name: c["id"] for name, c in collections.items()}
