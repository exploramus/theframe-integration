# TheFrame Controller for Samsung The Frame TV
#
# This code provides an API to manage and display images on a Samsung The Frame TV.
# It includes functionality to upload images, display slideshows, and manage TV settings.
# It uses FastAPI for the web interface and samsungframetv for communication with the TV.
# It also includes PIL for image processing if available.
# This code is designed to run in a Home Assistant environment, where it can be used as an add-on.

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from typing import Literal, Optional
from contextlib import asynccontextmanager
import contextlib
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


def api_error(status_code, code, message):
    '''
    /api/v1 error response: HTTP status plus a stable error code
    '''
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


class Slideshow:
    '''
    slideshow state owned by the add-on and persisted, so it survives restarts
    stores file names; content_ids are looked up when an image is shown, so a re-upload does not break it
    position is the index of the image shown last, -1 before the first one
    collections are named presets of mode with keywords or files; collection is the active one, if any
    '''

    FIELDS = ('enabled', 'interval_minutes', 'mode', 'keywords', 'files', 'position', 'collection')

    def __init__(self, path=None):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.path = path
        self.enabled = False
        self.interval_minutes = 2
        self.mode = 'keywords'      # keywords: files matching any keyword; files: an ordered playlist
        self.keywords = []
        self.files = []
        self.position = -1
        self.collection = None
        self.collections = {}       # name -> {'mode': 'keywords', 'keywords': [...]} or {'mode': 'files', 'files': [...]}
        self.load()

    def as_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    def load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path) as f:
                state = json.load(f)
            data = state.get('slideshow', {})
            for k in self.FIELDS:
                if k in data:
                    setattr(self, k, data[k])
            self.collections = state.get('collections', {})
        except Exception as e:
            self.log.warning('could not read slideshow state from {}: {}'.format(self.path, e))

    def save(self):
        if not self.path:
            return
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'slideshow': self.as_dict(), 'collections': self.collections}, f)
            os.replace(tmp, self.path)
        except Exception as e:
            self.log.warning('could not save slideshow state to {}: {}'.format(self.path, e))


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

   

class TheFrameController:

    allowed_ext = ['jpg', 'jpeg', 'png']

    def __init__(self, options_file=OPTIONS_FILE):
        @asynccontextmanager
        async def lifespan(_: FastAPI):
            # the server answers at once; the monitor finds the TV in the background
            tasks = [] if self.config_error else [
                asyncio.create_task(self.monitor.run()),
                asyncio.create_task(self._slideshow_timer()),
            ]
            yield
            for task in tasks:
                task.cancel()
            if self.tv is not None:
                with contextlib.suppress(Exception):
                    await self.tv.close()

        self.app = FastAPI(lifespan=lifespan)
        logging.basicConfig(level=logging.INFO)
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.tv_files = {}
        self.job = None                 # current or last sync job
        self._sync_retry_after = 0
        self._last_advance = time.monotonic()
        self._published_status = None
        self.events = EventHub()
        self._sync_lock = asyncio.Lock()
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

    def update_uploaded_files(self, filename, content_id, keywords=None):
        '''
        if file is uploaded, update the dictionary entry
        if content_id is None, file failed to upload, so remove it from the dict
        '''
        keywords_json = json.dumps(keywords if keywords is not None else [])
        self.tv_files.pop(filename, None)
        if content_id:
            self.tv_files[filename] = {'content_id': content_id, 'keywords': keywords_json, 'modified':self.get_last_updated(filename)}
        
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
            if file_data and self.tv.art_mode:
                if known is None:
                    known = set(await self.get_tv_content('MY-C0002') or [])
                self.log.info('uploading : {} to tv'.format(filename))
                content_id = await self.tv.upload(file_data, file_type=file_type, matte=self.matte, portrait_matte=self.matte)
                if not content_id:
                    content_id = await self.find_unconfirmed_upload(known)
                if content_id:
                    known.add(content_id)
                self.update_uploaded_files(filename, content_id, exif_tags)
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

    def playlist(self, mode=None, keywords=None, files=None):
        '''
        file names in the slideshow (or for the given mode with keywords or files), in order;
        only files uploaded to the tv
        '''
        if mode is None:
            mode, keywords, files = self.slideshow.mode, self.slideshow.keywords, self.slideshow.files
        if mode == 'files':
            return [f for f in files or [] if f in self.tv_files]
        wanted = set(keywords or [])
        return [f for f, v in self.tv_files.items() if wanted.intersection(self.file_keywords(v))]

    def collection_info(self, name):
        collection = self.slideshow.collections[name]
        return dict(collection, name=name, count=len(self.playlist(**collection)))

    def collection_names(self):
        return sorted(self.slideshow.collections, key=str.lower)

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
        self.slideshow.position = position
        self.slideshow.save()
        self._last_advance = time.monotonic()
        self.publish_status()
        return content_id, None

    def update_slideshow(self, source, **changes):
        '''
        change slideshow settings; source (the endpoint) is logged, so unexpected changes can be traced
        '''
        changes = {k: v for k, v in changes.items() if v is not None}
        name = changes.pop('collection', None)
        if name is not None:
            # load the collection; the slideshow starts again at its first image
            collection = self.slideshow.collections[name]
            changes.update(mode=collection['mode'], keywords=collection.get('keywords', []),
                           files=collection.get('files', []), position=changes.get('position', -1), collection=name)
        elif {'mode', 'keywords', 'files'} & changes.keys():
            changes['collection'] = None    # changed by hand, no longer the collection
        changed = {k: v for k, v in changes.items() if getattr(self.slideshow, k) != v}
        if changed:
            self.log.info('slideshow changed via {}: {}'.format(source, changed))
        for k, v in changed.items():
            setattr(self.slideshow, k, v)
        self.slideshow.save()
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
        await self.slideshow_show(s.position + 1)

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

    def status(self):
        '''
        status answered from cached state, never waits for the tv
        '''
        count = len(self.playlist())
        return {
            "in_artmode": None if self.monitor.state == 'unknown' else self.monitor.in_artmode,
            "current_content_id": self.monitor.current_content_id,
            "current_file": self.current_file(),
            "sync_pending": self.sync_pending,
            "file_count": len(self.tv_files),
            "slideshow": dict(self.slideshow.as_dict(), count=count),
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
            return dict(self.slideshow.as_dict(), count=len(self.playlist()))

        @self.app.put("/api/v1/slideshow")
        async def v1_slideshow_update(req: self.SlideshowUpdate):
            '''
            change any of enabled, interval_minutes, mode, keywords, files, position; persisted
            '''
            changes = req.model_dump(exclude_none=True)
            if "files" in changes:
                changes["files"] = list(dict.fromkeys(changes["files"]))
            if "collection" in changes and changes["collection"] not in self.slideshow.collections:
                return api_error(404, "not_found", "no collection named {}".format(changes["collection"]))
            self.update_slideshow('PUT /api/v1/slideshow', **changes)
            return dict(self.slideshow.as_dict(), count=len(self.playlist()))

        @self.app.get("/api/v1/collections")
        async def v1_collections():
            await self.check_file_dict_availability(reload=True)
            return {"collections": [self.collection_info(name) for name in self.collection_names()]}

        @self.app.put("/api/v1/collections/{name}")
        async def v1_collection_save(name: str, req: self.CollectionRequest):
            '''
            save a named slideshow preset: files (playlist), keywords, or without a body the current slideshow
            '''
            name = name.strip()
            if not name or len(name) > 60:
                return api_error(422, "invalid_request", "collection names need 1 to 60 characters")
            mode = req.mode or ('files' if req.files is not None else 'keywords' if req.keywords is not None else None)
            if mode is None:
                mode, keywords, files = self.slideshow.mode, self.slideshow.keywords, self.slideshow.files
            else:
                keywords, files = req.keywords or [], req.files or []
            collection = {'mode': 'files', 'files': list(dict.fromkeys(files))} if mode == 'files' else \
                {'mode': 'keywords', 'keywords': sorted(set(keywords))}
            self.slideshow.collections[name] = collection
            self.log.info('collection saved: {}: {}'.format(name, collection))
            if self.slideshow.collection == name:
                self.update_slideshow('PUT /api/v1/collections', collection=name)
            self.slideshow.save()
            self.publish_status()
            await self.check_file_dict_availability(reload=True)
            return self.collection_info(name)

        @self.app.delete("/api/v1/collections/{name}")
        async def v1_collection_delete(name: str):
            if name not in self.slideshow.collections:
                return api_error(404, "not_found", "no collection named {}".format(name))
            del self.slideshow.collections[name]
            self.log.info('collection deleted: {}'.format(name))
            if self.slideshow.collection == name:
                self.slideshow.collection = None    # the slideshow keeps showing the same images
            self.slideshow.save()
            self.publish_status()
            return {"deleted": name}

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
            return await v1_slideshow_show(self.slideshow.position + 1)

        @self.app.post("/api/v1/slideshow/previous")
        async def v1_slideshow_previous():
            return await v1_slideshow_show(self.slideshow.position - 1)

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
