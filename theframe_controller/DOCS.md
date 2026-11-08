# The Frame Controller

The add-on is the engine for a Samsung The Frame in Art Mode. It holds the connection to the TV, keeps a folder of images in sync with the TV's *My Photos*, runs the slideshow and its timer, and offers all of this as an HTTP API on port 5000.

Install the **The Frame Controller** integration (same repository, via HACS) to use it from Home Assistant: switches for the slideshow and each keyword, the slideshow interval, a live image of what the TV shows, sync and next/previous buttons, the media browser and Apple Home. The integration does no TV work itself; it mirrors and changes the add-on's state.

## Installation

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add `https://github.com/exploramus/theframe-integration`.
2. Install **The Frame Controller**, enter at least `tv_ip` on the **Configuration** tab, and start the add-on.
3. On the first connection the TV asks to allow a new device: press **Allow** with the remote. Until then the add-on log shows `did not accept the connection (ms.channel.timeOut)`.
4. Add the integration (**Settings → Devices & Services → Add Integration → The Frame Controller**) with the add-on URL, usually `http://homeassistant.local:5000`.

The add-on starts and answers at once even when the TV is off. It never switches the TV on by itself.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `tv_ip` | — (required) | IP address of the TV. Give the TV a fixed address in your router. |
| `tv_mac` | — | MAC address of the TV, only needed for Wake-on-LAN (`POST /api/v1/power_on`). |
| `token_file` | `/media/TheFrame/tv-token.txt` | Where the TV's access token is stored. |
| `image_path` | `/media/TheFrameImages/` | Folder with the images to show (JPEG, PNG). |
| `json_path` | `/media/TheFrame/uploaded_files.json` | Record of what the add-on uploaded: file name, TV content ID, keywords. |
| `matte` | `none` | Matte for uploaded images, e.g. `none`, `modern_polar`, `shadowbox_black`. |
| `ip_control` | `false` | Use Samsung IP Control for the power state and switching the TV on (see below). |
| `ip_control_token_file` | next to `token_file` | Where the IP Control pairing token is stored. |
| `place_names` | `false` | Look up place names for the images' GPS positions (see *Image information*). |

> **Keep `json_path` and `image_path` together.** A sync deletes from the TV every image recorded in `json_path` whose file is missing in `image_path`. Never point a second installation (or a test setup) at the same `json_path` with a different folder. Images you put on the TV in other ways are never touched.

Slideshow settings are kept in the add-on's own storage (`/data/state.json`) and survive restarts and updates.

## How it works

### TV connection and real-time status

The add-on keeps the TV's art connection open and reacts to the events the TV pushes: Art Mode on/off, a different image, standby and wake-up. These changes reach Home Assistant within about a second. A regular check of the power state runs as a safety net: every 30 seconds while the event connection is open, every 10 seconds otherwise, and backing off to 30 seconds while the TV does not answer at all (for example when it is unplugged).

The power state is one of `off` (not reachable), `standby`, `tv` (on, watching TV or an input), `art` (Art Mode) or `on` (on, Art Mode unknown). Commands that need Art Mode, such as showing an image, are refused at once otherwise instead of waiting for the TV.

### Image sync

A sync compares `image_path` with what the add-on uploaded and

- uploads new images,
- re-uploads images whose file changed (the old version is deleted from the TV),
- deletes images whose file was removed.

A sync runs as a background job, one at a time. If the TV is not in Art Mode the job waits (`queued`) and starts as soon as the TV enters Art Mode. A sync that fails, for example because the connection dropped, or that is cut short because the TV left Art Mode, stays queued and is retried. If the TV adds an image without confirming the upload, the add-on finds it on the TV instead of uploading it twice.

Start a sync with the integration's **Sync Files** button or the `theframe.sync_files` action.

### Keywords

Slideshows select images by keyword. Keywords are read from the images' metadata: IPTC keywords or XMP `dc:subject` (what macOS Photos, Lightroom and most photo tools write when exporting). PNG files can be shown but carry no keywords. Tag images before they reach `image_path`; changed keywords are picked up when the file is re-uploaded after a change.

### Slideshow

The add-on owns the slideshow and its timer:

- **Keyword mode** shows every uploaded image that has any of the selected keywords.
- **Playlist mode** shows an ordered list of file names (the integration's `theframe.play_files` action).

**Collections** are named presets of either kind: a *manual* collection (an ordered list of files) or a *keyword* collection. Loading a collection makes it the active one, starting at its first image. The slideshow then plays the collection as it is: if the collection is changed while it plays, the slideshow continues after the image it showed last, in the new order; if that image was removed, it continues with the image that followed it. Changing keywords or files directly leaves the collection. Files in a manual collection that are no longer on the TV stay in the collection, are skipped and reported as `missing`. The integration shows the collections as one select.

Each collection has a stable ID, a revision number that every change increases, and the time and user of the last change. Changes are applied one at a time, so several people can edit collections at once: moves and inserts refer to a neighbouring file name, not a position, and combine with each other's edits. Every change is logged in `/data/history.json` (the last 100 changes per collection and everything from the last 30 days), so it can be undone, a collection can be returned to an earlier revision, and deleted collections can be restored.

Interval, on/off, mode, keywords or playlist, the active collection, the collections and the image shown last are saved and survive restarts. The slideshow stores file names and looks up the TV's content ID when it shows an image, so re-uploading a changed file does not break it. The timer pauses outside Art Mode and while a sync is running.

Version 1.6.0 converts the stored slideshow state once on its first start; the previous file is kept as `/data/state.json.v1`.

### Image information

The TV keeps no title or description for your own images; it only stores a date with each upload. The add-on therefore reads the information from the image files and offers it to Home Assistant:

- **Title** (XMP `dc:title`) and **description** (XMP `dc:description` or EXIF `ImageDescription`): Photos writes these when *Titles, keywords and captions* is ticked on export.
- **Capture time** (EXIF `DateTimeOriginal` with time zone). It is also sent to the TV with the upload, so the TV records when a photo was taken instead of when it was uploaded. Images uploaded before 1.5.0 keep their upload date on the TV until they are uploaded again.
- **GPS position** (EXIF), if the export includes location information.
- **Place name**, only with `place_names: true`: looked up from the GPS position with [OpenStreetMap Nominatim](https://nominatim.org) (for example `Hamburg, Deutschland`), once per place, rounded to about 100 m, at most one request per second. This sends the positions of your images to OpenStreetMap; with the option off, nothing leaves your network. Failed lookups are retried after the next sync.

The integration shows these as attributes of `sensor.the_frame_current_image`.

### Collections editor

**The Frame** in Home Assistant's sidebar opens the collections editor (through Ingress, with Home Assistant's login; also in the companion app). It is open to all Home Assistant users, not only administrators; every change is logged with the user's name and can be undone or restored.

- **Collections** on the left: create (manual or keywords), rename, duplicate, delete. Deleted collections stay under *Recently deleted* and can be restored. *Export* downloads all collections as JSON, *Import* adds them again.
- **Images** in the middle, with search by text (title, description, file name), keywords, place and capture date. In a manual collection, clicking an image adds or removes it; Shift-click does the same for a range; *Add all results* adds everything the search shows. ▶ TV shows an image on the TV at once.
- **Order** at the bottom (manual collections): drag ⠿ to reorder, or select images and use *Move to start* / *Move to end*; Alt+← / Alt+→ moves the focused image one step; *Sort once by* capture time, title or file name. New images are added after the selected image in the strip, otherwise at the end.
- **Keyword collections** show the keywords to choose instead of the order strip.
- **Play on TV** starts the slideshow with the collection; **Undo** reverts your own last change; **History** lists the changes and restores any earlier revision.

Several people can edit at the same time: everyone sees the others' changes within a second. Edits inside a collection are shown at once and saved in the background; if the connection drops, they are kept in the browser and sent when it is back (the badge at the top shows *Offline – n changes waiting*). Creating, renaming and deleting collections need a connection.

### Waking the TV and IP Control

`POST /api/v1/power_on` switches the TV on: with Samsung IP Control if it is paired, otherwise with Wake-on-LAN to `tv_mac`. The add-on runs on the host network so Wake-on-LAN broadcasts reach the TV.

IP Control (Samsung's own control interface, port 1516) is optional and the most reliable source for the power state. To use it, set `ip_control: true`, restart the add-on, call `POST /api/v1/ip_control/pair` (for example from **http://homeassistant.local:5000/docs**) and press **Allow** on the TV within 30 seconds.

### Images from OneDrive (or another cloud)

The add-on syncs a local folder with the TV; getting images into that folder is up to you. This repository includes `image_sync.sh`, which mirrors `OneDrive:ImageCollection` into `/media/TheFrameImages` with [rclone](https://rclone.org) and prints `changed` or `unchanged`. It expects rclone at `/config/scripts/rclone` and its configuration at `/config/scripts/rclone.conf` with a remote named `OneDrive`.

Run it from Home Assistant as a shell command and sync the TV only when something changed:

```yaml
# configuration.yaml
shell_command:
  image_sync: bash /config/scripts/image_sync.sh
```

```yaml
# automation
alias: The Frame – sync images
triggers:
  - trigger: time_pattern
    minutes: /15
actions:
  - action: shell_command.image_sync
    response_variable: rclone
  - if:
      - condition: template
        value_template: "{{ rclone.stdout | trim == 'changed' }}"
    then:
      - action: theframe.sync_files
mode: single
max_exceeded: silent
```

## Troubleshooting

`http://homeassistant.local:5000/api/v1/status` shows what the add-on knows, without asking the TV:

| Field | Meaning |
| --- | --- |
| `power_state` | `off`, `standby`, `tv`, `art`, `on`, or `unknown` before the first check |
| `current_content_id`, `current_file` | Image shown in Art Mode |
| `job` | The current or last sync: `state` (`queued`, `running`, `done`), `total`, `done`, `attempts`, `error` |
| `slideshow` | Slideshow settings, the active `collection` (name and `collection_id`), the image shown last (`current_file`, `position`) and the number of images (`count`) |
| `collections` | Names of the saved collections |
| `tv_events` | `true` while the TV's event connection is open |
| `event_clients` | Connected event stream clients; `1` when the integration is connected, `0` means it falls back to polling every 60 seconds |
| `art_service_available` | `false` while the TV's art service stopped answering and requests pause briefly |
| `ip_control` | `disabled`, `unpaired` or `paired` |
| `error` | Last problem reaching the TV |

Useful lines in the add-on log:

- `TV power state: standby -> art` — every power state change.
- `did not accept the connection (ms.channel.timeOut)` — press **Allow** on the TV, or check the token file.
- `slideshow changed via …: {...}` — every slideshow change and which endpoint made it.
- `sync … failed, retrying in art mode` — a sync will be retried.
- `event stream client connected (1 open)` — the integration is connected.

## API

The integration uses the versioned API under `/api/v1`. Its full description is the OpenAPI document at `/openapi.json`, with an interactive version at `/docs`.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/status` | Power state, current image, slideshow, current sync job |
| `GET /api/v1/events` | Server-sent events: `status` (on connect and whenever it changes), `files` (after a sync), `collection` (after every collection change, with the change, collection, revision and user) |
| `GET /api/v1/files`, `GET /api/v1/keywords` | Uploaded files with keywords, `title`, `description`, `taken`, `place`, `latitude`, `longitude`; all keywords |
| `POST /api/v1/sync` | Start a sync job: `202` with the job; `409` while one is queued or running |
| `POST /api/v1/show` | Show `{"file_name": "..."}` or `{"content_id": "..."}` |
| `GET`, `PUT /api/v1/slideshow` | Slideshow settings: `enabled`, `interval_minutes`, `mode` (`keywords` or `files`), `keywords`, `files`, `position`; `{"collection": "name"}` loads a collection |
| `GET /api/v1/collections` | Collections with `id`, `kind` (`manual` or `keywords`), keywords or files, `missing` files, the number of images, `revision`, `updated`, `updated_by` |
| `PUT`, `DELETE /api/v1/collections/{name}` | Save a collection (`{"keywords": [...]}`, `{"files": [...]}`, or `{}` for the current slideshow); saving under an existing name keeps its ID; delete one |
| `POST /api/v1/collections/changes` | Apply one change: `{"id": "<change ID>", "change": "...", "collection": "<collection ID>", ...}` with `create` (`name`, `kind`, `files` or `keywords`), `add` (`files`, optional `after`), `remove` (`files`), `move` (`files`, `after`), `set_keywords`, `rename` (`name`), `delete`. `after` is the file to insert after, `null` for the start, left out for the end. A change ID sent again is not applied twice |
| `GET /api/v1/collections/{id}/history` | Changes of one collection, newest first |
| `POST /api/v1/collections/{id}/undo` | Undo your own last change to the collection |
| `POST /api/v1/collections/{id}/restore` | Return a collection to a revision (`{"revision": n}`), also a deleted one |
| `GET /api/v1/collections/deleted` | Deleted collections that can still be restored |
| `GET /api/v1/collections/export`, `POST /api/v1/collections/import` | All collections as JSON; import adds them, a taken name gets a number |
| `POST /api/v1/slideshow/next`, `/previous`, `/show` | Step through the slideshow, or show `{"position": n}` |
| `GET /api/v1/settings` | The TV's Art Mode settings (brightness, colour temperature, motion sensor) |
| `POST /api/v1/power_on` | Switch the TV on (IP Control or Wake-on-LAN) |
| `POST /api/v1/ip_control/pair` | Pair IP Control |
| `GET /api/v1/thumbnail/{file}?size=320` | JPEG thumbnail of an image file |
| `POST /api/v1/reconcile` | Recover content IDs missing from `json_path` by comparing the TV's thumbnails with the image files (slow; only needed after `json_path` was lost) |

Errors use HTTP status codes with a body `{"error": {"code": "...", "message": "..."}}`. Codes: `not_configured` (503); `tv_not_in_art_mode`, `tv_not_on`, `job_conflict`, `empty_slideshow`, `wake_unavailable`, `ip_control_disabled`, `name_taken`, `collection_deleted`, `nothing_to_undo` (409); `not_found` (404); `invalid_request`, `invalid_image`, `invalid_change` (422); `not_available` (501); `tv_error` (502).

The unversioned endpoints of versions before 1.4.0 (`/status`, `/sync_files`, `/show_file`, `/initialize_slideshow`, …) are gone; use the integration's entities and actions, or `/api/v1`.

## Development

The add-on runs on a Mac or PC too. It reads its options from the file named in `THEFRAME_OPTIONS` (default `/data/options.json`):

```bash
THEFRAME_OPTIONS=dev-options.json uvicorn theframe:app --app-dir theframe_controller/app --port 5000
```

Use your own token file, image folder and `json_path` for this (see the warning under **Options**). Tests, CI and the release process are described in the repository's README.
