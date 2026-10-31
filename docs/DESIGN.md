# TheFrame – Architecture & Development Environment Design

Status: agreed design, 2026-10-31 (rev. 2: samsungframetv 4.1.4 reviewed). Basis: code review of `exploramus/theframe-controller` (add-on, v1.0.17.001) and `exploramus/theframe-integration` (integration, v0.1.0), GitHub state of 2026-05-04.

## 1. Decisions

1. **Keep add-on and integration separate**, but version and release them together from **one repository**.
   - Reasons: process isolation for long-running jobs (sync, uploads, later OneDrive), independent lifecycle (HA restarts don't kill jobs), own dependency set (no conflicts with HA core pins, e.g. core's `samsungtvws`), and the add-on is testable without HA.
2. **Add-on = engine.** Owns all TV and data work: TV connection, power state, WOL, sync, keyword index, slideshow state and timer, playlists, later OneDrive sync and the playlist editor (as the add-on's own web UI via Ingress).
3. **Integration = thin HA adapter.** Entities, services, media browser, HomeKit/Apple Home exposure. No TV access, no heavy work, no own copy of slideshow state.
4. **Build the add-on on `samsungframetv` 4.1.x** (NickWaterton/samsung-tv-ws-api, published on PyPI since 4.0.0). It is the foundation of the TV monitor, not a late migration step. Details in section 3.0.
5. **No HAOS staging for daily work.** The real risk is the data on the TV; it is addressed by safety rules in code and a fake TV. A HAOS VM (Synology VMM or UTM on the Mac) is used only once per add-on release to verify Supervisor-specific behaviour (Ingress, host network, options, `/media` mapping).

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

### 3.1 TV monitor (add-on starts independently of the TV)
- Server starts immediately and always answers.
- Background task tracks TV state with `get_power_state()`: `off` → `standby` → `tv` → `art`; polls with back-off while the TV is off, respects the library's art-service back-off, calls `reset_art_service()` when the TV comes back.
- Optional IP Control pairing as the most reliable state source.
- Endpoints answer status from the cached state; no request ever blocks on the TV.
- All TV calls rely on the library's timeouts, with an outer `asyncio.timeout` as a safety net.
- Waking the TV (IP Control `power_on()`, else WOL) only on explicit request, never at startup.

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

0. **Hotfix release of the current add-on** (before anything else): `samsungframetv==4.1.4` from PyPI, imports changed to `samsungframetv`, otherwise unchanged. Test once against the real TV, release, install on the Pi. Run the library's `diagnose` tool once against the TV and record the results.
1. Monorepo layout and CI.
2. Add-on refactor on `samsungframetv` 4.1.x: TV monitor based on `get_power_state()`, client created off the event loop, library timeouts and back-off, start without the TV, `host_network: true`, config via env; optional IP Control.
3. Fake TV and the state-based tests (using the library's fixtures as reference).
4. Job model, persisted state, event stream, versioned API; integration adapted to it.
5. Later: OneDrive sync via Microsoft Graph (OAuth) in the add-on; playlist editor via Ingress; new entities from the art settings API.
