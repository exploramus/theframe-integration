"""Collection changes (design 3.8): applied one at a time with change IDs, file-name anchors, conflicts,
history with undo and restore, export and import, events, and recovery after an interrupted write."""

import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from conftest import write_options
from test_addon_api_v1 import synced

ANNA = {"X-Remote-User-Display-Name": "Anna"}
BEN = {"X-Remote-User-Display-Name": "Ben"}


def change(client, headers=None, change_id=None, **fields):
    return client.post("/api/v1/collections/changes", json=dict(fields, id=change_id or uuid.uuid4().hex),
                       headers=headers or {})


def create(client, files, name="Trip", headers=None):
    response = change(client, headers, change="create", name=name, kind="manual", files=files)
    assert response.status_code == 200, response.json()
    return response.json()["collection_id"]


def files_of(client, collection_id):
    return next(c["files"] for c in client.get("/api/v1/collections").json()["collections"] if c["id"] == collection_id)


def test_add_remove_and_move_use_file_name_anchors(addon):
    client = addon.client
    cid = create(client, ["a.jpg", "b.jpg"])

    change(client, change="add", collection=cid, files=["c.jpg", "a.jpg"])                 # end; a.jpg skipped
    change(client, change="add", collection=cid, files=["d.jpg"], after="a.jpg")
    change(client, change="add", collection=cid, files=["e.jpg"], after=None)               # start
    change(client, change="add", collection=cid, files=["f.jpg"], after="nowhere.jpg")      # anchor gone: end
    assert files_of(client, cid) == ["e.jpg", "a.jpg", "d.jpg", "b.jpg", "c.jpg", "f.jpg"]

    change(client, change="move", collection=cid, files=["c.jpg", "e.jpg"], after="a.jpg")
    assert files_of(client, cid) == ["a.jpg", "c.jpg", "e.jpg", "d.jpg", "b.jpg", "f.jpg"]
    change(client, change="move", collection=cid, files=["f.jpg"], after=None)
    change(client, change="remove", collection=cid, files=["d.jpg", "x.jpg"])
    result = change(client, change="move", collection=cid, files=["b.jpg"], after="b.jpg").json()  # onto itself: end
    assert result["collection"]["files"] == ["f.jpg", "a.jpg", "c.jpg", "e.jpg", "b.jpg"]
    assert result["revision"] == 9


def test_concurrent_edits_combine(addon):
    client = addon.client
    cid = create(client, ["a.jpg", "b.jpg", "c.jpg"])

    # Anna inserts after b.jpg while Ben moves a.jpg to the end and adds d.jpg: both apply in full
    change(client, BEN, change="move", collection=cid, files=["a.jpg"])
    change(client, ANNA, change="add", collection=cid, files=["x.jpg"], after="b.jpg")
    change(client, BEN, change="add", collection=cid, files=["d.jpg"])
    assert files_of(client, cid) == ["b.jpg", "x.jpg", "c.jpg", "a.jpg", "d.jpg"]


def test_changes_are_applied_one_at_a_time(addon):
    controller = addon.controller
    cid = create(addon.client, [])

    async def add_many():
        await asyncio.gather(*[controller.commit_change(uuid.uuid4().hex, cid, "add",
                                                        [{"change": "add", "files": ["{}.jpg".format(i)]}], "test")
                               for i in range(20)])

    asyncio.run(add_many())
    collection = controller.slideshow.collections[cid]
    assert (len(collection["files"]), collection["revision"]) == (20, 21)
    assert [e["revision"] for e in controller.history.entries[cid]] == list(range(1, 22))


def test_a_retried_change_is_applied_once_also_after_a_restart(addon, theframe, tmp_path):
    client = addon.client
    cid = create(client, ["a.jpg"])
    first = change(client, change_id="retry-1", change="add", collection=cid, files=["b.jpg"]).json()
    change(client, change="remove", collection=cid, files=["b.jpg"])
    again = change(client, change_id="retry-1", change="add", collection=cid, files=["b.jpg"]).json()

    assert (first["duplicate"], again["duplicate"], again["revision"]) == (False, True, first["revision"])
    assert files_of(client, cid) == ["a.jpg"]

    restarted = TestClient(theframe.TheFrameController(options_file=str(write_options(tmp_path))).app)
    assert change(restarted, change_id="retry-1", change="add", collection=cid, files=["b.jpg"]).json()["duplicate"]
    assert files_of(restarted, cid) == ["a.jpg"]


def test_conflicts_are_reported(addon):
    client = addon.client
    cid = create(client, ["a.jpg"])
    create(client, [], name="Other")
    synced(addon, ("s.jpg", ["Sea"]))
    sea = change(client, change="create", name="Sea", kind="keywords", keywords=["Sea"]).json()["collection_id"]

    def error(response):
        return response.status_code, response.json()["error"]["code"]

    assert error(change(client, change="rename", collection=cid, name="Other")) == (409, "name_taken")
    assert error(change(client, change="create", name=" Other ", kind="manual")) == (409, "name_taken")
    assert error(change(client, change="add", collection=sea, files=["a.jpg"])) == (422, "invalid_change")
    assert error(change(client, change="set_keywords", collection=cid, keywords=["Sea"])) == (422, "invalid_change")
    assert error(change(client, change="add", files=["a.jpg"])) == (422, "invalid_change")
    assert error(change(client, change="add", collection="nope", files=["a.jpg"])) == (404, "not_found")
    assert error(change(client, change="rename", collection=cid, name=" ")) == (422, "invalid_change")

    change(client, BEN, change="delete", collection=cid)
    assert error(change(client, ANNA, change="add", collection=cid, files=["b.jpg"])) == (409, "collection_deleted")
    assert change(client, change="set_keywords", collection=sea, keywords=["Sky", "Sea"]).json()["collection"]["keywords"] == \
        ["Sea", "Sky"]


def test_undo_reverts_the_users_own_last_change(addon):
    client = addon.client
    cid = create(client, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"], headers=ANNA)

    change(client, ANNA, change="move", collection=cid, files=["d.jpg", "a.jpg"], after="b.jpg")
    change(client, BEN, change="add", collection=cid, files=["e.jpg"], after="c.jpg")
    assert files_of(client, cid) == ["b.jpg", "d.jpg", "a.jpg", "c.jpg", "e.jpg"]

    undone = client.post("/api/v1/collections/{}/undo".format(cid), headers=ANNA).json()
    # each moved file goes back after its old neighbour; Ben's e.jpg stays
    assert undone["collection"]["files"] == ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"]

    # Anna's next undo reaches back to her create: the collection goes; Ben's undo then meets a deleted collection
    assert client.post("/api/v1/collections/{}/undo".format(cid), headers=ANNA).json()["collection"] is None
    response = client.post("/api/v1/collections/{}/undo".format(cid), headers=BEN)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "collection_deleted")
    restored = client.post("/api/v1/collections/{}/restore".format(cid), json={"revision": 4}, headers=BEN).json()
    assert restored["collection"]["files"] == ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"]
    nothing = client.post("/api/v1/collections/{}/undo".format(cid), headers={"X-Remote-User-Display-Name": "Carl"})
    assert (nothing.status_code, nothing.json()["error"]["code"]) == (409, "nothing_to_undo")


def test_undo_of_remove_and_delete(addon):
    client = addon.client
    cid = create(client, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    addon.client.put("/api/v1/slideshow", json={"collection": "Trip"})

    change(client, change="remove", collection=cid, files=["a.jpg", "c.jpg", "d.jpg"])
    assert client.post("/api/v1/collections/{}/undo".format(cid)).json()["collection"]["files"] == \
        ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]

    change(client, change="delete", collection=cid)
    slideshow = client.get("/api/v1/slideshow").json()
    assert (slideshow["collection"], slideshow["files"]) == (None, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    deleted = client.get("/api/v1/collections/deleted").json()["collections"]
    assert [(c["id"], c["name"]) for c in deleted] == [(cid, "Trip")]

    restored = client.post("/api/v1/collections/{}/undo".format(cid)).json()
    assert (restored["collection_id"], restored["collection"]["name"]) == (cid, "Trip")
    assert client.get("/api/v1/collections/deleted").json()["collections"] == []


def test_restore_a_revision_and_read_the_history(addon):
    client = addon.client
    cid = create(client, ["a.jpg"], headers=ANNA)
    change(client, BEN, change="add", collection=cid, files=["b.jpg"])
    change(client, BEN, change="rename", collection=cid, name="Ameland")

    restored = client.post("/api/v1/collections/{}/restore".format(cid), json={"revision": 1}, headers=ANNA).json()
    assert (restored["collection"]["name"], restored["collection"]["files"], restored["revision"]) == \
        ("Trip", ["a.jpg"], 4)

    entries = client.get("/api/v1/collections/{}/history".format(cid)).json()["entries"]
    assert [(e["revision"], e["change"], e["user"]) for e in entries] == \
        [(4, "restore", "Anna"), (3, "rename", "Ben"), (2, "add", "Ben"), (1, "create", "Anna")]
    assert client.post("/api/v1/collections/{}/undo".format(cid), headers=ANNA).json()["collection"]["name"] == "Ameland"

    missing = client.post("/api/v1/collections/{}/restore".format(cid), json={"revision": 99})
    assert missing.status_code == 404
    assert client.get("/api/v1/collections/nope/history").status_code == 404


def test_restored_name_clash_gets_a_suffix(addon):
    client = addon.client
    cid = create(client, ["a.jpg"])
    change(client, change="delete", collection=cid)
    create(client, ["b.jpg"])                       # a new "Trip" meanwhile

    restored = client.post("/api/v1/collections/{}/undo".format(cid)).json()
    assert restored["collection"]["name"] == "Trip (restored)"


def test_put_and_delete_by_name_are_logged_as_changes(addon):
    client = addon.client
    client.put("/api/v1/collections/Trip", json={"files": ["a.jpg"]}, headers=ANNA)
    saved = client.put("/api/v1/collections/Trip", json={"files": ["b.jpg"]}).json()
    client.delete("/api/v1/collections/Trip")

    entries = client.get("/api/v1/collections/{}/history".format(saved["id"])).json()["entries"]
    assert [(e["change"], e["user"]) for e in entries] == \
        [("delete", "Home Assistant"), ("save", "Home Assistant"), ("create", "Anna")]


def test_export_and_import(addon):
    client = addon.client
    create(client, ["b.jpg", "a.jpg"])
    change(client, change="create", name="Sea", kind="keywords", keywords=["Sea"])
    export = client.get("/api/v1/collections/export").json()
    assert export["collections"] == [{"name": "Sea", "kind": "keywords", "keywords": ["Sea"]},
                                     {"name": "Trip", "kind": "manual", "files": ["b.jpg", "a.jpg"]}]

    imported = client.post("/api/v1/collections/import", json=export, headers=ANNA).json()
    assert imported == {"imported": ["Sea (2)", "Trip (2)"]}
    names = client.get("/api/v1/status").json()["collections"]
    assert names == ["Sea", "Sea (2)", "Trip", "Trip (2)"]


def test_changes_are_announced(addon):
    controller = addon.controller
    queue = asyncio.Queue()
    controller.events.queues.add(queue)
    cid = create(addon.client, ["a.jpg"], headers=ANNA)

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    collection_events = [data for event, data in events if event == "collection"]
    assert [(e["change"], e["collection_id"], e["revision"], e["user"]) for e in collection_events] == \
        [("create", cid, 1, "Anna")]
    assert any(event == "status" and data["collections"] == ["Trip"] for event, data in events)


def test_interrupted_write_is_rolled_forward_from_the_history(addon, theframe, tmp_path):
    client = addon.client
    cid = create(client, ["a.jpg"])
    before = (tmp_path / "state.json").read_text()
    change(client, change="add", collection=cid, files=["b.jpg"])
    deleted = create(client, ["x.jpg"], name="Gone")
    state_before_delete = (tmp_path / "state.json").read_text()
    change(client, change="delete", collection=deleted)

    # the history was written, state.json not (crash between the two writes)
    state = json.loads(before)
    state["collections"].update(json.loads(state_before_delete)["collections"])
    state["collections"][cid] = json.loads(before)["collections"][cid]
    (tmp_path / "state.json").write_text(json.dumps(state))

    restarted = TestClient(theframe.TheFrameController(options_file=str(write_options(tmp_path))).app)
    assert files_of(restarted, cid) == ["a.jpg", "b.jpg"]
    assert restarted.get("/api/v1/status").json()["collections"] == ["Trip"]
