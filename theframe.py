# TheFrame Controller for Samsung The Frame TV
#
# This code provides an API to manage and display images on a Samsung The Frame TV.
# It includes functionality to upload images, display slideshows, and manage TV settings.
# It uses FastAPI for the web interface and samsungtvws for communication with the TV.
# It also includes PIL for image processing if available.
# This code is designed to run in a Home Assistant environment, where it can be used as an add-on.

from fastapi import FastAPI
from pydantic import BaseModel
import asyncio
import os
import json
import logging
import sys
import io
import random
import json
import time
import datetime
from wakeonlan import send_magic_packet
from iptcinfo3 import IPTCInfo
HAVE_PIL = False
try:
    from PIL import Image, ImageFilter, ImageChops
    HAVE_PIL=True
except ImportError:
    pass


from samsungtvws.async_art import SamsungTVAsyncArt
from samsungtvws import __version__



class PIL_methods:
    
    def __init__(self, mon):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.mon = mon
        self.folder = self.mon.json_path
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

    def __init__(self):
        self.app = FastAPI()
        logging.basicConfig(level=logging.INFO)
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.tv_files = {}
        self.slideshow_files = {}
        self.slideshow_index = 0
        # Load configuration from /data/options.json
        try:
            with open("/data/options.json", "r") as config_file:
                config = json.load(config_file)
        except Exception as e:
            self.log.error(f"Failed to load configuration from /data/options.json: {e}")
            sys.exit(1)
        self.tv_ip = config.get("tv_ip")
        if not self.tv_ip:
            self.log.error("TV_IP is not set in /data/options.json.")
            sys.exit(1)
        self.tv_mac = config.get("tv_mac")
        if not self.tv_mac:
            self.log.warning("No TV MAC address provided, WOL will not be sent.")
        self.token_file = config.get("token_file")
        self.log.info(f"Using token file: {self.token_file}")
        self.json_path = config.get("json_path")
        self.log.info(f"Using JSON path: {self.json_path}")
        self.folder = config.get("image_path")
        self.log.info(f"Using image folder: {self.folder}")
        self.matte = config.get("matte", "none")
        self.log.info(f"Using matte: {self.matte}")
        self.tv_connect_timeout = int(config.get("tv_connect_timeout", 15))
        self.tv_connect_interval = int(config.get("tv_connect_interval", 2))
        self.log.info(f"Using TV connect timeout: {self.tv_connect_timeout}s")
        self.log.info(f"Using TV connect interval: {self.tv_connect_interval}s")
        self.start = time.time()
        self.current_content_id = None
        self.pil = PIL_methods(self)
        self.port=8002
        self.tv = None
        self._tv_lock = asyncio.Lock()
        self._last_wol_sent = 0
        self._wol_min_interval = 10
        self.tv_connect_last_error = None
        self.setup_routes()

    class ShowImageRequest(BaseModel):
        content_id: str

    class ShowFileRequest(BaseModel):
        file_name: str

    class SlideshowInitializeRequest(BaseModel):
        keyword_list: list
        slideshow_index: int

    class SlideshowIndexRequest(BaseModel):
        slideshow_index: int

    async def _tv_handshake(self):
        if not self.tv:
            return False
        try:
            await asyncio.wait_for(self.tv.get_api_version(), timeout=3)
            return True
        except Exception as e:
            self.tv_connect_last_error = str(e)
            return False

    async def ensure_tv(self, wake_on_lan=True):
        if self.tv and await self._tv_handshake():
            return True
        async with self._tv_lock:
            if not self.tv:
                try:
                    self.tv = SamsungTVAsyncArt(host=self.tv_ip, port=self.port, token_file=self.token_file)
                except Exception as e:
                    self.tv_connect_last_error = str(e)
                    self.tv = None
                    return False
            if wake_on_lan and self.tv_mac and time.time() - self._last_wol_sent >= self._wol_min_interval:
                try:
                    send_magic_packet(self.tv_mac)
                    self._last_wol_sent = time.time()
                    self.log.info(f"Sent WOL packet to {self.tv_mac}")
                except Exception as e:
                    self.log.warning(f"Failed to send WOL packet: {e}")
            start = time.time()
            while time.time() - start < self.tv_connect_timeout:
                if await self._tv_handshake():
                    return True
                await asyncio.sleep(self.tv_connect_interval)
        return False

    async def get_api_version(self):
            '''
            checks api version to see if it's old (<2021) or new type
            sets api_version to 0 for old, and 1 for new
            '''
            if not await self.ensure_tv(wake_on_lan=False):
                raise RuntimeError("TV is not reachable")
            api_version = await self.tv.get_api_version()
            self.log.info('API version: {}'.format(api_version))
            self.api_version = 0 if int(api_version.replace('.','')) < 4000 else 1

    async def get_tv_content(self, category='MY-C0002'):
        '''
        gets content_id list of category - either My Photos (MY-C0002) or Favourites (MY-C0004) from tv
        '''
        if not await self.ensure_tv():
            self.log.warning("TV is not reachable")
            return None
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

    async def sync_file_list(self):
        '''
        if art has been deleted on tv, resyncronises uploaded_files with tv
        '''
        tv_contents = await self.get_tv_content('MY-C0002')
        if tv_contents is not None:
            self.tv_files = {k:v for k,v in self.tv_files.items() if v['content_id'] in tv_contents}
            self.write_program_data()

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
            await self.tv.select_image(content_id)
            return content_id
        except Exception as e:
            self.log.warning(f"Failed to select image for content_id {content_id}: {e}")
            return None
        
    async def get_tv_content(self, category='MY-C0002'):
        '''
        gets content_id list of category - either My Photos (MY-C0002) or Favourites (MY-C0004) from tv
        '''
        if not await self.ensure_tv():
            self.log.warning("TV is not reachable")
            return None
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
        if not await self.ensure_tv():
            return None
        try:
            content_id = (await self.tv.get_current()).get('content_id')
        except Exception:
            content_id = None
        return content_id
            
    async def sync_file_list(self):
        '''
        if art has been deleted on tv, resyncronises uploaded_files with tv
        '''
        if not await self.ensure_tv():
            return
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
        read image file, return file binary data, file type, and exif data (if available)
        '''
        try:
            file_type = self.get_file_type(filename)
            keywords = []
            if file_type:
                if file_type == 'jpeg' or file_type == 'jpg':
                    metadata = IPTCInfo(filename)
                    if metadata:
                        keywords = metadata['keywords']
                        # Convert binary keywords to unicode strings if needed
                        if keywords:
                            keywords = [k.decode('utf-8', errors='replace') if isinstance(k, bytes) else str(k) for k in keywords]
                    self.log.info('Keywords: {}'.format(keywords))
                with open(filename, 'rb') as f:
                    file_data = f.read()
            return file_data, file_type, keywords
        except Exception as e:
            self.log.error('Error reading file: {}, {}'.format(filename, e))
        return None, None, None
        
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
        if keywords is not None:
            keywords_json = json.dumps(keywords)
        self.tv_files.pop(filename, None)
        if content_id:
            self.tv_files[filename] = {'content_id': content_id, 'keywords': keywords_json, 'modified':self.get_last_updated(filename)}
        
    async def upload_files(self, filenames):
        '''
        upload files in list to tv
        '''
        if not await self.ensure_tv():
            return
        for filename in filenames:
            path = os.path.join(self.folder, filename)
            file_data, file_type, exif_tags = self.read_file(path)
            if file_data and self.tv.art_mode:
                self.log.info('uploading : {} to tv'.format(filename))
                self.update_uploaded_files(filename, await self.tv.upload(file_data, file_type=file_type, matte=self.matte, portrait_matte=self.matte), exif_tags)
                if self.tv_files.get(filename, {}).get('content_id'):
                    self.log.info('uploaded : {} to tv as {}'.format(filename, self.tv_files[filename]['content_id']))
                else:
                    self.log.warning('file: {} failed to upload'.format(filename))
                self.write_program_data()
            
    async def delete_files_from_tv(self, content_ids):
        '''
        remove files from tv if tv is in art mode
        '''
        if not await self.ensure_tv():
            return
        if self.tv.art_mode:
            self.log.info('removing files from tv : {}'.format(content_ids))
            await self.tv.delete_list(content_ids)
            await self.sync_file_list()

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
            
    async def add_files(self, files):
        '''
        if new files found, upload to tv
        '''
        new_files = [f for f in files if f not in self.tv_files.keys()]
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
        modified_files = [f for f in files if f in self.tv_files.keys() and self.tv_files[f].get('modified') != self.get_last_updated(f)]
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
        
    async def scan_and_sync_dir(self):
        '''
        scan folder for new, deleted or updated files, but only when tv is in art mode
        '''
        try:
            if not await self.ensure_tv():
                self.log.info('tv not reachable; skipping sync')
                return
            if await self.tv.in_artmode():
                self.log.info('checking directory: {}'.format(self.folder))
                files = self.get_folder_files()
                await self.sync_file_list()
                await self.remove_files(files)
                await self.add_files(files)
                await self.update_files(files)
            else:
                self.log.info('artmode or tv is off')
        except Exception as e:
            self.log.warning("error in check_dir: {}".format(e))


    def setup_routes(self):
        
        @self.app.post("/sync_files")
        async def sync_files():
            if not hasattr(self, '_sync_lock'):
                self._sync_lock = asyncio.Lock()     
            async with self._sync_lock:
                try:
                    await self.scan_and_sync_dir()
                    return {"status": "success", "files": self.tv_files}
                except Exception as e:
                    return {"status": "error", "error": str(e)}

        @self.app.post("/refresh_files")
        async def refresh_files():
            try:
                await self.check_file_dict_availability(reload=True)
                return {"status": "success", "files": self.tv_files}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @self.app.post("/show_image")
        async def show_image(req: self.ShowImageRequest):
            if not await self.ensure_tv():
                return {"status": "error", "error": "TV is not reachable"}
            if not await self.tv.in_artmode():
                return {"status": "error", "error": "TV is not in art mode"}
            if not await self.select_image(req.content_id):
                raise RuntimeError(f"Failed to display content_id {req.content_id}")
            return {"status": "success", "shown": req.content_id}

        @self.app.post("/show_file")
        async def show_file(req: self.ShowFileRequest):
            if not await self.ensure_tv():
                return {"status": "error", "error": "TV is not reachable"}
            if not await self.tv.in_artmode():
                return {"status": "error", "error": "TV is not in art mode"}
            content_id = await self.get_content_id(req.file_name)
            if not content_id:
                return {"status": "error", "error": "Content ID not found"}
            if not await self.select_image(content_id):
                return {"status": "error", "error": "Failed to display content ID"}
            return {"status": "success", "shown": content_id}

        @self.app.post("/initialize_slideshow")
        async def initialize_slideshow(req: self.SlideshowInitializeRequest):
            slideshow_keywords = req.keyword_list
            if not slideshow_keywords:
                return {"status": "error", "error": "No slideshow keywords to show"}
            await self.check_file_dict_availability(reload=True)
            if self.tv_files:
                self.slideshow_files = {}
                # Filter files based on keywords
                for k, v in self.tv_files.items():
                    if v.get("keywords") and any(keyword in v["keywords"] for keyword in slideshow_keywords):
                        self.slideshow_files[k] = v
                # self.slideshow_files = {k: v for k, v in self.tv_files.items() if k in slideshow_filesnames}
            if not self.slideshow_files:
                return {"status": "error", "error": "No valid slideshow files found"}
            if not isinstance(req.slideshow_index, int):
                return {"status": "error", "error": "Slideshow index must be an integer"}
            self.slideshow_index = req.slideshow_index
            if self.slideshow_index < 0 or self.slideshow_index >= len(self.slideshow_files):
                self.slideshow_index = 0
            return {"status": "success", "slideshow": self.slideshow_files, "index": self.slideshow_index}

        @self.app.post("/show_slideshow_next")
        async def show_slideshow_next():
            if not await self.ensure_tv():
                return {"status": "error", "error": "TV is not reachable"}
            if not await self.tv.in_artmode():
                return {"status": "error", "error": "TV is not in art mode"}
            if not self.slideshow_files:
                return {"status": "error", "error": "No slideshow files to show"}
            content_id = list(self.slideshow_files.values())[self.slideshow_index].get("content_id")
            if not content_id:
                return {"status": "error", "error": "No content ID found for the file"}
#            file = list(self.slideshow_files.keys())[self.slideshow_index]
#            content_id = await self.get_content_id(file)
#            if not content_id:
#                return {"status": "error", "error": "No content ID found for the file"}
            if not await self.select_image(content_id):
                return {"status": "error", "error": "Failed to display content ID"}
            self.slideshow_index = (self.slideshow_index + 1) % len(self.slideshow_files)
            return {"status": "success", "shown": content_id}

        @self.app.post("/show_slideshow_previous")
        async def show_slideshow_previous():
            if not await self.ensure_tv():
                return {"status": "error", "error": "TV is not reachable"}
            if not await self.tv.in_artmode():
                return {"status": "error", "error": "TV is not in art mode"}
            if not self.slideshow_files:
                return {"status": "error", "error": "No slideshow files to show"}
            self.slideshow_index = (self.slideshow_index - 1) % len(self.slideshow_files)
            content_id = list(self.slideshow_files.values())[self.slideshow_index].get("content_id")
            if not content_id:
                return {"status": "error", "error": "No content ID found for the file"}
#            file = list(self.slideshow_files.keys())[self.slideshow_index]
#            content_id = await self.get_content_id(file)
#            if not content_id:
#                return {"status": "error", "error": "No content ID found for the file"}
            if not await self.select_image(content_id):
                return {"status": "error", "error": "Failed to display content ID"}
            return {"status": "success", "shown": content_id}
        
        @self.app.post("/show_slideshow_index")
        async def show_slideshow_index(req: self.SlideshowIndexRequest):
            if not await self.ensure_tv():
                return {"status": "error", "error": "TV is not reachable"}
            if not await self.tv.in_artmode():
                return {"status": "error", "error": "TV is not in art mode"}
            if not self.slideshow_files:
                return {"status": "error", "error": "No slideshow files to show"}
            if not isinstance(req.slideshow_index, int):
                return {"status": "error", "error": "Slideshow index must be an integer"}
            if req.slideshow_index < 0 or req.slideshow_index >= len(self.slideshow_files):
                return {"status": "error", "error": "Slideshow index out of range"}
            self.slideshow_index = req.slideshow_index
            content_id = list(self.slideshow_files.values())[self.slideshow_index].get("content_id")
            if not content_id:
                return {"status": "error", "error": "No content ID found for the file"}
#            file = list(self.slideshow_files.keys())[self.slideshow_index]
#            content_id = await self.get_content_id(file)
#            if not content_id:
#                return {"status": "error", "error": "No content ID found for the file"}
            if not await self.select_image(content_id):
                return {"status": "error", "error": "Failed to display content ID"}
            return {"status": "success", "shown": content_id}

        @self.app.get("/available_keywords")
        async def available_keywords():
            try:
                await self.check_file_dict_availability(reload=True)
                keywords_set = set()
                for entry in self.tv_files.values():
                    if "keywords" in entry and entry["keywords"]:
                        try:
                            kws = json.loads(entry["keywords"])
                            if isinstance(kws, list):
                                keywords_set.update(kws)
                        except Exception:
                            continue
                return {"status": "success", "keywords": sorted(keywords_set)}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @self.app.get("/get_artmode_settings")
        async def get_artmode_settings():
            try:
                if not await self.ensure_tv():
                    return {"status": "error", "error": "TV is not reachable"}
                settings = await self.tv.get_artmode_settings('')
                if not settings:
                    return {"status": "error", "error": "Failed to retrieve art mode settings"}
                return {"status": "success", "settings": settings}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @self.app.post("/connect")
        async def connect():
            ok = await self.ensure_tv()
            if not ok:
                detail = self.tv_connect_last_error or "TV is not reachable"
                return {"status": "error", "error": detail}
            return {"status": "success", "message": "Connected to TV"}

# Instantiate the controller
controller = TheFrameController()
app = controller.app
