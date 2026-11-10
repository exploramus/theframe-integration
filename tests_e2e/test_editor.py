"""End-to-end tests of the collections editor in a real browser (Playwright), against the add-on served over
HTTP with the fake TV. Run with `pytest tests_e2e`; skipped where Playwright is not installed."""

import io
import json
import threading
import time

import httpx
import pytest
import uvicorn
from PIL import Image

pytest.importorskip("playwright")
from playwright.sync_api import expect  # noqa: E402

PHOTOS = [
    # file name, title, keywords, capture time, place
    ("sea_1.jpg", "Harbour at dawn", ["Sea"], "2026:07:01 06:10:00", "Hamburg, Deutschland"),
    ("sea_2.jpg", "Dunes", ["Sea", "Sky"], "2026:07:02 12:00:00", "Ameland, Nederland"),
    ("forest.jpg", "Old beech", ["Forest"], "2026:05:20 15:30:00", "Hamburg, Deutschland"),
    ("city.jpg", "Chilehaus", ["Architecture"], "2026:08:15 12:16:09", "Hamburg, Deutschland"),
]


def write_photo(folder, name, title, keywords, taken, colour):
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = taken
    items = "".join("<rdf:li>{}</rdf:li>".format(k) for k in keywords)
    xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{}</rdf:li></rdf:Alt></dc:title>'
           '<dc:subject><rdf:Bag>{}</rdf:Bag></dc:subject></rdf:Description></rdf:RDF></x:xmpmeta>').format(title, items)
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), colour).save(buf, format="JPEG", exif=exif)
    (folder / name).write_bytes(buf.getvalue() + xmp.encode())


@pytest.fixture
def served(addon):
    """the add-on with four synced photos on a free local port"""
    controller = addon.controller
    controller.monitor.interval = controller.monitor.interval_events = 0.05
    controller.monitor.interval_max = 0.2
    for i, (name, title, keywords, taken, _) in enumerate(PHOTOS):
        write_photo(addon.images, name, title, keywords, taken, (60 * i, 120, 200 - 40 * i))
    server = uvicorn.Server(uvicorn.Config(controller.app, host="127.0.0.1", port=0, log_level="warning",
                                           timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "add-on did not start"
        time.sleep(0.02)
    base = "http://127.0.0.1:{}".format(server.servers[0].sockets[0].getsockname()[1])
    http = httpx.Client(base_url=base, timeout=10)
    assert http.post("/api/v1/sync").status_code == 202
    while http.get("/api/v1/status").json()["job"]["state"] != "done":
        assert time.monotonic() < deadline + 10, "sync did not finish"
        time.sleep(0.05)
    for name, *_, place in PHOTOS:             # place names come from OpenStreetMap; set them directly
        controller.tv_files[name]["meta"]["place"] = place
    controller.write_program_data()
    yield base, http
    server.should_exit = True
    thread.join(5)


def api_collections(http):
    return {c["name"]: c for c in http.get("/api/v1/collections").json()["collections"]}


def open_editor(page, base):
    page.goto(base + "/editor/")
    expect(page.locator("#grid .tile")).to_have_count(len(PHOTOS))
    expect(page.locator("#sync-state")).to_have_text("All changes saved")


def new_collection(page, name, kind="manual"):
    page.get_by_role("button", name="New collection").click()
    page.locator("#dialog input[name=name]").fill(name)
    page.locator("#dialog input[value={}]".format(kind)).check()
    page.get_by_role("button", name="Create").click()
    expect(page.locator("#collection-name")).to_have_text(name)


def tile(page, title):
    return page.locator("#grid .tile", has_text=title)


def filmstrip_files(page):
    return page.locator("#filmstrip li").evaluate_all("items => items.map(i => i.dataset.file)")


def wait_saved(page):
    expect(page.locator("#sync-state")).to_have_text("All changes saved")


def test_root_redirects_to_the_editor(served):
    base, http = served
    response = http.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (307, "editor/")


def test_create_a_collection_add_and_reorder(served, page):
    base, http = served
    open_editor(page, base)
    new_collection(page, "Summer")

    for title in ["Dunes", "Chilehaus", "Harbour at dawn"]:
        tile(page, title).click()
    wait_saved(page)
    assert filmstrip_files(page) == ["sea_2.jpg", "city.jpg", "sea_1.jpg"]
    assert api_collections(http)["Summer"]["files"] == ["sea_2.jpg", "city.jpg", "sea_1.jpg"]

    # drag the last image to the front
    page.locator("#filmstrip li[data-file='sea_1.jpg'] .handle").drag_to(
        page.locator("#filmstrip li[data-file='sea_2.jpg']"), target_position={"x": 5, "y": 40})
    wait_saved(page)
    expect(page.locator("#filmstrip li").first).to_have_attribute("data-file", "sea_1.jpg")
    assert api_collections(http)["Summer"]["files"] == ["sea_1.jpg", "sea_2.jpg", "city.jpg"]

    # keyboard: Alt+→ moves the focused image one step
    page.locator("#filmstrip li[data-file='sea_1.jpg']").focus()
    page.keyboard.press("Alt+ArrowRight")
    wait_saved(page)
    assert api_collections(http)["Summer"]["files"] == ["sea_2.jpg", "sea_1.jpg", "city.jpg"]

    # clicking a selected tile removes it; Undo brings it back
    tile(page, "Chilehaus").click()
    wait_saved(page)
    assert api_collections(http)["Summer"]["files"] == ["sea_2.jpg", "sea_1.jpg"]
    page.get_by_role("button", name="Undo").click()
    expect(page.locator("#filmstrip li")).to_have_count(3)
    assert api_collections(http)["Summer"]["files"] == ["sea_2.jpg", "sea_1.jpg", "city.jpg"]


def test_sort_once_and_move_selected(served, page):
    base, http = served
    open_editor(page, base)
    new_collection(page, "All")
    page.get_by_role("button", name="Add all results").click()
    wait_saved(page)

    page.locator("#sort").select_option("taken")
    wait_saved(page)
    assert api_collections(http)["All"]["files"] == ["forest.jpg", "sea_1.jpg", "sea_2.jpg", "city.jpg"]

    page.locator("#filmstrip li[data-file='city.jpg']").click()
    page.locator("#filmstrip li[data-file='sea_2.jpg']").click(modifiers=["Shift"])
    page.get_by_role("button", name="Move to start").click()
    wait_saved(page)
    assert api_collections(http)["All"]["files"] == ["sea_2.jpg", "city.jpg", "forest.jpg", "sea_1.jpg"]


def test_search_by_text_keyword_place_and_date(served, page):
    base, http = served
    open_editor(page, base)
    tiles = page.locator("#grid .tile")

    page.locator("#search-text").fill("chile")
    expect(tiles).to_have_count(1)
    page.locator("#search-text").fill("")

    page.locator("#search-keywords .chip", has_text="Sea").click()
    expect(tiles).to_have_count(2)
    page.locator("#search-place").select_option("Ameland, Nederland")
    expect(tiles).to_have_count(1)
    page.locator("#search-place").select_option("")
    page.locator("#search-keywords .chip", has_text="Sea").click()

    page.locator("#search-from").fill("2026-07-01")
    page.locator("#search-to").fill("2026-07-31")
    page.locator("#search-to").dispatch_event("change")
    page.locator("#search-from").dispatch_event("change")
    expect(tiles).to_have_count(2)


def test_keyword_collection(served, page):
    base, http = served
    open_editor(page, base)
    new_collection(page, "Seaside", kind="keywords")
    page.locator("#keyword-editor-chips .chip", has_text="Sea").click()
    wait_saved(page)
    assert api_collections(http)["Seaside"]["keywords"] == ["Sea"]
    expect(page.locator("#grid .tile.in")).to_have_count(2)
    expect(page.locator("#filmstrip-panel")).to_be_hidden()


def test_changes_from_another_user_appear_live(served, browser):
    base, http = served
    anna, ben = browser.new_page(), browser.new_page()
    open_editor(anna, base)
    new_collection(anna, "Shared")
    open_editor(ben, base)
    ben.locator("#collection-list button", has_text="Shared").click()

    tile(anna, "Old beech").click()
    expect(ben.locator("#filmstrip li")).to_have_count(1)
    http.post("/api/v1/collections/changes", json={
        "id": "from-elsewhere", "change": "add", "collection": api_collections(http)["Shared"]["id"],
        "files": ["city.jpg"], "after": None})
    expect(anna.locator("#filmstrip li").first).to_have_attribute("data-file", "city.jpg")
    expect(ben.locator("#filmstrip li").first).to_have_attribute("data-file", "city.jpg")


def test_changes_made_offline_are_sent_on_reconnect(served, page, context):
    base, http = served
    open_editor(page, base)
    new_collection(page, "Offline")

    context.set_offline(True)
    tile(page, "Dunes").click()
    tile(page, "Old beech").click()
    expect(page.locator("#filmstrip li")).to_have_count(2)        # shown at once
    expect(page.locator("#sync-state")).to_contain_text("Offline")
    expect(page.locator("#sync-state")).to_contain_text("2 changes waiting")
    assert api_collections(http)["Offline"]["files"] == []
    assert len(json.loads(page.evaluate("localStorage.getItem('theframe-editor-queue')"))) == 2

    context.set_offline(False)
    wait_saved(page)
    assert api_collections(http)["Offline"]["files"] == ["sea_2.jpg", "forest.jpg"]


def test_delete_and_restore(served, page):
    base, http = served
    open_editor(page, base)
    new_collection(page, "Temporary")
    tile(page, "Dunes").click()
    wait_saved(page)

    page.get_by_role("button", name="Delete").click()
    page.locator("#dialog-ok").click()
    expect(page.locator("#collection-header")).to_be_hidden()
    assert "Temporary" not in api_collections(http)

    page.locator("#deleted summary").click()
    page.locator("#deleted-list li", has_text="Temporary").get_by_role("button", name="Restore").click()
    expect(page.locator("#collection-name")).to_have_text("Temporary")
    assert api_collections(http)["Temporary"]["files"] == ["sea_2.jpg"]


def test_tiles_keep_their_height_when_the_grid_is_too_short(served, page):
    """many images (or a short window) must scroll the grid, not squash its rows"""
    base, _ = served
    page.set_viewport_size({"width": 1000, "height": 280})
    open_editor(page, base)
    heights = page.locator("#grid .tile").evaluate_all("els => els.map(e => e.getBoundingClientRect().height)")
    assert min(heights) > 100, heights
    assert page.locator("#grid").evaluate("g => g.scrollHeight > g.clientHeight")


def test_play_on_tv(served, page):
    base, http = served
    open_editor(page, base)
    new_collection(page, "Harbour")
    tile(page, "Harbour at dawn").click()
    wait_saved(page)
    page.get_by_role("button", name="Play on TV").click()
    expect(page.locator(".toast", has_text="Playing Harbour on the TV")).to_be_visible()
    expect(page.locator(".toast.error")).to_have_count(0)
    expect(page.locator("#collection-list .playing")).to_have_text("Harbour1")
    slideshow = http.get("/api/v1/status").json()["slideshow"]
    assert (slideshow["collection"], slideshow["enabled"]) == ("Harbour", True)

    tile(page, "Dunes").click()                      # still editable while it plays
    wait_saved(page)
    assert api_collections(http)["Harbour"]["files"] == ["sea_1.jpg", "sea_2.jpg"]
    new_collection(page, "Forest")                   # and the list still switches
    page.locator("#collection-list button", has_text="Harbour").click()
    expect(page.locator("#collection-name")).to_have_text("Harbour")
