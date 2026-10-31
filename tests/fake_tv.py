"""Fake Samsung Frame TV for the add-on tests.

Stands in for samsungframetv's SamsungTVAsyncArt: only the calls the add-on makes, with the
response shapes our 2022 Frame returned in its diagnose run (fixtures/frame_2022.json).
Scenario knobs: power state, latency, a connection drop mid-upload, leaving art mode during a
sync, and uploads the TV adds without confirming.
"""

import asyncio
import copy
import json
from pathlib import Path

from samsungframetv import exceptions

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "frame_2022.json").read_text())
ART_ON = ("tv", "art")      # the art channel answers in these power states


class FakeFrameTV:
    def __init__(self, power="art"):
        self.power = power              # off, standby, tv, art
        self.latency = 0                # seconds before each answer
        self.photos = {}                # content_id -> available() item, category MY-C0002
        self.current = None
        self.art_service_available = True
        self.reset_calls = 0
        self.uploaded = []              # file data in upload order
        self.selected = []
        self.drop_connection_on_upload = None   # raise ConnectionFailure on this upload (1-based)
        self.leave_art_after_upload = None      # switch to 'tv' after this upload (1-based)
        self.unconfirmed_uploads = False        # add the image but return None, like a missed image_added
        self._next_id = 9000

    @property
    def art_mode(self):
        return self.power == "art"

    def add_photo(self, content_id=None):
        if content_id is None:
            content_id = "MY_F{}".format(self._next_id)
            self._next_id += 1
        item = copy.deepcopy(FIXTURE["available_item"])
        item.update(content_id=content_id, category_id="MY-C0002", content_type="myphoto")
        self.photos[content_id] = item
        return content_id

    async def _answer(self):
        if self.latency:
            await asyncio.sleep(self.latency)

    async def _art_request(self):
        await self._answer()
        if self.power not in ART_ON:
            raise exceptions.ArtRequestTimeoutError("art channel did not answer, TV is {}".format(self.power))

    async def get_power_state(self, ip_control=None):
        await self._answer()
        return self.power

    def reset_art_service(self):
        self.reset_calls += 1

    async def get_api_version(self):
        await self._art_request()
        return FIXTURE["art_api_version"]

    async def get_current(self):
        await self._art_request()
        return dict(FIXTURE["get_current"], content_id=self.current)

    async def select_image(self, content_id, category=None, show=True):
        await self._art_request()
        if content_id not in self.photos:
            raise exceptions.ResponseError("unknown content_id {}".format(content_id))
        self.current = content_id
        self.selected.append(content_id)

    async def available(self, category=None, timeout=10):
        await self._art_request()
        return [copy.deepcopy(v) for v in self.photos.values() if category in (None, v["category_id"])]

    async def upload(self, file, matte="shadowbox_polar", portrait_matte="shadowbox_polar", file_type="png", date=None, timeout=30):
        await self._art_request()
        count = len(self.uploaded) + 1
        if count == self.drop_connection_on_upload:
            raise exceptions.ConnectionFailure("connection dropped during upload")
        self.uploaded.append(file)
        content_id = self.add_photo()
        if count == self.leave_art_after_upload:
            self.power = "tv"
        return None if self.unconfirmed_uploads else content_id

    async def delete_list(self, content_ids):
        await self._art_request()
        for content_id in content_ids:
            self.photos.pop(content_id, None)
        return True

    async def get_artmode_settings(self, setting=""):
        await self._art_request()
        return copy.deepcopy(FIXTURE["get_artmode_settings"])

    async def close(self):
        pass
