"""Image metadata: title, capture time and position read from the files, capture date sent with uploads, place names."""

import asyncio
import io
import json
from fractions import Fraction

from PIL import Image

from test_addon_scenarios import sync, write_image

XMP = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Chilehaus</rdf:li></rdf:Alt></dc:title>'
    '<dc:subject><rdf:Bag><rdf:li>hamburg</rdf:li><rdf:li>TheFrame</rdf:li></rdf:Bag></dc:subject>'
    "</rdf:Description></rdf:RDF></x:xmpmeta>"
)


def write_photo(folder, name, xmp=XMP):
    """a real JPEG with EXIF like an iPhone export from Photos (capture time, offset, GPS, description) plus XMP"""
    exif = Image.Exif()
    exif[0x010E] = "Kontorhausviertel"
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = "2026:08:15 12:16:09"
    exif_ifd[0x9011] = "+02:00"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (Fraction(53), Fraction(32), Fraction(5352, 100))
    gps[3], gps[4] = "E", (Fraction(10), Fraction(0), Fraction(1225, 100))
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (120, 90, 60)).save(buf, format="JPEG", exif=exif)
    path = folder / name
    path.write_bytes(buf.getvalue() + xmp.encode())
    return path


def test_reads_title_capture_time_and_position(theframe, tmp_path):
    meta = theframe.image_meta(str(write_photo(tmp_path, "IMG_7365.jpeg")))

    assert meta == {
        "title": "Chilehaus",
        "description": "Kontorhausviertel",
        "taken": "2026-08-15T12:16:09+02:00",
        "latitude": 53.548200,
        "longitude": 10.003403,
    }
    assert theframe.tv_date(meta) == "2026:08:15 12:16:09"


def test_files_without_metadata_give_nothing(theframe, tmp_path):
    png = tmp_path / "plain.png"
    Image.new("RGB", (8, 8)).save(png)

    assert theframe.image_meta(str(png)) == {}
    assert theframe.image_meta(str(write_image(tmp_path, "fake.jpg", ["x"]))) == {}
    assert theframe.image_meta(str(tmp_path / "missing.jpg")) == {}
    assert theframe.tv_date({}) is None


def test_upload_sends_capture_date_and_files_report_metadata(addon):
    write_photo(addon.images, "IMG_7365.jpeg")
    write_image(addon.images, "plain.jpg")

    assert sync(addon) == "done"

    assert sorted(addon.tv.upload_dates, key=str) == ["2026:08:15 12:16:09", None]
    files = {f["file_name"]: f for f in addon.client.get("/api/v1/files").json()["files"]}
    assert files["IMG_7365.jpeg"]["title"] == "Chilehaus"
    assert files["IMG_7365.jpeg"]["taken"] == "2026-08-15T12:16:09+02:00"
    assert files["IMG_7365.jpeg"]["latitude"] == 53.5482
    assert files["IMG_7365.jpeg"]["place"] is None      # place names are off by default
    assert files["plain.jpg"]["title"] is None
    stored = json.loads(addon.json_path.read_text())["uploaded_files"]
    assert stored["IMG_7365.jpeg"]["meta"]["title"] == "Chilehaus"


def test_fills_metadata_of_files_uploaded_before(addon):
    write_photo(addon.images, "IMG_7365.jpeg")
    addon.controller.tv_files = {"IMG_7365.jpeg": {"content_id": "MY_F0001", "keywords": "[]", "modified": 0},
                                 "gone.jpg": {"content_id": "MY_F0002", "keywords": "[]", "modified": 0}}

    asyncio.run(addon.controller.update_metadata())

    stored = json.loads(addon.json_path.read_text())["uploaded_files"]
    assert stored["IMG_7365.jpeg"]["meta"]["taken"] == "2026-08-15T12:16:09+02:00"
    assert "meta" not in stored["gone.jpg"]         # no file to read, tried again next start
    assert addon.controller.fill_metadata() == 0    # nothing left to fill


def test_place_names_are_looked_up_once_per_position(theframe, addon, monkeypatch):
    calls = []

    async def lookup(client, latitude, longitude):
        calls.append((latitude, longitude))
        return "Hamburg, Deutschland"

    monkeypatch.setattr(addon.controller, "reverse_geocode", lookup)
    monkeypatch.setattr(theframe, "NOMINATIM_DELAY", 0)
    write_photo(addon.images, "a.jpeg")
    write_photo(addon.images, "b.jpeg")
    addon.controller.place_names = True

    assert sync(addon) == "done"
    asyncio.run(addon.controller.resolve_places())

    assert calls == [(53.548, 10.003)]
    files = {f["file_name"]: f for f in addon.client.get("/api/v1/files").json()["files"]}
    assert files["a.jpeg"]["place"] == files["b.jpeg"]["place"] == "Hamburg, Deutschland"


def test_failed_place_lookup_is_retried_later(addon, monkeypatch):
    async def fail(client, latitude, longitude):
        raise OSError("no network")

    monkeypatch.setattr(addon.controller, "reverse_geocode", fail)
    write_photo(addon.images, "a.jpeg")
    addon.controller.place_names = True

    assert sync(addon) == "done"
    asyncio.run(addon.controller.resolve_places())

    files = addon.client.get("/api/v1/files").json()["files"]
    assert files[0]["place"] is None
    assert addon.controller._places == {}
