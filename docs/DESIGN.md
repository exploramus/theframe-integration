# TheFrame – Architecture & Development Environment Design

Status: agreed design, 2026-10-31 (rev. 2: samsungframetv 4.1.4 reviewed; rev. 3, 2026-11-01: image sources stay outside, decision 6; rev. 4, 2026-11-08: collections editor, decision 7 and section 3.8). Basis: code review of `exploramus/theframe-controller` (add-on, v1.0.17.001) and `exploramus/theframe-integration` (integration, v0.1.0), GitHub state of 2026-05-04.

## 1. Decisions

1. **Keep add-on and integration separate**, but version and release them together from **one repository**.
   - Reasons: process isolation for long-running jobs (sync, uploads), independent lifecycle (HA restarts don't kill jobs), own dependency set (no conflicts with HA core pins, e.g. core's `samsungtvws`), and the add-on is testable without HA.
2. **Add-on = engine.** Owns all TV and data work: TV connection, power state, WOL, sync, keyword index, slideshow state and timer, playlists, later the playlist editor (as the add-on's own web UI via Ingress).
3. **Integration = thin HA adapter.** Entities, services, media browser, HomeKit/Apple Home exposure. No TV access, no heavy work, no own copy of slideshow state.
4. **Build the add-on on `samsungframetv` 4.1.x** (NickWaterton/samsung-tv-ws-api, published on PyPI since 4.0.0). It is the foundation of the TV monitor, not a late migration step. Details in section 3.0.
5. **No HAOS staging for daily work.** The real risk is the data on the TV; it is addressed by safety rules in code and a fake TV. A HAOS VM (Synology VMM or UTM on the Mac) is used only once per add-on release to verify Supervisor-specific behaviour (Ingress, host network, options, `/media` mapping).
6. **Image sources stay outside** (decided 2026-11-01). The add-on's contract is a local folder (`image_path`); how images get there is not part of the add-on or the integration, and no cloud sync is built in, not even a generic rclone step. Reasons: OneDrive is one of many possible sources; existing tools already cover them (rclone for 70+ cloud providers, the Samba share add-on, Syncthing, a Photos export from a Mac); keeping sources out keeps the add-on small and general. Today an HA automation runs `image_sync.sh` (rclone) and starts a TV sync only when it reports `changed`; DOCS.md shows it as a recipe.
   - If convenience is wanted later: one generic step (rclone bundled in the image, options for remote and schedule) rather than a plugin architecture; plugins only for sources that are not files.
   - iCloud shared albums: readable without login when "Public Website" is on, through undocumented web endpoints; but Apple stores shared-album photos at reduced resolution (about 2048 px long edge, to be confirmed) against the TV's 3840 × 2160, keywords are probably lost, and the album is public via its link. The full iCloud library needs an Apple ID login with 2FA that has to be renewed. Only worth a test with a real album before any decision.

7. **Collections editor in the add-on, served through Ingress** (decided 2026-11-08). The add-on owns the collections and the image metadata, so the editor talks to the add-on's API directly: no proxy layer in the integration and no second copy of the data. Home Assistant shows it in the sidebar with its own login, also in the companion app. Rejected: an integration panel (`panel_custom`), which would need integration WebSocket commands for every editor call and an HA restart for UI changes; a Lovelace card, which needs its own HACS frontend package and is cramped for hundreds of thumbnails. Design in section 3.8.

## 2. Findings from the code review

### Add-on startup and TV state
- Constructor runs at import time: sends WOL 4× with blocking `time.sleep(5)` (~20 s before the server listens), wakes the TV on every add-on restart, `sys.exit` on config problems.
- No timeouts on TV calls (except `available(timeout=10)`); `tv_connect_timeout` / `tv_connect_interval` are defined in `config.yaml` but never used.
- Nearly every endpoint calls `tv.in_artmode()` first → hangs when the TV is off or in standby. The integration's first `/status` then times out (20 s) and the config entry goes "not ready".
- `host_network: true` is missing in `config.yaml` → WOL broadcasts stay inside the Docker network. Likely cause of non-working WOL.

### Interaction integration ↔ add-on
- `/sync_files` runs the whole sync inside one HTTP request (minutes for many images); the integration times out after 20 s while the sync continues; a second call waits on the lock and times out too.
- Slideshow stores **content IDs**; a re-upload of a modified image creates a new content ID → slideshow points at a deleted item.
- Slideshow ticks and sync share the TV connection without coordination.
- State lives in memory on both sides and diverges after restarts: add-on loses `slideshow_files`/index; integration loses `active_keywords`, `show_enabled`, `interval_minutes`.
- Errors: mostly HTTP 200 + `{"status":"error"}`, some 500s (e.g. `show_image` raises). The integration can't distinguish "TV off" from "broken".
- `/sync_files` deletes TV uploads for every entry in `uploaded_files.json` whose file is missing in the image folder → a dev instance must never use production's JSON with a different folder.

### Housekeeping
- No tests, no CI in either repo. `__pycache__/` and `.DS_Store` committed.
- README/manifest links point to `tilo-boehmann/…` instead of `exploramus/…`.
- Add-on is built on the Pi from the Dockerfile (no prebuilt image); `samsungtvws` installed from a git URL.
- `image_sync.sh` (rclone/OneDrive) lives in the add-on repo but is run by HA Core via `shell_command`.

## 3. Target design

### 3.0 Library: `samsungframetv` 4.1.x

Checked against version 4.1.4 (released 2026-10-26; 4.0.0 on 2026-10-25).

**Urgent – the current add-on breaks on its next rebuild.** `requirements.txt` installs `samsungtvws @ git+https://github.com/NickWaterton/samsung-tv-ws-api` from the branch head. Since 4.0.0 that repository builds a package named `samsungframetv`, so pip rejects the install (name mismatch) and the module `samsungtvws` no longer exists. Any rebuild or reinstall of the add-on (e.g. on a new Pi, after a restore, or a version bump) fails. Fix as a standalone hotfix release before anything else: depend on `samsungframetv==4.1.4` from PyPI and change the imports to `from samsungframetv.async_art import SamsungTVAsyncArt`. The API is otherwise unchanged. Do not use the `samsungframetv-compat` shim; it exists for code that cannot be changed.

**Package facts.** PyPI name `samsungframetv`, import `samsungframetv`, Python ≥ 3.10 (add-on uses 3.11), LGPL-3.0. Dependencies: `websocket-client`, `requests`, `aiohttp`, `websockets>=13`, `async_timeout`; async support is included by default. Pin an exact version; the library is moving fast (six releases in two days).

**What 4.x provides for this design:**

| Library feature | Used for |
|---|---|
| `get_power_state()` → `off` / `standby` / `tv` / `art` / `on` (combines REST `PowerState`, the art channel and optional IP Control) | Core of the TV monitor (3.1) |
| `in_artmode()` checks REST `PowerState` first and only then opens the art channel; handles 2025 Frames reporting `standby` while in art mode | Removes the main cause of hanging requests when the TV is off |
| Built-in timeouts: connection open 10 s, art requests 5 s, raising `ArtRequestTimeoutError` (still an `AssertionError`); automatic reconnect | Replaces the missing timeouts; the unused `tv_connect_*` options become obsolete or map onto these |
| Art-service back-off: after repeated timeouts requests fail fast for 30 s, doubling up to 240 s; `art_service_available`, `reset_art_service()` | Monitor reads the back-off state and calls `reset_art_service()` when the TV comes back on |
| Samsung **IP Control** (port 1516, one-time pairing with its own token): reliable power state, `power_on()`, `power_off()`, `set_artmode()` | Most reliable state source; possible alternative to WOL for waking the TV. Must be tested on this TV model and its network-standby setting. Optional; the design works without it |
| `upload()` matches the TV's confirmation to the right upload and waits 30 s; returns `None` if unconfirmed ("may still be added") | Sync must re-read the TV's list after unconfirmed uploads to avoid duplicates or orphaned content IDs |
| `ThumbnailUnavailableError` | Explicit handling in thumbnail reconciliation |
| Art settings: brightness, colour temperature, motion timer/sensitivity, brightness sensor, matte and photo filters, TV-native slideshow status | Future entities (e.g. brightness as a number entity); our own slideshow logic stays |
| Bundled `diagnose` tool | Run once against this TV to record model year, art API version and supported features |
| Test suite with recorded fixtures (2018 Frame, uploads, timeouts) | Reference and starting point for our fake TV |

**Caveats:**
- The `SamsungTVAsyncArt` constructor does blocking network I/O: a synchronous REST call for the model year and, on 2024+ models without a token, a synchronous websocket open. Create the client in a worker thread inside the monitor, never at import time or directly on the event loop.
- WOL stays as fallback (needs `host_network: true`); IP Control `power_on()` is preferred where it works.

**Diagnose results for this TV** (`python -m samsungframetv.diagnose <tv-ip> --ipcontrol`, samsungframetv 4.1.4, 2026-10-31, read-only run, TV on in TV mode, not art mode):

| Item | Result |
|---|---|
| Model | GQ55LS03BGUXZG, `22_PONTUSM_FTV` → model year 2022; Tizen, wired, 3840×2160 |
| Art API | 4.3.4.0 (new API, `legacy_art_api` false) |
| REST | `PowerState` reported (`on`); `TokenAuthSupport` true |
| Power state | `get_power_state()` → `tv`; `get_artmode` → `off`; `in_artmode()` → false |
| Art channel | All art requests answered, typically 0.01–0.2 s; thumbnail 1.5 s (JPEG, ~25 KB) |
| Content | 373 items: `MY-C0008` 185, `MY-C0002` 183, `MY-C0004` 5 |
| Device features | brightness sensor, motion sensor, colour tone: yes; My Shelf: no |
| Art settings | brightness 4 (0–10), colour temperature 0 (−5…5), motion sensitivity 2 (1–3), motion timer 15 (off/5/15/30/60/120/240), brightness sensor on |
| Mattes / filters | 10 matte types (none … squares) with colours; 7 photo filters (None, Aqua, ArtDeco, Ink, Wash, Pastel, Feuve) |
| TV-native slideshow | off |
| IP Control (1516) | Port open; pairing works after pressing Allow on the TV (token stored); `get_tv_states` answers in 0.5 s |

Notes:
- The websocket connection is refused (`ms.channel.timeOut`) until Allow is pressed on the TV; the first run failed this way. No websocket token file was written after Allow, so this 2022 model appears to remember the client by name. The add-on's existing token file still works; re-check when a dev client name is used.
- IP Control pairing needs someone at the TV as well; without Allow the pairing request times out silently.
- Not tested (would change TV state): IP Control `power_on()` / `power_off()` / `set_artmode()`, WOL, `upload()`, and power-state behaviour in standby and art mode. Test these in step 2.
- Full redacted report: `diagnose_report_ipc.json` (not committed).

### 3.1 TV monitor (add-on starts independently of the TV)
- Server starts immediately and always answers.
- Background task tracks TV state with `get_power_state()`: `off` → `standby` → `tv` → `art`; polls with back-off while the TV is off, respects the library's art-service back-off, calls `reset_art_service()` when the TV comes back.
- Optional IP Control pairing as the most reliable state source.
- Endpoints answer status from the cached state; no request ever blocks on the TV.
- All TV calls rely on the library's timeouts, with an outer `asyncio.timeout` as a safety net.
- Waking the TV (IP Control `power_on()`, else WOL) only on explicit request, never at startup.

- **TV push events** (add-on 1.3.1): recorded on our 2022 Frame, the art channel pushes `art_mode_changed` (`status` on/off), `image_selected` (`content_id`; also sent in standby, ignored there), `go_to_standby` and `wakeup` (plus `recently_set_updated`, ignored). The connection stayed open through standby. The monitor registers callbacks for these and updates the cached state at once; checks remain as a safety net (30 s while the art channel is open, 10 s otherwise, back-off to 30 s while the TV does not answer). Measured with the real TV: changes reach `/api/v1/events` within about a second.

### 3.2 Pending intents
- Commands needing art mode either return a clear error (409/503) or are accepted and queued.
- Optional `wake: true`: send WOL, wait for art mode, then execute.
- Generalises today's `_sync_pending` mechanism.

### 3.3 Background jobs
- `POST /api/v1/sync` → `202` + job ID. Progress/results via `/status` and events.
- One job at a time; slideshow pauses during uploads.

### 3.4 State ownership and persistence
- Add-on owns slideshow state (mode keyword|playlist, keywords or file list, index, interval, on/off) and the slideshow timer; persisted in `/data`.
- Slideshow stores **file names**, resolves content IDs at display time.
- Integration mirrors add-on state; its switch/number entities only set it.

### 3.5 Event stream
- Integration holds one long-lived connection to `/api/v1/events` (SSE or WebSocket): art-mode changes, job progress, sync results.
- Polling only as fallback; automatic reconnect on either side restarting.

### 3.6 API contract
- Versioned `/api/v1/...`, defined error codes (TV unavailable, job conflict, invalid request).
- FastAPI's OpenAPI output is the contract; CI checks the integration client against it.

### 3.7 TV data safety
- Delete only content IDs the add-on uploaded and recorded.
- Dry-run mode for sync (default on in dev).
- Snapshot of the TV's "My Photos" list before destructive operations.
- Separate dev token; pause production slideshow during live tests.

### 3.8 Collections editor

**Requirements.**

1. Works within Home Assistant.
2. Select and unselect images for a collection.
3. Add, change, delete and rename collections.
4. Search images by keywords, place, title, capture time and other metadata.
5. Collection changes reach Apple Home.
6. Manual order of the images in a collection.
7. Robust: no data or metadata at risk when Home Assistant fails or restarts, when connections drop, or when several users edit at the same time.

**Today (1.5.0).** Collections live in the add-on's `/data/state.json` next to the slideshow, keyed by name: `{"mode": "keywords", "keywords": [...]}` or `{"mode": "files", "files": [...]}`. Loading a collection copies its keywords or file list into the slideshow; the slideshow remembers its position as an index. `PUT`/`DELETE /api/v1/collections/{name}` replace or remove a whole collection; there is no rename, no revision and no history. Writes are atomic (temporary file, `os.replace`).

#### Data model

- **Two kinds:** *manual* collections (an ordered list of file names; the stored order is the order on the TV) and *keyword* collections, which stay as they are: their images are whatever carries the keywords, in the add-on's order, with no sort rule and no manual order.
- **Stable IDs:** every collection gets an internal ID (`uuid4`) next to its name. Changes, the slideshow and the history refer to the ID; a rename by one user does not interrupt another user's editing or a playing slideshow. The integration's select and Apple Home keep showing names.
- **Revision:** each collection has a revision number, increased by every change, plus `updated` (time) and `updated_by` (user).
- **Slideshow refers to the collection.** Loading a collection stores its ID in the slideshow instead of copying its list, and the slideshow remembers the **current file name** instead of an index. If someone reorders or edits the collection while it plays, the slideshow continues from the same image in the new order. If that image was removed, it continues with the image that followed it. Changing keywords or files by hand still leaves the collection, as today.
- **Missing files are kept, not dropped.** A file name in a manual collection whose file is no longer uploaded stays in the list and is reported (`missing`). The slideshow skips it; the editor shows it greyed out with *Remove from collection*.
- **Migration:** existing collections get IDs and revision 1 on the first start of the new version; `files` collections become manual, `keywords` collections stay keyword collections. The slideshow's index is converted to the file name it points at.

#### Changes instead of whole-collection saves

The editor sends each action as it happens; the add-on applies one change at a time to the current state, writes it atomically and announces it on the event stream. There is no draft and no *Save* button.

| Change | Fields | Applied as |
| --- | --- | --- |
| `create` | name, kind, files or keywords | new collection; refused if the name is taken |
| `add` | files, optional `after` | inserted after the anchor file, or at the end; files already in the collection are skipped |
| `remove` | files | removed where they are |
| `move` | files, `after` (file name, `null` for the start, left out for the end) | moved as a block after the anchor, in the order given; an anchor among the moved files counts as left out |
| `set_keywords` | keywords | keyword collections only |
| `rename` | name | refused if the name is taken |
| `delete` | — | removed; restorable from the history |

- **Anchors are file names, not positions.** "After Y" stays correct when someone else has meanwhile inserted or moved images in front of it. If the anchor is gone, the files go to the end. Most concurrent edits therefore combine without a conflict: two users adding, one adding while the other moves, two moves of different images.
- **Change IDs and retries:** every change carries an ID made by the browser (`uuid4`). The add-on remembers the IDs of the last 1,000 changes with their result; a change sent again (for example after a dropped connection) is answered with the stored result instead of being applied twice.
- **Serialisation:** one `asyncio.Lock` around apply, write and publish. One process owns the state, so no file locking is needed.
- **Real conflicts are shown, not resolved silently:**

| Situation | Behaviour |
| --- | --- |
| Collection deleted while another user edits it | The change is refused (`collection_deleted`); the editor says so and offers *Restore* |
| Name already taken (create, rename) | Refused (`name_taken`) with a message |
| Two renames at the same time | The later one wins; both are in the history |
| `set_keywords` from two users | The later one wins; both are in the history |

#### History, undo and restore

- The add-on keeps a log per collection in `/data/history.json`: change, result, time, user, revision. Kept: the last 100 changes per collection and anything younger than 30 days. Written atomically like `state.json`.
- **Undo** reverts the user's own last change by applying its inverse (`add` ↔ `remove`, `move` back after its previous anchor, `rename` back), even if others changed the collection in between. Undo of a `delete` restores the collection with its ID.
- **Restore this version** returns a collection to its state at a revision, as one new change (so it can be undone too). Deleted collections are listed and restorable while their history is kept.
- **Export and import:** all collections as one JSON file (`GET /api/v1/collections/export`, `POST /api/v1/collections/import`); import adds collections and renames on name clashes, it never overwrites. `/data` is part of every Home Assistant backup as before.

#### Users and access

- Behind Ingress the Supervisor passes the logged-in user (`X-Remote-User-Id`, `X-Remote-User-Name`, `X-Remote-User-Display-Name`). The add-on records it in the history; changes without these headers (integration actions, direct API calls) are recorded as "Home Assistant".
- **Access:** add-on panels are visible to administrators only by default. The panel is opened to all users (`panel_admin: false` in `config.yaml`), so family members can curate; every change is in the history and restorable. Changing this back is a one-line config change.
- Optional presence: the editor shows who else has the same collection open, from the event stream's connected editors.

#### API additions

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/collections` | As today, plus `id`, `kind`, `revision`, `updated`, `updated_by`, `missing` |
| `POST /api/v1/collections/changes` | Apply one change (`{"id", "change", "collection", ...}`); returns the collection with its new revision, or an error |
| `GET /api/v1/collections/{id}/history` | Log of one collection |
| `POST /api/v1/collections/{id}/undo` | Undo the requesting user's last change |
| `POST /api/v1/collections/{id}/restore` | Restore a revision, also for deleted collections |
| `GET /api/v1/collections/export`, `POST /api/v1/collections/import` | Export and import |

- Event stream: new `collection` events (change, collection, revision, user) after every change, so editors and the integration update within a second.
- `PUT`/`DELETE /api/v1/collections/{name}` and `PUT /api/v1/slideshow {"collection": name}` stay for the integration's actions (`save_collection`, `delete_collection`, the select); they are applied through the same change path, so they are serialised, logged and announced like editor changes.
- New error codes: `name_taken`, `collection_deleted` (409); `invalid_change` (422).

#### Editor (Ingress panel)

- **Layout:** list of collections on the left (image count, *New*, *Rename*, *Duplicate*, *Delete*); thumbnail grid in the middle; for manual collections a filmstrip with the collection's order below the grid.
- **Selecting:** click adds an image to or removes it from the open manual collection, Shift-click selects a range, *Select all results* adds every image the current search shows. Keyword collections show the keyword choice instead of the selection grid.
- **Manual order:** drag and drop in the filmstrip, with handles that work by touch; *Move to start* / *Move to end*; moving several selected images at once; keyboard shortcuts. *Sort by capture time / title / file name* is a one-time action that rewrites the order (sent as one `move`); afterwards the order can be adjusted by hand. New images go to the end, or after the image selected in the filmstrip.
- **Search:** free text over title, description and file name; keyword chips; place; capture date range; *only images in this collection* / *only images not in it*. Runs in the browser on `/api/v1/files`, which already carries the metadata; a few thousand entries filter instantly.
- **Preview:** *Show on TV* for one image, *Play collection*.
- **Live updates:** the editor listens to `/api/v1/events` and applies other users' changes to its view as they arrive.
- **Offline queue:** changes made while the connection is down are kept in the browser's storage with their change IDs and sent in order on reconnect; a change that no longer fits (e.g. its collection was deleted) is reported instead of guessed. Thanks to the change IDs a resend never applies twice.
- **Ingress details:** all URLs relative (Ingress serves the panel under a changing path prefix, `X-Ingress-Path`); `ingress: true`, `ingress_port`, `panel_icon`, `panel_title` and `panel_admin: false` in `config.yaml`. Port 5000 stays for the integration.
- **Panel name:** "Frame Collections" (decided 2026-11-08), so it does not clash with a user's own control dashboard named "The Frame"; an add-on panel's title cannot be renamed per installation, a dashboard's can. Rejected: hiding the panel and linking to it from the dashboard (needs the installation-specific Ingress URL, an extra setup step). Kept as a later idea: a compact control strip in the editor (current image, previous/next, play/pause, sync), which could make a separate control dashboard unnecessary; not now, because dashboard cards also serve HA's app widgets and voice.

#### Apple Home

The add-on already pushes the collection list to the integration, so `select.the_frame_collection` updates within a second. The HomeKit bridge, however, builds its accessories when it starts; new or renamed select options were expected to appear in Apple Home only after the bridge reloads; with the real setup they appear without it (2026-11-11), so the option below is a fallback. New integration option *Reload the HomeKit bridge when collections change*: reloads only the bridge entry that exposes the select (`homeassistant.reload_config_entry`), 30 seconds after the last change, so a long editing session triggers one reload.

#### Robustness summary

- One source of truth: the add-on's `/data` (`state.json`, `history.json`), written atomically, included in Home Assistant backups. Neither the editor nor the integration keeps its own copy.
- Home Assistant restarts do not touch the add-on; the integration and the editor reconnect.
- Lost connections: changes are small, applied server-side and idempotent by change ID; the editor queues them while offline.
- Concurrent users: one change at a time, file-name anchors, revision numbers, live updates, conflicts shown; every change logged and undoable.
- Image files and their metadata are only read, never written.

#### Modules

- **Add-on backend: no new dependencies.** FastAPI's `StaticFiles` serves the editor; `asyncio.Lock`, `uuid` and `json` from the standard library; the existing event stream; Pillow for thumbnails (optionally cached in `/data`). JSON files instead of SQLite: at this scale (dozens of collections, a few hundred logged changes) they are simpler, readable in a backup and consistent with the existing state file; `sqlite3` (standard library) is the fallback if the history ever needs to grow much larger.
- **Editor: plain JavaScript modules and native web components, no build step, nothing loaded from the internet at runtime.** One library: **SortableJS** (MIT, about 45 KB) for drag and drop, because the browser's own drag and drop is unreliable by touch on iPhone and iPad; it also handles multi-select and auto-scrolling. Copied into `theframe_controller/app/static/vendor/` with a fixed version and its license file, not loaded from a CDN. Large grids use `loading="lazy"` and CSS `content-visibility`; light and dark mode via `prefers-color-scheme` (the Ingress iframe does not get Home Assistant's theme).
- **Integration: no new dependencies** (`homeassistant.reload_config_entry` is built in).
- **Tests:** concurrent changes, retries, history and migration with the existing pytest setup and FastAPI's test client; end-to-end editor tests with **Playwright** (`pytest-playwright`, development and CI only, never in the add-on image): open the editor against the add-on with the fake TV, create a collection, add, reorder and remove images, two browser contexts editing the same collection, offline queue replay. Own CI job with the Chromium download cached.

## 4. Repository layout (monorepo)

```
repository.yaml              # add-on repository
hacs.json                    # HACS integration repo
custom_components/theframe/  # integration
theframe_controller/         # add-on: config.yaml, Dockerfile, app/
tests/                       # add-on, integration, contract tests; fake TV
```
- One release tag sets the version in `manifest.json` and add-on `config.yaml`.
- CI publishes multi-arch add-on images (aarch64, amd64) to GHCR; `config.yaml` references the image.
- Verify early that HACS validation and the Supervisor accept this combined layout.

## 5. Development environment

| Layer | Setup |
|---|---|
| Add-on dev | Native Python venv on the Mac (WOL and IP Control work natively; WOL does not from Docker on macOS); config via env variable; dev token, own image folder and state file; dry-run on by default |
| Integration dev | HA Core in a native venv on the Mac, pointed at the local add-on or a fake add-on built from the OpenAPI contract |
| Tests | pytest with a fake TV simulating states (off, standby, slow, connection drop mid-upload, art mode toggled during sync); pytest-homeassistant-custom-component for the integration; contract tests |
| CI (GitHub-hosted) | Add-on: linter, tests, multi-arch image build. Integration: hassfest, HACS validation, ruff/mypy, tests |
| Staging | HAOS VM once per add-on release |
| Production | Raspberry Pi; tagged releases via add-on store and HACS; backup before each update |

Key test scenarios: add-on starts with TV off and answers immediately; queued sync runs once art mode appears; slideshow survives a re-upload; state survives restarts of either component.

## 6. Implementation order

0. **Hotfix release of the current add-on** (before anything else): `samsungframetv==4.1.4` from PyPI, imports changed to `samsungframetv`, otherwise unchanged. Test once against the real TV, release, install on the Pi. Run the library's `diagnose` tool once against the TV and record the results. *Code change done (add-on 1.0.17.002, not yet released); diagnose done 2026-10-31, results in 3.0.*
1. Monorepo layout and CI. *Done on branch `step1/monorepo` (not yet pushed), details below.*

   - Monorepo is `exploramus/theframe-integration`; the add-on was imported with its history (`git subtree`) into `theframe_controller/`, code in `theframe_controller/app/`. `exploramus/theframe-controller` is to be archived after the first release from here.
   - Shared version **1.1.0** in `manifest.json` and add-on `config.yaml`; `tests/test_versions.py` checks they match each other, the add-on changelog and, on release, the tag.
   - Add-on installs as a prebuilt image `ghcr.io/exploramus/theframe-controller-{arch}`, arches aarch64 and amd64 (armv7 dropped).
   - CI (`.github/workflows/ci.yaml`): ruff (baseline rules E4/E7/E9/F), pytest, hassfest, HACS validation (brands ignored), add-on linter, image build for both arches. Release (`release.yaml`, on tag `v*`): version check, push images to GHCR with `io.hass.*` labels, GitHub release.
   - Fixed along the way: hassfest errors (manifest key order, URL in config-flow strings), `__pycache__` untracked, five unused imports, `tilo-boehmann` links.
   - Checked locally: ruff, pytest, actionlint, hassfest, Docker build and import smoke test for both arches. Not checked locally: HACS validation and the add-on linter (need GitHub), Supervisor install from the new repository (HAOS VM or Pi, after the first release).
   - Repository made public on 2026-10-31 (needed by HACS, the Supervisor add-on store and anonymous GHCR pulls); description and topics set for HACS. History checked before: no secrets. The TV's LAN IP and MAC were removed from the add-on defaults and this doc; `tv_ip` must now be entered at install, `tv_mac` is optional (WOL is skipped without it).
   - Deferred: mypy for the integration (needs Home Assistant installed; add with pytest-homeassistant-custom-component in step 3).
2. Add-on refactor on `samsungframetv` 4.1.x: TV monitor based on `get_power_state()`, client created off the event loop, library timeouts and back-off, start without the TV, `host_network: true`, config via env; optional IP Control. *Done on branch `step2/tv-monitor` (add-on 1.2.0), details below.*

   - `TVMonitor` polls `get_power_state()` every 10 s while on, backing off to 60 s while off/standby; calls `reset_art_service()` when the TV comes back; starts a pending sync and refreshes the current artwork in art mode. Endpoints read its cache; art-mode commands fail fast with the old error text; `/status` keeps the integration's keys and adds `power_state`, `power_state_since`, `last_check`, `art_service_available`, `ip_control`, `error`.
   - TV client created in a worker thread with `timeout=10`. Outer timeouts: 15 s per power check (the library's async REST calls have none, so aiohttp's 5-minute default would apply), 30 s around other TV calls. A check that times out counts as `off`.
   - No WOL and no `sys.exit` at startup; config errors are reported by `/status`. New `POST /power_on` (IP Control if paired, else WOL) and `POST /ip_control/pair`; options `ip_control`, `ip_control_token_file`; `tv_connect_*` removed.
   - Options file from `THEFRAME_OPTIONS` (default `/data/options.json`) for running on the Mac.
   - Tests: `tests/test_addon_monitor.py` with a minimal fake TV (startup without TV, off→art transition, fail-fast, config error, hanging power check). The full fake TV is step 3.
   - Checked against the real TV (read-only, dev options, IP Control paired): first check `art`, `/status` ~10 ms. Unreachable TV: server answers at once, `off` after ~25 s. Not checked: off/standby transitions with the real TV, `/power_on`, sync and display commands, Supervisor install with `host_network`.
3. Fake TV and the state-based tests (using the library's fixtures as reference). *Done on branch `step3/fake-tv`, details below.*

   - `tests/fake_tv.py`: fake `SamsungTVAsyncArt` with the calls the add-on makes. Response shapes come from our own TV's diagnose run (`tests/fixtures/frame_2022.json`, redacted) instead of the library's 2018 recordings. Knobs: power state, latency, connection drop on the n-th upload, leaving art mode after the n-th upload, unconfirmed uploads.
   - `tests/test_addon_scenarios.py`: TV off/standby/watching TV, queued sync runs in art mode, slow TV, connection drop mid-upload, art mode left during sync, unconfirmed upload, deletes only own uploads, modified file re-uploaded. The two step-4 scenarios (slideshow survives a re-upload, survives a restart) are strict `xfail`s and will flag when step 4 makes them pass.
   - Fixed in the add-on (1.2.0, unreleased): failed or interrupted syncs stay queued and are retried (failures after 60 s); unconfirmed uploads are looked up in My Photos (exactly one new image) instead of being uploaded again.
4. Job model, persisted state, event stream, versioned API; integration adapted to it. *Add-on part in PR #4 (1.3.0), integration part on branch `step4/integration-api-v1`.*

   - `/api/v1` with HTTP error codes (`{"error": {"code", "message"}}`): `not_configured` 503; `tv_not_in_art_mode`, `tv_not_on`, `job_conflict`, `empty_slideshow`, `wake_unavailable`, `ip_control_disabled` 409; `not_found` 404; `invalid_request` 422; `tv_error` 502.
   - Sync job (3.3): `POST /api/v1/sync` → 202 + job; queued until art mode (replaces `_sync_pending`, generalising 3.2 for syncs); one at a time (409); total/done progress; failures retried as in step 3. Display commands return 409 outside art mode; the optional `wake: true` intent is not implemented.
   - Slideshow (3.4): owned by the add-on, persisted in `/data/state.json` (next to options.json); stores file names, resolves content IDs when shown; timer in the add-on (5 s tick), paused outside art mode and while a sync runs. `position` = index shown last; legacy `slideshow_index` keeps its "next to show" meaning.
   - Events (3.5): SSE at `/api/v1/events`: `status` on connect and whenever the status changes (deduplicated, ignoring the check timestamp), `files` after a sync; keep-alive every 15 s.
   - Old endpoints kept as deprecated wrappers on the same state; `/sync_files` still waits for the sync. Fixed: fresh installs without `uploaded_files.json` returned errors.
   - 1.4.0: the deprecated unversioned endpoints are removed (no backward compatibility needed at this stage); only `/api/v1` is served, reconcile moved to `POST /api/v1/reconcile`.
   - Tests: `tests/test_addon_api_v1.py`; the two step-4 scenarios from step 3 now pass. Checked read-only against the real TV (status, slideshow, event stream).
   - Integration: client for `/api/v1` with all endpoints in one `ENDPOINTS` table; coordinator takes `status` events from the stream (reconnect with back-off 5 s → 5 min; polling every 60 s stays as fallback and for files/keywords) and refreshes after `files` events; its own slideshow timer and in-memory slideshow state removed; switch, keyword switches, number and `play_files` only `PUT /api/v1/slideshow`; new `sensor.the_frame_sync`.
   - Contract (3.6): `tests/test_contract.py` checks every `ENDPOINTS` entry and the slideshow fields against the add-on's OpenAPI document; `tests/test_integration_client.py` runs the real client against the add-on served over HTTP with the fake TV, event stream included.
   - Home Assistant tests in `tests_ha/` (pytest-homeassistant-custom-component, Python 3.13, own CI job): entities mirror add-on state, switches/number/services send the right `PUT`s, status events update entities, sync conflict is not an error. `custom_components/__init__.py` added so the harness imports this repo's integration. mypy still deferred.
5. Later: playlist editor via Ingress; new entities from the art settings API; a control strip in the collections editor (see 3.8, *Panel name*). (OneDrive sync in the add-on dropped, see decision 6.)
   - Done in 1.4.0: **collections**, named slideshow presets (keyword set or playlist) stored with the slideshow state; the integration's `select.the_frame_collection` puts all of them into one Apple Home tile instead of one switch per keyword. A playlist editor would edit collections.
   - Done in 1.5.0: **image information**: title, description, capture time, GPS position and optional place names read from the files and reported in `/api/v1/files`; capture date sent to the TV with uploads; metadata attributes on `sensor.the_frame_current_image`.
6. **Collections editor** (design in 3.8, decision 7), in this order:
   1. Add-on data model: collection IDs, kinds, revisions, missing files; slideshow refers to the collection by ID and position by file name; one-time migration. Tests. *Done on branch `step6.1/collection-model` (1.6.0, unreleased): `state.json` version 2 (version 1 kept as `state.json.v1`); `PUT /api/v1/collections/{name}` keeps the ID and counts revisions; `updated_by` from the Ingress user headers, else "Home Assistant"; the API still takes collection names, so the integration is unchanged. Tests in `tests/test_addon_collections.py`.*
   2. Change API: changes with IDs applied one at a time, `collection` events, history, undo, restore, rename, export and import; existing endpoints routed through the change path. Tests including concurrent changes and retries. *Done on branch `step6.2/change-api` (part of 1.6.0): `history.json` is written before `state.json`, and at start the collections are rolled forward from the history, so a crash between the two writes loses nothing and change IDs stay consistent with the state; change IDs are rebuilt from the history after a restart; undo applies the stored inverse changes (each moved or removed file goes back after its old neighbour); restore and undo of a delete keep the collection ID and add " (restored)" on a name clash; `PUT /api/v1/collections/{name}` is logged as `save`; extra endpoint `GET /api/v1/collections/deleted`. Tests in `tests/test_addon_changes.py`.*
   3. Ingress editor: panel config, grid, search, filmstrip with SortableJS, live updates, offline queue. Playwright tests and their CI job. *Done on branch `step6.3/editor` (part of 1.6.0): `ingress_entry: editor/`, the add-on serves `app/static/` at `/editor` and redirects `/` there; SortableJS 1.15.6 checked against the npm registry's checksum and bundled with its license. Only edits inside a collection (add, remove, move, set_keywords) go through the offline queue; create, rename, duplicate, delete, undo, restore and import need a connection, which keeps the browser from guessing collection IDs. Change IDs are made with `crypto.getRandomValues`, because `crypto.randomUUID` needs a secure context and Home Assistant is often reached over plain http. Without the event stream the editor polls every 30 s. Presence ("who else is editing") is left out for now. Phones: the page scrolls as a whole, the order strip stays at the bottom. Browser tests in `tests_e2e/` (own folder: Playwright's sync API keeps an event loop running that the add-on tests cannot share), CI job "Editor – browser tests".*
   4. Integration: HomeKit reload option. HA tests. *Done on branch `step6.4/homekit-reload` (part of 1.6.0): option `reload_homekit` (off by default) in the integration's options; `homekit.py` watches the collection names in the add-on status (rename, add, delete, order) and reloads, 30 s after the last change, every loaded HomeKit entry whose entity filter lets `select.the_frame_collection` through (an empty filter counts as all), via `config_entries.async_reload`, the same as the `homeassistant.reload_config_entry` action. Unloading the integration drops a pending reload. Whether Apple Home needs the reload at all is still to be checked with the real setup in step 6.5. Tests in `tests_ha/test_homekit.py`.*
   5. Docs (DOCS.md, README), HAOS check of Ingress (decision 5), release. *Docs finished on branch `step6.5/release-1.6.0`: sidebar step in the installation, backup note, editor troubleshooting, the editor in the README's collections section. Decided 2026-11-08: for 1.6.0 the HAOS check is done on the Pi after the release (backup first) instead of in a VM; still to confirm there: the panel through Ingress and *Show in sidebar*, user names in the history, the editor in the companion app, and whether Apple Home needs the HomeKit reload. Results (2026-11-11): Apple Home shows new and renamed collections without the reload, so the option stays off by default and is kept as a fallback. Dragging in the iPhone app opened the image's share menu on a long press (fixed in 1.6.3: no touch callout or text selection on the order strip, larger handle). Safari kept the 1.6.0 editor after updating (no `Cache-Control`; fixed in 1.6.3 with `no-cache`).*
