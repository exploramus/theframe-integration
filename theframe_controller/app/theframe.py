# TheFrame Controller for Samsung The Frame TV
#
# This code provides an API to manage and display images on a Samsung The Frame TV.
# It includes functionality to upload images, display slideshows, and manage TV settings.
# It uses FastAPI for the web interface and samsungframetv for communication with the TV.
# It also includes PIL for image processing if available.
# This code is designed to run in a Home Assistant environment, where it can be used as an add-on.

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal, Optional
from contextlib import asynccontextmanager
import contextlib
import copy
import shutil
from pydantic import BaseModel, Field
import asyncio
import os
import json
import logging
import io
import time
import datetime
import re
import uuid
import xml.etree.ElementTree as ET
import httpx
from wakeonlan import send_magic_packet
from iptcinfo3 import IPTCInfo
HAVE_PIL = False
try:
    from PIL import Image, ImageFilter, ImageChops
    HAVE_PIL=True
except ImportError:
    pass


from samsungframetv import ipcontrol
from samsungframetv.async_art import SamsungTVAsyncArt

# Add-on options; set THEFRAME_OPTIONS to run outside Home Assistant (e.g. a dev options file)
OPTIONS_FILE = os.environ.get("THEFRAME_OPTIONS", "/data/options.json")

POLL_INTERVAL = 10          # seconds between power state checks while the TV is on
POLL_INTERVAL_EVENTS = 30   # same, while the TV's event connection is open and pushes changes itself
POLL_INTERVAL_MAX = 30      # back-off limit while the TV does not answer at all
TV_CALL_TIMEOUT = 30        # outer safety net around TV calls, the library has its own timeouts
TV_CHECK_TIMEOUT = 15       # power state check; the library's async REST calls have no timeout of their own
TV_CONNECT_TIMEOUT = 10     # client timeout: sync REST call in the constructor and websocket open
TV_ON_STATES = ('tv', 'art', 'on')
# events the TV pushes on the art channel; recorded on a 2022 Frame (GQ55LS03B)
TV_EVENTS = ('art_mode_changed', 'image_selected', 'go_to_standby', 'wakeup')
SYNC_RETRY_DELAY = 60       # seconds before a failed sync is retried
SLIDESHOW_TICK = 5          # seconds between slideshow timer checks
EVENT_KEEPALIVE = 15        # seconds between keep-alive comments on the event stream
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
HISTORY_KEEP = 100          # changes kept per collection, plus everything younger than HISTORY_MAX_AGE
HISTORY_MAX_AGE = 30 * 86400
META_FIELDS = ('title', 'description', 'taken', 'place', 'latitude', 'longitude')
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/reverse'
NOMINATIM_DELAY = 1.1       # seconds between requests (Nominatim allows one per second)
USER_AGENT = 'theframe-controller (https://github.com/exploramus/theframe-integration)'


def request_user(request):
    '''
    the Home Assistant user behind an Ingress request, else "Home Assistant" (integration actions, direct API calls)
    '''
    return (request.headers.get('X-Remote-User-Display-Name') or request.headers.get('X-Remote-User-Name')
            or 'Home Assistant')


def api_error(status_code, code, message):
    '''
    /api/v1 error response: HTTP status plus a stable error code
    '''
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _rdf_text(root, tag):
    '''
    first text of an XMP property such as dc:title (rdf:Alt / rdf:li) in a parsed XMP block
    '''
    for prop in root.iter('{http://purl.org/dc/elements/1.1/}' + tag):
        for item in prop.iter('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li'):
            if item.text and item.text.strip():
                return item.text.strip()
    return None


def _gps_degrees(value, ref):
    try:
        degrees = float(value[0]) + float(value[1]) / 60 + float(value[2]) / 3600
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None
    return round(-degrees if ref in ('S', 'W') else degrees, 6)


def image_meta(path):
    '''
    title, description, capture time and GPS position of an image file, from XMP and EXIF;
    only the fields found are returned
    '''
    meta = {}
    if HAVE_PIL:
        try:
            with Image.open(path) as img:
                exif = img.getexif()
            exif_ifd, gps = exif.get_ifd(0x8769), exif.get_ifd(0x8825)
            taken = exif_ifd.get(0x9003)                     # DateTimeOriginal
            if taken:
                taken = datetime.datetime.strptime(str(taken).strip(), '%Y:%m:%d %H:%M:%S').isoformat()
                offset = str(exif_ifd.get(0x9011) or '').strip()  # OffsetTimeOriginal
                meta['taken'] = taken + offset if re.fullmatch(r'[+-]\d\d:\d\d', offset) else taken
            if exif.get(0x010E) and str(exif[0x010E]).strip():  # ImageDescription
                meta['description'] = str(exif[0x010E]).strip()
            latitude = _gps_degrees(gps.get(2), gps.get(1))
            longitude = _gps_degrees(gps.get(4), gps.get(3))
            if latitude is not None and longitude is not None:
                meta['latitude'], meta['longitude'] = latitude, longitude
        except Exception:
            pass
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return meta
    for block in re.findall(rb'<x:xmpmeta[\s\S]*?</x:xmpmeta>', data, flags=re.IGNORECASE):
        try:
            root = ET.fromstring(block.decode('utf-8', errors='replace'))
        except ET.ParseError:
            continue
        for key in ('title', 'description'):
            text = _rdf_text(root, key)
            if text:
                meta[key] = text
    return meta


def tv_date(meta):
    '''
    capture time in the format the TV stores with an upload, or None
    '''
    taken = (meta or {}).get('taken')
    return taken[:19].replace('-', ':').replace('T', ' ') if taken else None


class Slideshow:
    '''
    slideshow state owned by the add-on and persisted, so it survives restarts
    stores file names; content_ids are looked up when an image is shown, so a re-upload does not break it
    current_file is the image shown last (None before the first one); position is its index then, kept as a hint
    for where to continue when that file leaves the playlist
    collections are named presets, keyed by a stable id: kind 'manual' (an ordered list of files) or 'keywords';
    collection_id is the active one: the slideshow then plays that collection as it is now, also after edits
    mode, keywords and files are the slideshow's own content, used when no collection is active
    '''

    VERSION = 2
    FIELDS = ('enabled', 'interval_minutes', 'mode', 'keywords', 'files', 'collection_id', 'current_file', 'position')

    def __init__(self, path=None):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.path = path
        self.enabled = False
        self.interval_minutes = 2
        self.mode = 'keywords'      # keywords: files matching any keyword; files: an ordered playlist
        self.keywords = []
        self.files = []
        self.collection_id = None
        self.current_file = None
        self.position = -1
        self.collections = {}       # id -> {'id', 'name', 'kind', 'files' or 'keywords', 'revision', 'updated', 'updated_by'}
        self.legacy_position = None # index from a version 1 state file, resolved to a file name by the controller
        self.load()

    def as_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    def by_name(self, name):
        return next((c for c in self.collections.values() if c['name'] == name), None)

    def load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path) as f:
                state = json.load(f)
        except Exception as e:
            self.log.warning('could not read slideshow state from {}: {}'.format(self.path, e))
            return
        if state.get('version', 1) < 2:
            state = self.migrate(state)
        data = state.get('slideshow', {})
        for k in self.FIELDS:
            if k in data:
                setattr(self, k, data[k])
        self.collections = state.get('collections', {})
        if self.collection_id not in self.collections:
            self.collection_id = None

    def migrate(self, state):
        '''
        version 1 (up to 1.5.0): collections keyed by name with mode files|keywords, the active one by name,
        position as an index; the old file is kept next to the new one as state.json.v1
        '''
        backup = self.path + '.v1'
        if not os.path.exists(backup):
            with contextlib.suppress(OSError):
                shutil.copy2(self.path, backup)
        now = time.time()
        collections = {}
        for name, old in (state.get('collections') or {}).items():
            cid = uuid.uuid4().hex
            collection = {'id': cid, 'name': name, 'revision': 1, 'updated': now, 'updated_by': None}
            if old.get('mode') == 'files':
                collection.update(kind='manual', files=list(old.get('files') or []))
            else:
                collection.update(kind='keywords', keywords=list(old.get('keywords') or []))
            collections[cid] = collection
        data = dict(state.get('slideshow') or {})
        name = data.pop('collection', None)
        data['collection_id'] = next((c['id'] for c in collections.values() if c['name'] == name), None)
        self.legacy_position = data.pop('position', -1)
        self.log.info('slideshow state converted to version 2 ({} collections), old state kept in {}'.format(
            len(collections), backup))
        return {'version': 2, 'slideshow': data, 'collections': collections}

    def save(self):
        if not self.path:
            return
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'version': self.VERSION, 'slideshow': self.as_dict(), 'collections': self.collections}, f)
            os.replace(tmp, self.path)
        except Exception as e:
            self.log.warning('could not save slideshow state to {}: {}'.format(self.path, e))


class CollectionHistory:
    '''
    log of collection changes, per collection id, persisted next to state.json
    each entry: change_id, change, params, user, time, revision, after (the collection after the change, None
    after a delete), inverse (changes that undo it), undone, undo_of
    written before state.json, so after a crash between the two writes the state can be rolled forward from it
    '''

    def __init__(self, path=None):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.path = path
        self.entries = {}       # collection id -> list of entries, oldest first
        self.load()

    def load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path) as f:
                self.entries = json.load(f).get('collections', {})
        except Exception as e:
            self.log.warning('could not read collection history from {}: {}'.format(self.path, e))

    def save(self):
        if not self.path:
            return
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'version': 1, 'collections': self.entries}, f)
        os.replace(tmp, self.path)

    def add(self, collection_id, entry):
        entries = self.entries.setdefault(collection_id, [])
        entries.append(entry)
        cutoff = time.time() - HISTORY_MAX_AGE
        keep_from = max(0, len(entries) - HISTORY_KEEP)
        self.entries[collection_id] = [e for i, e in enumerate(entries) if i >= keep_from or e['time'] >= cutoff]

    def last_revision(self, collection_id):
        entries = self.entries.get(collection_id)
        return entries[-1]['revision'] if entries else 0

    def entry(self, collection_id, revision):
        return next((e for e in self.entries.get(collection_id, []) if e['revision'] == revision), None)

    def last_undoable(self, collection_id, user):
        '''
        the user's latest change to the collection that is not undone and not an undo itself
        '''
        for entry in reversed(self.entries.get(collection_id, [])):
            if entry['user'] == user and not entry.get('undone') and entry.get('undo_of') is None:
                return entry
        return None

    def is_deleted(self, collection_id):
        entries = self.entries.get(collection_id)
        return bool(entries) and entries[-1]['after'] is None

    def change_ids(self):
        return {e['change_id']: (cid, e['revision']) for cid, entries in self.entries.items() for e in entries}


class ChangeError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class SyncJob:
    '''
    one sync of the image folder with the TV; waits as 'queued' until the TV is in art mode
    states: queued, running, done
    '''

    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self.kind = 'sync'
        self.state = 'queued'
        self.created = time.time()
        self.started = None
        self.finished = None
        self.total = 0
        self.done = 0
        self.attempts = 0
        self.error = None
        self.ran = asyncio.Event()      # set each time a run ends

    def as_dict(self):
        return {k: getattr(self, k) for k in ('id', 'kind', 'state', 'created', 'started', 'finished', 'total', 'done', 'attempts', 'error')}


class EventHub:
    '''
    fans server-sent events out to connected clients; a slow client drops events and catches up
    with the next status event
    '''

    def __init__(self):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.queues = set()

    def publish(self, event, data):
        for queue in list(self.queues):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait((event, data))

    @staticmethod
    def format(event, data):
        return 'event: {}\ndata: {}\n\n'.format(event, json.dumps(data))

    async def stream(self, first_event):
        queue = asyncio.Queue(maxsize=100)
        self.queues.add(queue)
        self.log.info('event stream client connected ({} open)'.format(len(self.queues)))
        try:
            yield self.format(*first_event)
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), EVENT_KEEPALIVE)
                    yield self.format(event, data)
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
        finally:
            self.queues.discard(queue)
            self.log.info('event stream client disconnected ({} open)'.format(len(self.queues)))


class TVMonitor:
    '''
    background task that tracks the TV's power state with get_power_state()
    endpoints read the cached state and never wait for the TV
    '''

    def __init__(self, mon, interval=POLL_INTERVAL, interval_max=POLL_INTERVAL_MAX, interval_events=POLL_INTERVAL_EVENTS):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.mon = mon
        self.interval = interval
        self.interval_max = interval_max
        self.interval_events = interval_events
        self.state = 'unknown'      # off, standby, tv, art, on (art mode unknown), unknown before the first check
        self.since = None
        self.last_check = None
        self.error = None
        self.current_content_id = None
        self._wakeup = asyncio.Event()

    @property
    def in_artmode(self):
        return self.state == 'art'

    @property
    def tv_on(self):
        return self.state in TV_ON_STATES

    @property
    def events_connected(self):
        '''
        True while the art channel is open, so the TV pushes art mode, image and standby changes
        '''
        return self.mon.tv is not None and self.mon.tv.is_alive()

    def listen(self, tv):
        for name in TV_EVENTS:
            tv.set_callback(name, self.on_tv_event)

    def on_tv_event(self, event, response):
        '''
        update the cached state the moment the TV reports a change; the checks remain as a safety net
        '''
        try:
            data = json.loads(response.get('data') or '{}')
        except (AttributeError, ValueError):
            return
        name = data.get('event')
        self.log.debug('TV event: {}'.format(data))
        if name == 'art_mode_changed':
            self.set_state('art' if data.get('status') == 'on' else 'tv')
            if not self.in_artmode:
                self.current_content_id = None
            else:
                self.poke()             # the check fetches the current image and starts a queued sync
        elif name == 'image_selected' and self.in_artmode:
            # the TV also announces images while in standby; only art mode shows them
            self.current_content_id = data.get('content_id') or self.current_content_id
        elif name == 'go_to_standby':
            self.set_state('standby')
            self.current_content_id = None
        elif name == 'wakeup':
            self.poke()
        self.mon.publish_status()

    def poke(self):
        '''
        check again now, e.g. after a wake request
        '''
        self._wakeup.set()

    async def run(self):
        delay = self.interval
        while True:
            previous = self.state
            await self.check()
            delay = self.next_delay(previous, delay)
            try:
                await asyncio.wait_for(self._wakeup.wait(), delay)
            except asyncio.TimeoutError:
                pass
            self._wakeup.clear()

    def next_delay(self, previous, delay):
        '''
        seconds until the next check; standby answers quickly and can turn into art mode any moment
        (motion sensor, remote), so only back off while the TV does not answer at all
        '''
        if self.state in ('off', 'unknown') and self.state == previous:
            return min(delay * 2, self.interval_max)
        return self.interval_events if self.events_connected else self.interval

    async def check(self):
        '''
        one power state check; creates the TV client first if needed
        '''
        self.last_check = time.time()
        if self.mon.tv is None:
            try:
                async with asyncio.timeout(TV_CALL_TIMEOUT):
                    self.mon.tv = await asyncio.to_thread(self.mon.create_tv)
                self.listen(self.mon.tv)
            except Exception as e:
                self.error = 'cannot create TV client: {}'.format(e)
                self.log.warning(self.error)
                self.set_state('off')
                return
        try:
            async with asyncio.timeout(TV_CHECK_TIMEOUT):
                state = await self.mon.tv.get_power_state(self.mon.paired_ip_control())
            self.error = None
        except TimeoutError:
            self.error = 'TV did not answer within {}s'.format(TV_CHECK_TIMEOUT)
            state = 'off'
        except Exception as e:
            self.error = 'power state check failed: {}'.format(e)
            self.log.warning(self.error)
            state = 'unknown'
        self.set_state(state)
        if self.in_artmode:
            self.current_content_id = await self.mon.get_current_artwork()
            if self.mon.sync_pending:
                asyncio.create_task(self.mon.run_sync_job())
        else:
            self.current_content_id = None
        self.mon.publish_status()

    def set_state(self, state):
        previous = self.state
        if state == previous:
            return
        self.log.info('TV power state: {} -> {}'.format(previous, state))
        self.state = state
        self.since = time.time()
        if state in TV_ON_STATES and previous not in TV_ON_STATES and self.mon.tv is not None:
            # the TV just came back, forget art requests that timed out while it was off
            self.mon.tv.reset_art_service()



class PIL_methods:
    
    def __init__(self, mon):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.mon = mon
        self.folder = self.mon.folder
        self.uploaded_files = self.mon.tv_files
        
    async def initialize(self):
        '''
        initialize uploaded_files using PIL
        compares the file data with thumbnails to find the content_id and write to uploaded_files
        if it doesn't already exist
        '''
        if not HAVE_PIL:
            return
        self.log.info('Checking uploaded files list using PIL')
        files_images = self.load_files()
        if files_images:
            self.log.info('getting My Photos list')
            my_photos = await self.mon.get_tv_content('MY-C0002')
            if my_photos is not None and len(my_photos) > 0:
                await self.check_thumbnails(files_images, my_photos)
            else:
                self.log.info('no photos found on tv')
        else:
            self.log.info('no files, using origional uploaded files list')
            
    async def check_thumbnails(self, files_images, my_photos):
        '''
        download thumbnails from my_photos to compare with file data
        save any updates
        '''
        self.log.info('downloading My Photos thumbnails')
        my_photos_thumbnails = await self.get_thumbnails(my_photos)
        if my_photos_thumbnails:
            self.log.info('checking thumbnails against {} files, please wait...'.format(len(files_images)))
            self.compare_thumbnails(files_images, my_photos_thumbnails)
            self.mon.write_program_data()
        else:
            self.log.info('failed to get thumbnails')
            
    def compare_thumbnails(self, files_images, my_photos_thumbnails):
        '''
        compare file data with thumbnails to find a match, and update update_uploaded_files
        '''
        for k, (filename, file_data) in enumerate(files_images.items()):
            for i, (my_content_id, my_data) in enumerate(my_photos_thumbnails.items()):
                self.log_progress(len(files_images)*len(my_photos_thumbnails), k*len(files_images)+i)
                self.log.debug('checking: {} against {}, thumbnail: {} bytes'.format(filename, my_content_id, len(my_data)))
                if self.are_images_equal(Image.open(io.BytesIO(my_data)), file_data):
                    self.log.info('found uploaded file: {} as {}'.format(filename, my_content_id))
                    if filename not in self.uploaded_files.keys():
                        self.mon.update_uploaded_files(filename, my_content_id)
                    break
        
    def log_progress(self, total, count):
        '''
        log % progress every 10% if this will take a while
        '''
        if total >= 1000:
            percent = min(100,(count*100)//total)
            if count % (total//10) == 0:
                self.log.info('{}% complete'.format(percent))
        
    def load_files(self):
        '''
        reads folder files, and returns dictionary of filenames and binary data
        only used if PIL is installed
        '''
        files = self.mon.get_folder_files()
        self.log.info('loading files: {}'.format(files))
        files_images = self.get_files_dict(files)
        self.log.info('loaded: {}'.format(list(files_images.keys())))
        return files_images
        
    def get_files_dict(self, files):
        '''
        makes a dictionary of filename and file binary data
        warns if file type given by extension is wrong
        only used if PIL is installed
        '''
        files_images = {}
        for file in files:
            try:
                data = Image.open(os.path.join(self.folder, file))
                format = self.mon.get_file_type(os.path.join(self.folder, file), data)
                if not (file.lower().endswith(format) or (format=='jpeg' and file.lower().endswith('jpg'))):
                    self.log.warning('file: {} is of type {}, the extension is wrong! please fix this'.format(file, format))
                files_images[file] = data
            except Exception as e:
                self.log.warning('Error loading: {}, {}'.format(file, e))
        return files_images
        
    async def get_thumbnails(self, content_ids):
        '''
        gets thumbnails from tv in list of content_ids
        returns dictionary of content_ids and binary data
        only used if PIL is installed
        '''
        thumbnails = {}
        if content_ids:
            if self.mon.api_version == 0:
                thumbnails = {content_id:await self.mon.tv.get_thumbnail(content_id) for content_id in content_ids}
            elif self.mon.api_version == 1:
                thumbnails = {os.path.splitext(k)[0]:v for k,v in (await self.mon.tv.get_thumbnail_list(content_ids)).items()}
        self.log.info('got {} thumbnails'.format(len(thumbnails)))
        return thumbnails
        
    def fix_file_type(self, filename, file_type, image_data=None):
        if not all([HAVE_PIL, file_type]):
            return file_type
        org = file_type
        file_type = Image.open(filename).format.lower() if not image_data else image_data.format.lower()
        if file_type in['jpg', 'jpeg', 'mpo']:
            file_type = 'jpeg'
        if not (org == file_type or (org == 'jpg' and file_type == 'jpeg')):
            self.log.warning('file {} type changed from {} to {}'.format(filename, org, file_type))
        return file_type
        
    def are_images_equal(self, img1, img2):
        '''
        rough check if images are similar using PIL (avoid numpy which is faster)
        '''
        img1 = img1.convert('L').resize((384, 216)).filter(ImageFilter.GaussianBlur(radius=2))
        img2 = img2.convert('L').resize((384, 216)).filter(ImageFilter.GaussianBlur(radius=2))
        img3 = ImageChops.difference(img1, img2)    #updated 11/3/25 per suggestion in issue #11
        diff = sum(list(img3.getdata()))/(384*216)  #normalize
        equal_content = diff <= 1.0                 #pick a threshhold
        self.log.debug('equal_content: {}, diff: {}'.format(equal_content, diff))
        return equal_content

   

class ImportedCollection(BaseModel):
    name: str
    kind: Literal['manual', 'keywords']
    files: Optional[list[str]] = None
    keywords: Optional[list[str]] = None


class ImportRequest(BaseModel):
    collections: list[ImportedCollection]


class TheFrameController:

    allowed_ext = ['jpg', 'jpeg', 'png']

    def __init__(self, options_file=OPTIONS_FILE):
        @asynccontextmanager
        async def lifespan(_: FastAPI):
            # the server answers at once; the monitor finds the TV in the background
            tasks = [] if self.config_error else [
                asyncio.create_task(self.monitor.run()),
                asyncio.create_task(self._slideshow_timer()),
                asyncio.create_task(self.update_metadata()),
            ]
            yield
            for task in tasks:
                task.cancel()
            if self.tv is not None:
                with contextlib.suppress(Exception):
                    await self.tv.close()

        self.app = FastAPI(lifespan=lifespan)
        # collections editor, shown in Home Assistant's sidebar through Ingress (ingress_entry: editor/)
        self.app.mount("/editor", StaticFiles(directory=STATIC_DIR, html=True), name="editor")
        self.app.add_api_route("/", lambda: RedirectResponse("editor/"), include_in_schema=False)
        logging.basicConfig(level=logging.INFO)
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.tv_files = {}
        self.job = None                 # current or last sync job
        self._sync_retry_after = 0
        self._last_advance = time.monotonic()
        self._published_status = None
        self.events = EventHub()
        self._sync_lock = asyncio.Lock()
        self._collection_lock = asyncio.Lock()
        self.tv = None
        self.ip_control = None
        self.config_error = None
        try:
            with open(options_file, "r") as config_file:
                config = json.load(config_file)
        except Exception as e:
            config = {}
            self.log.error("cannot read %s: %s", options_file, e)
            self.config_error = f"Failed to load configuration from {options_file}, see the add-on log"
        self.tv_ip = config.get("tv_ip")
        if not self.tv_ip and not self.config_error:
            self.config_error = f"tv_ip is not set in {options_file}"
        if self.config_error:
            self.log.error(self.config_error)
        self.tv_mac = config.get("tv_mac")
        if not self.tv_mac:
            self.log.warning("No TV MAC address provided, WOL is not available.")
        self.token_file = config.get("token_file")
        self.log.info(f"Using token file: {self.token_file}")
        self.json_path = config.get("json_path")
        self.log.info(f"Using JSON path: {self.json_path}")
        self.folder = config.get("image_path")
        self.log.info(f"Using image folder: {self.folder}")
        self.matte = config.get("matte", "none")
        self.log.info(f"Using matte: {self.matte}")
        self.place_names = bool(config.get("place_names"))
        self._places = {}
        self._resolving_places = False
        if config.get("ip_control") and self.tv_ip:
            token_file = config.get("ip_control_token_file") or os.path.join(
                os.path.dirname(self.token_file or OPTIONS_FILE), "ipcontrol-token.txt")
            self.ip_control = ipcontrol.SamsungTVAsyncIPControl(self.tv_ip, token_file=token_file)
            self.log.info("IP Control enabled, token file: {}, paired: {}".format(token_file, bool(self.ip_control.token)))
        self.start = time.time()
        self.pil = PIL_methods(self)
        self.port=8002
        self.monitor = TVMonitor(self)
        # slideshow state lives next to the options, i.e. in the add-on's /data
        self.slideshow = Slideshow(os.path.join(os.path.dirname(os.path.abspath(options_file)), "state.json"))
        if self.json_path and os.path.isfile(self.json_path):
            with contextlib.suppress(Exception):
                self.tv_files = self.read_uploaded_files(self.json_path)
        self.history = CollectionHistory(os.path.join(os.path.dirname(os.path.abspath(options_file)), "history.json"))
        self.recover_collections()
        self._change_ids = self.history.change_ids()
        self.resolve_legacy_position()
        self.setup_v1_routes()

    def paired_ip_control(self):
        return self.ip_control if self.ip_control is not None and self.ip_control.token else None

    def create_tv(self):
        '''
        create the TV client; blocking (REST call and possibly a websocket open), run in a worker thread
        '''
        return SamsungTVAsyncArt(host=self.tv_ip, port=self.port, token_file=self.token_file, timeout=TV_CONNECT_TIMEOUT)

    async def get_api_version(self):
            '''
            checks api version to see if it's old (<2021) or new type
            sets api_version to 0 for old, and 1 for new
            '''
            api_version = await self.tv.get_api_version()
            self.log.info('API version: {}'.format(api_version))
            self.api_version = 0 if int(api_version.replace('.','')) < 4000 else 1

    def read_uploaded_files(self, json_path):
        try:
            with open(json_path, "r") as f:
                content = f.read()
                data = json.loads(content)
                return data.get("uploaded_files", {})
        except FileNotFoundError:
            raise RuntimeError(f"JSON file not found: {json_path}")
        except json.JSONDecodeError:
            raise RuntimeError(f"Invalid JSON format in: {json_path}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error reading {json_path}: {e}")

    async def check_file_dict_availability(self, reload=False):
        if not (self.json_path and os.path.isfile(self.json_path)):
            return      # fresh install: nothing uploaded yet
        if not self.tv_files or reload:
            try:
                files = self.read_uploaded_files(self.json_path)
                if not isinstance(files, dict):
                    raise RuntimeError("uploaded_files.json did not return a dictionary.", files, self.json_path)
                self.tv_files.update(files)
            except Exception as e:
                raise RuntimeError(f"Failed to load uploaded_files.json: {e}")

    async def get_content_id(self, filename):
        await self.check_file_dict_availability()
        try:
            file_entry = self.tv_files.get(filename)
            if not file_entry:
                raise ValueError(f"Filename '{filename}' not found in uploaded_files.json")
            content_id = file_entry.get("content_id")
            if not content_id:
                raise ValueError(f"No content_id found for filename '{filename}'")
            return content_id
        except Exception as e:
            raise RuntimeError(f"Failed to get content_id for {filename}: {e}")

    async def select_image(self, content_id):
        try:
            async with asyncio.timeout(TV_CALL_TIMEOUT):
                await self.tv.select_image(content_id)
            self.monitor.current_content_id = content_id
            return content_id
        except Exception as e:
            self.log.warning("Failed to select image for content_id {}: {}".format(content_id, e))
            return None
        
    async def get_tv_content(self, category='MY-C0002'):
        '''
        gets content_id list of category - either My Photos (MY-C0002) or Favourites (MY-C0004) from tv
        '''
        try:
            result = [v['content_id'] for v in await self.tv.available(category, timeout=10)]
        except AssertionError:
            self.log.warning('failed to get contents from TV')
            result = None
        return result
        
    def get_folder_files(self):
        '''
        returns list of files in folder is extension matches allowed image types
        '''
        return [f for f in os.listdir(self.folder) if os.path.isfile(os.path.join(self.folder, f)) and self.get_file_type(os.path.join(self.folder, f)) in self.allowed_ext]
        
    async def get_current_artwork(self):
        '''
        reads currently displayed art content_id from tv
        '''
        try:
            async with asyncio.timeout(TV_CALL_TIMEOUT):
                content_id = (await self.tv.get_current()).get('content_id')
        except Exception:
            content_id = None
        return content_id
            
    async def sync_file_list(self):
        '''
        if art has been deleted on tv, resyncronises uploaded_files with tv
        '''
        my_photos = await self.get_tv_content('MY-C0002')
        if my_photos is not None:
            self.tv_files = {k:v for k,v in self.tv_files.items() if v['content_id'] in my_photos}
            self.write_program_data()
        
    def get_time(self, sec):
        '''
        returns seconds as timedelta for display as h:m:s
        '''
        return datetime.timedelta(seconds = sec)
   
    def load_program_data(self):
        '''
        load previous settings on program start update
        '''
        if os.path.isfile(self.json_path):
            with open(self.json_path, 'r') as f:
                program_data = json.load(f)
                self.tv_files = program_data.get('uploaded_files', program_data)
                self.start = program_data.get('last_update', time.time())
        else:
            self.tv_files = {}
            self.start = time.time()
        
    def write_program_data(self):
        '''
        save current settings, including file list with content_id on tv and last updated time
        also save the last time that art was updated, for timing slideshows
        '''
        with open(self.json_path, 'w') as f:
            program_data = {'last_update': self.start, 'uploaded_files': self.tv_files}
            json.dump(program_data, f)
            
    def read_file(self, filename):
        '''
        read image file, return file binary data, file type, and keywords (if available)
        '''
        try:
            file_type = self.get_file_type(filename)
            keywords = []
            if file_type:
                if file_type == 'jpeg' or file_type == 'jpg':
                    metadata = None
                    # Older macOS exports wrote keywords to IPTC 'subject'
                    try:
                        metadata = IPTCInfo(filename)
                    except Exception as e:
                        self.log.info('No IPTC metadata in {}: {}'.format(filename, e))
                    self.log.info('Metadata: {}'.format(metadata))
                    if metadata:
                        keywords = self.normalize_keywords(metadata['keywords'])
                    if not keywords:
                        # Newer macOS exports keep keywords in XMP dc:subject
                        keywords = self.extract_xmp_subject_keywords(filename)
                        keywords = self.normalize_keywords(keywords)
                    self.log.info('Keywords: {}'.format(keywords))
                with open(filename, 'rb') as f:
                    file_data = f.read()
            return file_data, file_type, keywords
        except Exception as e:
            self.log.error('Error reading file: {}, {}'.format(filename, e))
        return None, None, None

    def normalize_keywords(self, keywords):
        if not keywords:
            return []
        normalized = [k.decode('utf-8', errors='replace') if isinstance(k, bytes) else str(k) for k in keywords]
        return [k for k in normalized if k]

    def extract_xmp_subject_keywords(self, filename):
        keywords = []
        try:
            with open(filename, 'rb') as f:
                file_data = f.read()
        except Exception as e:
            self.log.info('Unable to read {} for XMP parsing: {}'.format(filename, e))
            return keywords
        xmp_blocks = re.findall(rb'<x:xmpmeta[\s\S]*?</x:xmpmeta>', file_data, flags=re.IGNORECASE)
        if not xmp_blocks:
            return keywords
        seen = set()
        for xmp_block in xmp_blocks:
            try:
                root = ET.fromstring(xmp_block.decode('utf-8', errors='replace'))
            except ET.ParseError:
                continue
            for subject in root.findall('.//{http://purl.org/dc/elements/1.1/}subject'):
                for item in subject.findall('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li'):
                    if item.text:
                        kw = item.text.strip()
                        if kw and kw not in seen:
                            seen.add(kw)
                            keywords.append(kw)
        return keywords
        
    def get_file_type(self, filename, image_data=None):
        '''
        try to figure out what kind of image file is, starting with the extension
        use PIL if available to check
        fix the file type if it's wrong
        '''
        try:
            file_type = os.path.splitext(filename)[1][1:].lower()
            file_type = file_type.lower() if file_type else None
            # file_type = self.pil.fix_file_type(filename, file_type, image_data)
            return file_type
        except Exception as e:
            self.log.error('Error reading file: {}, {}'.format(filename, e))
        return None

    def update_uploaded_files(self, filename, content_id, keywords=None, meta=None):
        '''
        if file is uploaded, update the dictionary entry
        if content_id is None, file failed to upload, so remove it from the dict
        '''
        keywords_json = json.dumps(keywords if keywords is not None else [])
        self.tv_files.pop(filename, None)
        if content_id:
            self.tv_files[filename] = {'content_id': content_id, 'keywords': keywords_json, 'modified':self.get_last_updated(filename),
                                       'meta': meta or {}}
        
    async def upload_files(self, filenames):
        '''
        upload files in list to tv
        an upload the tv did not confirm may still have been added, so look for it in My Photos
        instead of uploading it again on the next sync
        '''
        known = None
        for filename in filenames:
            path = os.path.join(self.folder, filename)
            file_data, file_type, exif_tags = self.read_file(path)
            meta = image_meta(path)
            if file_data and self.tv.art_mode:
                if known is None:
                    known = set(await self.get_tv_content('MY-C0002') or [])
                self.log.info('uploading : {} to tv'.format(filename))
                content_id = await self.tv.upload(file_data, file_type=file_type, matte=self.matte, portrait_matte=self.matte,
                                                  date=tv_date(meta))
                if not content_id:
                    content_id = await self.find_unconfirmed_upload(known)
                if content_id:
                    known.add(content_id)
                self.update_uploaded_files(filename, content_id, exif_tags, meta)
                if self.tv_files.get(filename, {}).get('content_id'):
                    self.log.info('uploaded : {} to tv as {}'.format(filename, self.tv_files[filename]['content_id']))
                else:
                    self.log.warning('file: {} failed to upload'.format(filename))
                self.write_program_data()
                if self.job is not None and self.job.state == 'running':
                    self.job.done += 1
                    self.publish_status()
            
    async def find_unconfirmed_upload(self, known):
        '''
        content_id of an upload the tv added without confirming, if exactly one new image appeared
        known: content_ids in My Photos before the upload, updated with what is there now
        '''
        my_photos = await self.get_tv_content('MY-C0002')
        if my_photos is None:
            return None
        new = set(my_photos) - known
        known.update(my_photos)
        if len(new) == 1:
            content_id = new.pop()
            self.log.info('upload was not confirmed, found it on the tv as {}'.format(content_id))
            return content_id
        if new:
            self.log.warning('upload was not confirmed and {} new images are on the tv, cannot tell which is ours'.format(len(new)))
        return None

    async def delete_files_from_tv(self, content_ids):
        '''
        remove files from tv if tv is in art mode
        '''
        if self.tv.art_mode:
            self.log.info('removing files from tv : {}'.format(content_ids))
            await self.tv.delete_list(content_ids)
            await self.sync_file_list()

    def image_file(self, name):
        '''
        path of an image file named in a request, or None; only files directly in the image folder,
        resolving symlinks and '..', so a request cannot read anything else
        '''
        if not self.folder:
            return None
        folder = os.path.realpath(self.folder)
        path = os.path.realpath(os.path.join(folder, name))
        if os.path.dirname(path) != folder or not os.path.isfile(path):
            return None
        return path

    def get_last_updated(self, filename):
        '''
        get last updated timestamp for file
        '''
        return os.path.getmtime(os.path.join(self.folder, filename))
        
    async def remove_files(self, files):
        '''
        if files deleted, remove them from tv
        '''
        content_ids_removed = [v['content_id'] for k, v in self.tv_files.items() if k not in files]
        #delete images from tv
        if content_ids_removed:
            await self.delete_files_from_tv(content_ids_removed)
            return True
        return False
            
    def new_files(self, files):
        return [f for f in files if f not in self.tv_files.keys()]

    def modified_files(self, files):
        return [f for f in files if f in self.tv_files.keys() and self.tv_files[f].get('modified') != self.get_last_updated(f)]

    async def add_files(self, files):
        '''
        if new files found, upload to tv
        '''
        new_files = self.new_files(files)
        #upload new files
        if new_files:
            self.log.info('adding files to tv : {}'.format(new_files))
            await self.wait_for_files(new_files)
            await self.upload_files(new_files)
            return True
        return False
            
    async def update_files(self, files):
        '''
        check if files were modified
        if so, delete old content on tv and upload new
        '''
        modified_files = self.modified_files(files)
        #delete old file and upload new:
        if modified_files:
            self.log.info('updating files on tv : {}'.format(modified_files))
            await self.wait_for_files(modified_files)
            files_to_delete = [v['content_id'] for k, v in self.tv_files.items() if k in modified_files]
            await self.delete_files_from_tv(files_to_delete)
            await self.upload_files(modified_files)
            return True
        return False
            
    async def wait_for_files(self, files):
        #wait for files to arrive
        await asyncio.sleep(min(10, 5 * len(files)))
        
    async def _do_sync(self, job=None):
        '''
        returns False if the tv left art mode during the sync, so some files were skipped
        '''
        files = self.get_folder_files()
        await self.sync_file_list()
        await self.remove_files(files)
        if job is not None:
            job.total = len(self.new_files(files)) + len(self.modified_files(files))
            job.done = 0
        await self.add_files(files)
        await self.update_files(files)
        if self.tv.art_mode is False:
            self.log.info('tv left art mode during sync, queuing the rest')
            return False
        return True

    @property
    def sync_pending(self):
        return self.job is not None and self.job.state == 'queued'

    def request_sync(self):
        '''
        returns (job, created): the queued or running sync job, or a new queued one
        '''
        if self.job is not None and self.job.state in ('queued', 'running'):
            return self.job, False
        self.job = SyncJob()
        self.publish_status()
        return self.job, True

    async def run_sync_job(self):
        '''
        run the queued sync job if the tv is in art mode; one job at a time
        a sync that fails, or that is cut short by leaving art mode, stays queued and is retried
        '''
        job = self.job
        if job is None or job.state != 'queued' or not self.monitor.in_artmode:
            return
        if time.monotonic() < self._sync_retry_after:
            return
        async with self._sync_lock:
            if job.state != 'queued':
                return
            job.state = 'running'
            job.started = time.time()
            job.attempts += 1
            job.error = None
            job.ran.clear()
            self.publish_status()
            try:
                self.log.info('sync {}: checking directory: {}'.format(job.id, self.folder))
                if await self._do_sync(job):
                    job.state = 'done'
                    job.finished = time.time()
                else:
                    job.state = 'queued'
            except Exception as e:
                self.log.warning('sync {} failed, retrying in art mode after {}s: {}'.format(job.id, SYNC_RETRY_DELAY, e))
                job.state = 'queued'
                job.error = str(e)
                self._sync_retry_after = time.monotonic() + SYNC_RETRY_DELAY
            except asyncio.CancelledError:
                job.state = 'queued'        # e.g. add-on shutting down; runs again next time
                raise
            finally:
                job.ran.set()
        self.publish_status()
        self.events.publish('files', {'file_count': len(self.tv_files)})
        asyncio.create_task(self.resolve_places())

    @staticmethod
    def file_keywords(entry):
        '''
        keywords of an uploaded_files entry, stored as a JSON string (or a list by older writers)
        '''
        raw = entry.get('keywords')
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return []
        return raw if isinstance(raw, list) else []

    def active_collection(self):
        return self.slideshow.collections.get(self.slideshow.collection_id)

    def content(self):
        '''
        what the slideshow plays: (mode, keywords, files) of the active collection as it is now, else its own
        '''
        collection = self.active_collection()
        if collection is None:
            return self.slideshow.mode, self.slideshow.keywords, self.slideshow.files
        if collection['kind'] == 'manual':
            return 'files', [], collection['files']
        return 'keywords', collection['keywords'], []

    def playlist(self, mode=None, keywords=None, files=None):
        '''
        file names in the slideshow (or for the given mode with keywords or files), in order;
        only files uploaded to the tv
        '''
        if mode is None:
            mode, keywords, files = self.content()
        if mode == 'files':
            return [f for f in files or [] if f in self.tv_files]
        wanted = set(keywords or [])
        return [f for f, v in self.tv_files.items() if wanted.intersection(self.file_keywords(v))]

    def collection_playlist(self, collection):
        if collection['kind'] == 'manual':
            return self.playlist('files', files=collection['files'])
        return self.playlist('keywords', keywords=collection['keywords'])

    def collection_info(self, collection):
        info = {k: collection[k] for k in ('id', 'name', 'kind', 'revision', 'updated', 'updated_by')}
        if collection['kind'] == 'manual':
            info.update(mode='files', files=collection['files'],
                        missing=[f for f in collection['files'] if f not in self.tv_files])
        else:
            info.update(mode='keywords', keywords=collection['keywords'])
        info['count'] = len(self.collection_playlist(collection))
        return info

    def collection_names(self):
        return sorted((c['name'] for c in self.slideshow.collections.values()), key=str.lower)

    def recover_collections(self):
        '''
        roll the collections forward to the history, which is written first: after a crash between writing
        history.json and state.json the last change is in the history only
        '''
        collections = self.slideshow.collections
        recovered = []
        for cid, entries in self.history.entries.items():
            last = entries[-1] if entries else None
            current = collections.get(cid)
            if last is None or (current and current['revision'] >= last['revision']):
                continue
            if last['after'] is None:
                if current is not None:
                    if self.slideshow.collection_id == cid:
                        self.leave_collection()
                    del collections[cid]
                    recovered.append(cid)
            else:
                collections[cid] = copy.deepcopy(last['after'])
                recovered.append(cid)
        if recovered:
            self.log.warning('collections recovered from the history after an interrupted write: {}'.format(recovered))
            self.slideshow.save()

    def check_name(self, name, collection_id=None):
        name = (name or '').strip()
        if not name or len(name) > 60:
            raise ChangeError(422, 'invalid_change', 'collection names need 1 to 60 characters')
        if any(c['name'] == name and c['id'] != collection_id for c in self.slideshow.collections.values()):
            raise ChangeError(409, 'name_taken', 'there is already a collection named {}'.format(name))
        return name

    @staticmethod
    def place(files, items, after, has_after):
        '''
        insert items into files: after the anchor file; at the start if after is None; at the end without an
        anchor or if the anchor is not in files
        '''
        if not has_after or (after is not None and after not in files):
            index = len(files)
        else:
            index = 0 if after is None else files.index(after) + 1
        files[index:index] = items

    def apply_change(self, work, change):
        '''
        apply one change to a working copy of a collection (None if it does not exist);
        returns the new working copy and the changes that undo it, in the order to apply them
        '''
        kind = change['change']
        if kind == 'create':
            if work is not None:
                raise ChangeError(422, 'invalid_change', 'the collection exists already')
            work = {'id': change['id'], 'name': self.check_name(change.get('name')), 'kind': change.get('kind') or 'manual',
                    'revision': 0}
            if work['kind'] == 'manual':
                work['files'] = list(dict.fromkeys(change.get('files') or []))
            else:
                work['keywords'] = sorted(set(change.get('keywords') or []))
            return work, [{'change': 'delete'}]
        if kind == 'replace':
            inverse = [{'change': 'replace', 'collection': copy.deepcopy(work)}] if work else [{'change': 'delete'}]
            new = copy.deepcopy(change['collection'])
            new['id'] = change['id']
            try:
                new['name'] = self.check_name(new['name'], new['id'])
            except ChangeError as e:
                if e.code != 'name_taken':
                    raise
                new['name'] = self.check_name('{} (restored)'.format(new['name'])[:60], new['id'])
            new['revision'] = work['revision'] if work else 0
            return new, inverse
        if work is None:
            raise ChangeError(404, 'not_found', 'no such collection')
        if kind == 'delete':
            return None, [{'change': 'replace', 'collection': copy.deepcopy(work)}]
        if kind == 'rename':
            old = work['name']
            work['name'] = self.check_name(change.get('name'), work['id'])
            return work, [{'change': 'rename', 'name': old}]
        if kind == 'set_keywords':
            if work['kind'] != 'keywords':
                raise ChangeError(422, 'invalid_change', 'set_keywords needs a keyword collection')
            old = work['keywords']
            work['keywords'] = sorted(set(change.get('keywords') or []))
            return work, [{'change': 'set_keywords', 'keywords': old}]
        if work['kind'] != 'manual':
            raise ChangeError(422, 'invalid_change', '{} needs a manual collection'.format(kind))
        files = work['files']
        items = list(dict.fromkeys(change.get('files') or []))
        if kind == 'add':
            added = [f for f in items if f not in files]
            self.place(files, added, change.get('after'), 'after' in change)
            return work, [{'change': 'remove', 'files': added}]
        if kind == 'remove':
            # put each file back after the file that preceded it, in the original order
            inverse = [{'change': 'add', 'files': [f], 'after': files[i - 1] if i else None}
                       for i, f in enumerate(files) if f in items]
            work['files'] = [f for f in files if f not in items]
            return work, inverse
        if kind == 'move':
            moving = [f for f in items if f in files]
            inverse = [{'change': 'move', 'files': [f], 'after': files[i - 1] if i else None}
                       for i, f in enumerate(files) if f in moving]
            rest = [f for f in files if f not in moving]
            after = change.get('after')
            # without an anchor, or onto one of the moved files: to the end
            self.place(rest, moving, after, 'after' in change and after not in moving)
            work['files'] = rest
            return work, inverse
        raise ChangeError(422, 'invalid_change', 'unknown change {}'.format(kind))

    async def commit_change(self, change_id, collection_id, kind, changes, user, params=None, undo_of=None):
        '''
        apply changes (one user action) to a collection as one revision: all or nothing, one at a time, logged
        and announced; a change_id seen before is answered without applying it again
        '''
        async with self._collection_lock:
            if change_id in self._change_ids:
                cid, revision = self._change_ids[change_id]
                collection = self.slideshow.collections.get(cid)
                return {'change_id': change_id, 'duplicate': True, 'collection_id': cid, 'revision': revision,
                        'collection': self.collection_info(collection) if collection else None}
            current = self.slideshow.collections.get(collection_id)
            if current is None and changes[0]['change'] not in ('create', 'replace'):
                if self.history.is_deleted(collection_id):
                    raise ChangeError(409, 'collection_deleted', 'the collection was deleted')
                raise ChangeError(404, 'not_found', 'no such collection')
            work = copy.deepcopy(current)
            inverse = []
            for change in changes:
                work, undo = self.apply_change(work, dict(change, id=collection_id))
                inverse = undo + inverse
            revision = max(current['revision'] if current else 0, self.history.last_revision(collection_id)) + 1
            now = time.time()
            if work is not None:
                work.update(revision=revision, updated=now, updated_by=user)
            self.history.add(collection_id, {
                'change_id': change_id, 'change': kind, 'params': params or {}, 'user': user, 'time': now,
                'revision': revision, 'after': copy.deepcopy(work), 'inverse': inverse, 'undone': False,
                'undo_of': undo_of})
            if undo_of is not None:
                undone = self.history.entry(collection_id, undo_of)
                if undone:
                    undone['undone'] = True
            self.history.save()     # first: the state can be rolled forward from it
            if work is None:
                if self.slideshow.collection_id == collection_id:
                    self.leave_collection()     # the slideshow keeps showing the same images
                self.slideshow.collections.pop(collection_id, None)
            else:
                self.slideshow.collections[collection_id] = work
            self.slideshow.save()
            self._change_ids[change_id] = (collection_id, revision)
            if len(self._change_ids) > 2000:
                self._change_ids = self.history.change_ids()
            info = self.collection_info(work) if work else None
            self.log.info('collection {} {} by {} (revision {})'.format(
                (work or current or {}).get('name', collection_id), kind, user, revision))
        self.events.publish('collection', {'change': kind, 'change_id': change_id, 'collection_id': collection_id,
                                           'revision': revision, 'user': user, 'collection': info})
        self.publish_status()
        return {'change_id': change_id, 'duplicate': False, 'collection_id': collection_id, 'revision': revision,
                'collection': info}

    def slideshow_position(self):
        '''
        index of the image shown last in the playlist as it is now; -1 before the first one
        if that image left the playlist, the one just before the place it had, so the next step shows its follower
        '''
        s = self.slideshow
        if s.current_file is None:
            return -1
        playlist = self.playlist()
        if s.current_file in playlist:
            return playlist.index(s.current_file)
        return min(s.position, len(playlist)) - 1

    def slideshow_view(self):
        '''
        slideshow as reported by the API: settings, the content it plays, position and the active collection
        '''
        s = self.slideshow
        mode, keywords, files = self.content()
        collection = self.active_collection()
        return {"enabled": s.enabled, "interval_minutes": s.interval_minutes, "mode": mode,
                "keywords": keywords, "files": files, "position": self.slideshow_position(),
                "current_file": s.current_file, "collection": collection['name'] if collection else None,
                "collection_id": s.collection_id, "count": len(self.playlist())}

    def set_position(self, position):
        playlist = self.playlist()
        self.slideshow.current_file = playlist[position] if 0 <= position < len(playlist) else None
        self.slideshow.position = position if self.slideshow.current_file else -1

    def resolve_legacy_position(self):
        '''
        after converting a version 1 state file: turn its index into the file name it pointed at
        '''
        if self.slideshow.legacy_position is None:
            return
        self.set_position(self.slideshow.legacy_position)
        self.slideshow.legacy_position = None
        self.slideshow.save()

    async def slideshow_show(self, position):
        '''
        show the slideshow image at position (wrapping); content_id is looked up now, so re-uploads are fine
        returns (content_id, error) where error is None, 'empty_slideshow' or 'tv_error'
        '''
        playlist = self.playlist()
        if not playlist:
            return None, 'empty_slideshow'
        position %= len(playlist)
        content_id = self.tv_files[playlist[position]].get('content_id')
        if not content_id or not await self.select_image(content_id):
            return None, 'tv_error'
        self.slideshow.current_file, self.slideshow.position = playlist[position], position
        self.slideshow.save()
        self._last_advance = time.monotonic()
        self.publish_status()
        return content_id, None

    def leave_collection(self):
        '''
        keep playing what the active collection holds now, as the slideshow's own content
        '''
        mode, keywords, files = self.content()
        s = self.slideshow
        s.mode, s.keywords, s.files, s.collection_id = mode, list(keywords), list(files), None

    def update_slideshow(self, source, **changes):
        '''
        change slideshow settings; source (the endpoint) is logged, so unexpected changes can be traced
        collection (a name) makes that collection the active one, starting at its first image;
        changing mode, keywords or files by hand leaves the collection
        '''
        changes = {k: v for k, v in changes.items() if v is not None}
        s = self.slideshow
        before = self.slideshow_view()
        name = changes.pop('collection', None)
        position = changes.pop('position', None)
        if name is not None:
            s.collection_id = s.by_name(name)['id']
            if position is None:
                position = -1
        elif {'mode', 'keywords', 'files'} & changes.keys() and s.collection_id is not None:
            self.leave_collection()
        for k, v in changes.items():
            setattr(s, k, v)
        if position is not None:
            self.set_position(position)
        after = self.slideshow_view()
        changed = {k: v for k, v in after.items() if before[k] != v and k != 'count'}
        if changed:
            self.log.info('slideshow changed via {}: {}'.format(source, changed))
        s.save()
        self.publish_status()

    async def slideshow_tick(self):
        '''
        advance the slideshow when its interval is up; pauses outside art mode and while a sync runs
        '''
        s = self.slideshow
        if not s.enabled or not self.monitor.in_artmode:
            return
        if self.job is not None and self.job.state == 'running':
            return
        if time.monotonic() - self._last_advance < s.interval_minutes * 60:
            return
        await self.slideshow_show(self.slideshow_position() + 1)

    async def _slideshow_timer(self):
        while True:
            await asyncio.sleep(SLIDESHOW_TICK)
            try:
                await self.slideshow_tick()
            except Exception as e:
                self.log.warning('slideshow advance failed: {}'.format(e))

    def current_file(self):
        content_id = self.monitor.current_content_id
        if not content_id:
            return None
        return next((f for f, v in self.tv_files.items() if v.get('content_id') == content_id), None)

    def fill_metadata(self):
        '''
        read the metadata of uploaded files that have none yet (uploaded by versions before 1.5.0);
        returns the number of entries filled
        '''
        filled = 0
        for filename, entry in list(self.tv_files.items()):
            path = os.path.join(self.folder or '', filename)
            if 'meta' not in entry and os.path.isfile(path):
                entry['meta'] = image_meta(path)
                filled += 1
        return filled

    async def update_metadata(self):
        '''
        at start: fill missing metadata in the background, then look up place names
        '''
        try:
            if await asyncio.to_thread(self.fill_metadata):
                self.write_program_data()
                self.events.publish('files', {'file_count': len(self.tv_files)})
        except Exception as e:
            self.log.warning('reading image metadata failed: %s', e)
        await self.resolve_places()

    async def reverse_geocode(self, client, latitude, longitude):
        '''
        place name ("Hamburg, Deutschland") for a position from OpenStreetMap Nominatim;
        "" if there is none, None if the lookup failed
        '''
        response = await client.get(NOMINATIM_URL, params={
            'format': 'jsonv2', 'lat': latitude, 'lon': longitude, 'zoom': 10})
        response.raise_for_status()
        address = response.json().get('address') or {}
        locality = next((address[k] for k in ('city', 'town', 'village', 'municipality', 'county', 'state') if address.get(k)), None)
        return ', '.join(p for p in (locality, address.get('country')) if p)

    async def resolve_places(self):
        '''
        add place names to uploaded files with a GPS position (option place_names);
        positions are rounded to about 100 m, each is looked up once
        '''
        if not self.place_names or self._resolving_places:
            return
        self._resolving_places = True
        changed = False
        try:
            async with httpx.AsyncClient(timeout=10, headers={'User-Agent': USER_AGENT}) as client:
                for entry in list(self.tv_files.values()):
                    meta = entry.get('meta') or {}
                    if 'place' in meta or meta.get('latitude') is None:
                        continue
                    key = (round(meta['latitude'], 3), round(meta['longitude'], 3))
                    if key not in self._places:
                        self._places[key] = await self.reverse_geocode(client, *key)
                        await asyncio.sleep(NOMINATIM_DELAY)
                    meta['place'] = self._places[key]
                    changed = True
        except Exception as e:
            self.log.warning('looking up place names failed, trying again after the next sync: %s', e)
        finally:
            self._resolving_places = False
        if changed:
            self.write_program_data()
            self.events.publish('files', {'file_count': len(self.tv_files)})

    def status(self):
        '''
        status answered from cached state, never waits for the tv
        '''
        return {
            "in_artmode": None if self.monitor.state == 'unknown' else self.monitor.in_artmode,
            "current_content_id": self.monitor.current_content_id,
            "current_file": self.current_file(),
            "sync_pending": self.sync_pending,
            "file_count": len(self.tv_files),
            "slideshow": self.slideshow_view(),
            "collections": self.collection_names(),
            "job": self.job.as_dict() if self.job is not None else None,
            "power_state": self.monitor.state,
            "power_state_since": self.monitor.since,
            "last_check": self.monitor.last_check,
            "art_service_available": self.tv.art_service_available if self.tv is not None else None,
            "tv_events": self.monitor.events_connected,
            "ip_control": "paired" if self.paired_ip_control() else ("unpaired" if self.ip_control else "disabled"),
            "error": self.monitor.error,
            "event_clients": len(self.events.queues),
        }

    def publish_status(self):
        '''
        push the status to event stream clients when it changed (ignoring the check timestamp)
        '''
        status = self.status()
        compare = dict(status, last_check=None)
        if compare != self._published_status:
            self._published_status = compare
            self.events.publish('status', status)

    async def file_items(self):
        '''
        uploaded files with content_id and keywords, sorted by name
        '''
        await self.check_file_dict_availability(reload=True)
        items = [{
            "file_name": filename,
            "content_id": entry.get("content_id"),
            "keywords": self.file_keywords(entry),
            "modified": entry.get("modified"),
            **{key: (entry.get("meta") or {}).get(key) for key in META_FIELDS},
        } for filename, entry in self.tv_files.items()]
        items.sort(key=lambda x: x["file_name"].lower())
        return items

    async def all_keywords(self):
        await self.check_file_dict_availability(reload=True)
        return sorted({k for entry in self.tv_files.values() for k in self.file_keywords(entry)})

    async def wake(self):
        '''
        wake the tv: IP Control if paired, else Wake-on-LAN (needs tv_mac and host network)
        returns the method used, or None if there is no way to wake the tv
        '''
        if self.paired_ip_control():
            async with asyncio.timeout(TV_CALL_TIMEOUT):
                await self.ip_control.async_call("power_on")
            method = "ip_control"
        elif self.tv_mac:
            await asyncio.to_thread(send_magic_packet, self.tv_mac)
            method = "wol"
        else:
            return None
        self.monitor.poke()
        return method

    class ShowRequest(BaseModel):
        file_name: Optional[str] = None
        content_id: Optional[str] = None

    class SlideshowUpdate(BaseModel):
        enabled: Optional[bool] = None
        interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
        mode: Optional[Literal['keywords', 'files']] = None
        keywords: Optional[list[str]] = None
        files: Optional[list[str]] = None
        position: Optional[int] = Field(None, ge=-1)
        collection: Optional[str] = None

    class CollectionRequest(BaseModel):
        mode: Optional[Literal['keywords', 'files']] = None
        keywords: Optional[list[str]] = None
        files: Optional[list[str]] = None

    class SlideshowShowRequest(BaseModel):
        position: int = Field(ge=0)

    class ChangeRequest(BaseModel):
        id: str = Field(min_length=1, max_length=64, description="change ID made by the client; a retry with the same ID is not applied twice")
        change: Literal['create', 'add', 'remove', 'move', 'set_keywords', 'rename', 'delete']
        collection: Optional[str] = Field(None, description="collection ID; not for create")
        name: Optional[str] = None
        kind: Optional[Literal['manual', 'keywords']] = None
        files: Optional[list[str]] = None
        keywords: Optional[list[str]] = None
        after: Optional[str] = Field(None, description="anchor file; null for the start; left out for the end (add)")

    class UndoRequest(BaseModel):
        id: Optional[str] = Field(None, min_length=1, max_length=64)

    class RestoreRequest(BaseModel):
        revision: int = Field(ge=1)
        id: Optional[str] = Field(None, min_length=1, max_length=64)

    def v1_unavailable(self, need='art'):
        '''
        /api/v1 error for commands that need the tv in art mode (need='art') or on (need='on'), or None
        '''
        if self.config_error:
            return api_error(503, "not_configured", self.config_error)
        if need == 'art' and not self.monitor.in_artmode:
            return api_error(409, "tv_not_in_art_mode", "TV is not in art mode (power state: {})".format(self.monitor.state))
        if need == 'on' and not self.monitor.tv_on:
            return api_error(409, "tv_not_on", "TV is not on (power state: {})".format(self.monitor.state))
        return None

    def setup_v1_routes(self):
        '''
        versioned API used by the integration; errors are HTTP status codes with {"error": {"code", "message"}}
        '''

        @self.app.exception_handler(RequestValidationError)
        async def validation_error(request: Request, exc: RequestValidationError):
            return api_error(422, "invalid_request", str(exc.errors()))

        @self.app.get("/api/v1/status")
        async def v1_status():
            if self.config_error:
                return api_error(503, "not_configured", self.config_error)
            return self.status()

        @self.app.get("/api/v1/events")
        async def v1_events():
            '''
            server-sent events: 'status' (whole status, on connect and on every change), 'files' (after a sync)
            '''
            return StreamingResponse(self.events.stream(("status", self.status())), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})

        @self.app.get("/api/v1/files")
        async def v1_files():
            return {"files": await self.file_items()}

        @self.app.get("/api/v1/keywords")
        async def v1_keywords():
            return {"keywords": await self.all_keywords()}

        @self.app.post("/api/v1/sync", status_code=202)
        async def v1_sync():
            '''
            start a sync job; it waits as 'queued' until the tv is in art mode. 409 while one is queued or running
            '''
            if self.config_error:
                return api_error(503, "not_configured", self.config_error)
            job, created = self.request_sync()
            if not created:
                return JSONResponse(status_code=409, content={
                    "error": {"code": "job_conflict", "message": "a sync is already {}".format(job.state)},
                    "job": job.as_dict()})
            asyncio.create_task(self.run_sync_job())
            return {"job": job.as_dict()}

        @self.app.post("/api/v1/show")
        async def v1_show(req: self.ShowRequest):
            error = self.v1_unavailable()
            if error:
                return error
            if req.file_name:
                await self.check_file_dict_availability(reload=True)
                content_id = self.tv_files.get(req.file_name, {}).get("content_id")
                if not content_id:
                    return api_error(404, "not_found", "{} is not uploaded to the TV".format(req.file_name))
            elif req.content_id:
                content_id = req.content_id
            else:
                return api_error(422, "invalid_request", "file_name or content_id is required")
            if not await self.select_image(content_id):
                return api_error(502, "tv_error", "TV did not show {}".format(content_id))
            self.publish_status()
            return {"shown": content_id, "file_name": self.current_file()}

        @self.app.get("/api/v1/slideshow")
        async def v1_slideshow():
            return self.slideshow_view()

        @self.app.put("/api/v1/slideshow")
        async def v1_slideshow_update(req: self.SlideshowUpdate):
            '''
            change any of enabled, interval_minutes, mode, keywords, files, position; persisted
            '''
            changes = req.model_dump(exclude_none=True)
            if "files" in changes:
                changes["files"] = list(dict.fromkeys(changes["files"]))
            if "collection" in changes and self.slideshow.by_name(changes["collection"]) is None:
                return api_error(404, "not_found", "no collection named {}".format(changes["collection"]))
            self.update_slideshow('PUT /api/v1/slideshow', **changes)
            return self.slideshow_view()

        @self.app.get("/api/v1/collections")
        async def v1_collections():
            await self.check_file_dict_availability(reload=True)
            collections = sorted(self.slideshow.collections.values(), key=lambda c: c['name'].lower())
            return {"collections": [self.collection_info(c) for c in collections]}

        async def change_response(coro):
            try:
                return await coro
            except ChangeError as e:
                return api_error(e.status, e.code, e.message)

        @self.app.post("/api/v1/collections/changes")
        async def v1_collection_change(req: self.ChangeRequest, request: Request):
            '''
            apply one change to a collection; changes are applied one at a time, logged and announced as
            "collection" events; a change ID sent again is answered without applying it twice
            '''
            change = req.model_dump(exclude_none=True, exclude={'id', 'collection'})
            if 'after' in req.model_fields_set:
                change['after'] = req.after
            if req.change == 'create':
                collection_id = uuid.uuid4().hex
            elif req.collection:
                collection_id = req.collection
            else:
                return api_error(422, "invalid_change", "{} needs the collection ID".format(req.change))
            return await change_response(self.commit_change(
                req.id, collection_id, req.change, [change], request_user(request), params=change))

        @self.app.get("/api/v1/collections/deleted")
        async def v1_collections_deleted():
            deleted = []
            for cid, entries in self.history.entries.items():
                if self.history.is_deleted(cid) and cid not in self.slideshow.collections:
                    before = next((c['collection'] for c in entries[-1]['inverse'] if c['change'] == 'replace'), {})
                    deleted.append({"id": cid, "name": before.get('name'), "deleted": entries[-1]['time'],
                                    "deleted_by": entries[-1]['user'], "revision": entries[-1]['revision']})
            return {"collections": sorted(deleted, key=lambda c: -c['deleted'])}

        @self.app.get("/api/v1/collections/export")
        async def v1_collections_export():
            collections = sorted(self.slideshow.collections.values(), key=lambda c: c['name'].lower())
            return {"version": 1, "exported": time.time(), "collections": [
                {k: c[k] for k in ('name', 'kind', 'files', 'keywords') if k in c} for c in collections]}

        @self.app.post("/api/v1/collections/import")
        async def v1_collections_import(req: ImportRequest, request: Request):
            '''
            add the collections of an export; a name that is taken gets a number, nothing is overwritten
            '''
            imported = []
            for c in req.collections:
                name = c.name.strip()[:60]
                base, n = name, 2
                while self.slideshow.by_name(name) is not None:
                    name = '{} ({})'.format(base[:54], n)
                    n += 1
                change = {'change': 'create', 'name': name, 'kind': c.kind, 'files': c.files or [],
                          'keywords': c.keywords or []}
                result = await change_response(self.commit_change(
                    uuid.uuid4().hex, uuid.uuid4().hex, 'import', [change], request_user(request),
                    params={'name': name}))
                if isinstance(result, JSONResponse):
                    return result
                imported.append(name)
            return {"imported": imported}

        @self.app.get("/api/v1/collections/{collection_id}/history")
        async def v1_collection_history(collection_id: str):
            entries = self.history.entries.get(collection_id)
            if not entries and collection_id not in self.slideshow.collections:
                return api_error(404, "not_found", "no such collection")
            keys = ('change_id', 'change', 'params', 'user', 'time', 'revision', 'undone', 'undo_of')
            return {"collection_id": collection_id,
                    "entries": [{k: e.get(k) for k in keys} for e in reversed(entries or [])]}

        @self.app.post("/api/v1/collections/{collection_id}/undo")
        async def v1_collection_undo(collection_id: str, request: Request, req: Optional[self.UndoRequest] = None):
            '''
            undo the requesting user's last change to the collection, also after others changed it since
            '''
            user = request_user(request)
            entry = self.history.last_undoable(collection_id, user)
            if entry is None:
                return api_error(409, "nothing_to_undo", "no change of yours to undo")
            return await change_response(self.commit_change(
                (req and req.id) or uuid.uuid4().hex, collection_id, 'undo', entry['inverse'], user,
                params={'revision': entry['revision'], 'change': entry['change']}, undo_of=entry['revision']))

        @self.app.post("/api/v1/collections/{collection_id}/restore")
        async def v1_collection_restore(collection_id: str, req: self.RestoreRequest, request: Request):
            '''
            return a collection to its state at a revision, as a new change; also restores deleted collections
            '''
            entry = self.history.entry(collection_id, req.revision)
            if entry is None:
                return api_error(404, "not_found", "revision {} is not in the history".format(req.revision))
            if entry['after'] is None:
                return api_error(422, "invalid_change", "revision {} is a deletion".format(req.revision))
            return await change_response(self.commit_change(
                req.id or uuid.uuid4().hex, collection_id, 'restore',
                [{'change': 'replace', 'collection': entry['after']}], request_user(request),
                params={'revision': req.revision}))

        @self.app.put("/api/v1/collections/{name}")
        async def v1_collection_save(name: str, req: self.CollectionRequest, request: Request):
            '''
            save a named slideshow preset: files (playlist), keywords, or without a body the current slideshow;
            saving under an existing name replaces its content and keeps its id; applied as a change
            '''
            name = name.strip()
            if not name or len(name) > 60:
                return api_error(422, "invalid_request", "collection names need 1 to 60 characters")
            mode = req.mode or ('files' if req.files is not None else 'keywords' if req.keywords is not None else None)
            if mode is None:
                mode, keywords, files = self.content()
            else:
                keywords, files = req.keywords or [], req.files or []
            content = {'kind': 'manual', 'files': list(dict.fromkeys(files))} if mode == 'files' else \
                {'kind': 'keywords', 'keywords': sorted(set(keywords))}
            existing = self.slideshow.by_name(name)
            if existing is None:
                result = await change_response(self.commit_change(
                    uuid.uuid4().hex, uuid.uuid4().hex, 'create', [dict(content, change='create', name=name)],
                    request_user(request), params={'name': name}))
            else:
                replacement = {k: v for k, v in existing.items() if k not in ('files', 'keywords')}
                replacement.update(content)
                result = await change_response(self.commit_change(
                    uuid.uuid4().hex, existing['id'], 'save', [{'change': 'replace', 'collection': replacement}],
                    request_user(request), params=content))
            if isinstance(result, JSONResponse):
                return result
            await self.check_file_dict_availability(reload=True)
            return self.collection_info(self.slideshow.collections[result['collection_id']])

        @self.app.delete("/api/v1/collections/{name}")
        async def v1_collection_delete(name: str, request: Request):
            collection = self.slideshow.by_name(name)
            if collection is None:
                return api_error(404, "not_found", "no collection named {}".format(name))
            result = await change_response(self.commit_change(
                uuid.uuid4().hex, collection['id'], 'delete', [{'change': 'delete'}], request_user(request)))
            if isinstance(result, JSONResponse):
                return result
            return {"deleted": name, "collection_id": collection['id']}

        async def v1_slideshow_show(position):
            error = self.v1_unavailable()
            if error:
                return error
            content_id, error = await self.slideshow_show(position)
            if error == "empty_slideshow":
                return api_error(409, "empty_slideshow", "the slideshow has no images uploaded to the TV")
            if error:
                return api_error(502, "tv_error", "TV did not show the next image")
            return {"shown": content_id, "position": self.slideshow.position, "file_name": self.current_file()}

        @self.app.post("/api/v1/slideshow/next")
        async def v1_slideshow_next():
            return await v1_slideshow_show(self.slideshow_position() + 1)

        @self.app.post("/api/v1/slideshow/previous")
        async def v1_slideshow_previous():
            position = self.slideshow_position()
            # after the image shown last left the playlist, position already points at the one before it
            gone = self.slideshow.current_file is not None and self.slideshow.current_file not in self.playlist()
            return await v1_slideshow_show(position if gone else position - 1)

        @self.app.post("/api/v1/slideshow/show")
        async def v1_slideshow_show_position(req: self.SlideshowShowRequest):
            count = len(self.playlist())
            if count and req.position >= count:
                return api_error(422, "invalid_request", "position {} is out of range (0-{})".format(req.position, count - 1))
            return await v1_slideshow_show(req.position)

        @self.app.get("/api/v1/thumbnail/{file_name:path}")
        async def v1_thumbnail(file_name: str, size: int = 320):
            if not HAVE_PIL:
                return api_error(501, "not_available", "Pillow is not installed")
            path = self.image_file(file_name)
            if path is None:
                return api_error(404, "not_found", "{} is not in the image folder".format(file_name))
            size = max(64, min(int(size), 1024))
            try:
                with Image.open(path) as img:
                    img = img.convert("RGB")
                    img.thumbnail((size, size))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=80)
            except Exception as e:
                self.log.warning("thumbnail: cannot read %s: %s", file_name, e)
                return api_error(422, "invalid_image", "{} cannot be read as an image".format(file_name))
            return Response(content=buf.getvalue(), media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

        @self.app.post("/api/v1/reconcile")
        async def v1_reconcile():
            '''
            recover content IDs missing from json_path by comparing the TV's thumbnails with the image files;
            slow, only needed after json_path was lost or replaced
            '''
            if not HAVE_PIL:
                return api_error(501, "not_available", "Pillow is not installed")
            error = self.v1_unavailable()
            if error:
                return error
            try:
                await self.get_api_version()
                await self.pil.initialize()
            except Exception as e:
                self.log.warning("reconcile failed: %s", e)
                return api_error(502, "tv_error", "reconcile failed, see the add-on log")
            self.publish_status()
            return {"file_count": len(self.tv_files)}

        @self.app.get("/api/v1/settings")
        async def v1_settings():
            error = self.v1_unavailable(need='on')
            if error:
                return error
            try:
                async with asyncio.timeout(TV_CALL_TIMEOUT):
                    settings = await self.tv.get_artmode_settings('')
            except Exception as e:
                self.log.warning("TV did not return its art mode settings: %s", e)
                return api_error(502, "tv_error", "TV did not return its art mode settings")
            return {"settings": settings}

        @self.app.post("/api/v1/power_on")
        async def v1_power_on():
            if self.config_error:
                return api_error(503, "not_configured", self.config_error)
            try:
                method = await self.wake()
            except Exception as e:
                self.log.warning("power_on failed: %s", e)
                return api_error(502, "tv_error", "power_on failed, see the add-on log")
            if method is None:
                return api_error(409, "wake_unavailable", "no way to wake the TV: pair IP Control or set tv_mac")
            return {"method": method}

        @self.app.post("/api/v1/ip_control/pair")
        async def v1_ip_control_pair():
            if self.ip_control is None:
                return api_error(409, "ip_control_disabled", "IP Control is disabled, set the ip_control option")
            try:
                await self.ip_control.async_pair()
            except Exception as e:
                self.log.warning("IP Control pairing failed: %s", e)
                return api_error(502, "tv_error", "pairing failed, see the add-on log")
            self.monitor.poke()
            return {"paired": True}

# Instantiate the controller
controller = TheFrameController()
app = controller.app
