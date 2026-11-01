# The Frame Controller for Home Assistant

Run a Samsung The Frame in Art Mode from Home Assistant: keep a folder of your own images in sync with the TV, run slideshows by keyword or as playlists, and see in real time what the TV shows — on dashboards, in automations and in Apple Home.

The project has two parts, released together with the same version:

| Part | What it does | Install from |
| --- | --- | --- |
| **Add-on** ([`theframe_controller/`](theframe_controller/)) | The engine: connection to the TV, image sync, slideshow and its timer, real-time status, HTTP API | Home Assistant add-on store (this repository) |
| **Integration** ([`custom_components/theframe/`](custom_components/theframe/)) | Home Assistant entities, actions and media browser on top of the add-on | HACS (this repository) |

The add-on works on its own through its API; the integration needs the add-on.

## Features

- **Image sync** — new images in your folder appear on the TV, changed ones are re-uploaded, deleted ones removed. Runs in the background; if the TV is off the sync waits for Art Mode.
- **Keyword slideshows** — one switch per keyword from your images' metadata (Photos, Lightroom); turn on the ones you want to see.
- **Playlists** — an ordered list of images with `theframe.play_files`.
- **Real-time status** — Art Mode, power state and the current image within about a second, pushed by the TV.
- **Live image** of what the TV shows, for dashboards.
- **Media browser** — browse your images with thumbnails, by keyword, and show one on the TV.
- **Apple Home** — slideshow, keywords and interval through Home Assistant's HomeKit bridge.
- **Settings survive restarts** — the add-on owns the slideshow state.

Tested with a 2022 The Frame (GQ55LS03B); uses [samsungframetv](https://pypi.org/project/samsungframetv/) 4.1.

## Installation

**1. Add-on.** **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add `https://github.com/exploramus/theframe-integration`, install **The Frame Controller**, enter the TV's IP address and start it. On the first connection press **Allow** on the TV. The add-on's **Documentation** tab ([DOCS.md](theframe_controller/DOCS.md)) describes all options.

**2. Integration.** In HACS add this repository as a custom repository (category *Integration*) and install **The Frame Controller**, or copy `custom_components/theframe/` to `<config>/custom_components/`. Restart Home Assistant, then **Settings → Devices & Services → Add Integration → The Frame Controller** and enter the add-on URL, usually `http://homeassistant.local:5000`.

Keep both at the same version; updates arrive through the add-on store and HACS.

## Entities

| Entity | Purpose |
| --- | --- |
| `switch.the_frame_slideshow` | Slideshow timer on/off |
| `switch.the_frame_art_<keyword>` | One per keyword; the slideshow shows images with any active keyword. Created automatically; umlauts become `ae`/`oe`/`ue` (`Blüte` → `bluete`) |
| `number.the_frame_slideshow_interval` | Minutes per image (1–30) |
| `select.the_frame_slideshow_interval` | The same as a choice (1, 2, 3, 5, 10, 15, 20, 30 min) — Apple Home shows it as one switch per option |
| `button.the_frame_sync_files` | Sync the image folder with the TV |
| `button.the_frame_next_image`, `button.the_frame_previous_image` | Step through the slideshow |
| `image.the_frame_current_image` | Picture of what the TV shows, for picture cards |
| `sensor.the_frame_current_image` | Content ID of the current image; file name and keywords as attributes |
| `sensor.the_frame_image_count` | Images uploaded by the add-on |
| `sensor.the_frame_sync` | Sync state: `idle`, `queued` (waiting for Art Mode), `running`, `done`; progress as attributes |
| `sensor.the_frame_slideshow_position` | Position in the slideshow (disabled by default) |
| `binary_sensor.the_frame_in_art_mode` | TV is in Art Mode |
| `binary_sensor.the_frame_sync_pending` | A sync is waiting for Art Mode |
| `media_player.the_frame_player` | Target for **Play** in the media browser; next/previous |

Actions: `theframe.show_file`, `theframe.show_image`, `theframe.sync_files`, `theframe.next`, `theframe.previous`, `theframe.play_files` (details in **Developer Tools → Actions**).

## Slideshows

The slideshow is either in **keyword mode** or in **playlist mode**; switching is always a replacement:

- **Keyword mode:** turn on one or more `switch.the_frame_art_*`. The slideshow shows every image with any of these keywords.
- **Playlist mode:** call `theframe.play_files` with an ordered list of file names. The keyword switches show off while a playlist runs; turning one on goes back to keyword mode.

In both modes the slideshow switch, the interval, the next/previous buttons and the media player work the same way. The timer pauses outside Art Mode and during a sync.

Playlists fit well into scripts:

```yaml
script:
  the_frame_sunset_show:
    alias: "The Frame: Sunset show"
    sequence:
      - action: theframe.play_files
        data:
          file_names:
            - sunset_01.jpg
            - sunset_02.jpg
            - harbor_evening.jpg
      - action: switch.turn_on
        target:
          entity_id: switch.the_frame_slideshow
```

| `play_files` field | Default | Meaning |
| --- | --- | --- |
| `file_names` | required | File names as in the image folder, in order. Files not on the TV are skipped with a warning in the log. |
| `slideshow_index` | `0` | Position to start at (0-based) |
| `start` | `true` | Show that image at once; otherwise wait for the next timer step |

## Media browser

**Media → The Frame** lists all images, or the images per keyword, with thumbnails. To show one on the TV, pick **The Frame Player** as the target: the Frame's Art Mode is not a DLNA renderer, so other players would show the image outside Art Mode. Thumbnails are served through Home Assistant and work remotely too.

## Configuration

- **Hide keywords:** **Settings → Devices & Services → The Frame → Configure**, enter keywords to hide (e.g. `TheFrame, exported`). Their switches and media browser folders disappear; the add-on's data is unchanged.
- **Change the add-on URL:** **⋮ → Reconfigure** on the integration; entities and history are kept.
- **Apple Home:** expose `switch.the_frame_slideshow`, the keyword switches you want and `select.the_frame_slideshow_interval` in the HomeKit bridge.

A starter dashboard is in [`dashboards/theframe.yaml`](dashboards/theframe.yaml); its header explains how to add it. Replace the keyword switches with your own.

**Images from OneDrive:** the add-on syncs a local folder with the TV. To fill that folder from OneDrive or another cloud, see *Images from OneDrive* in [DOCS.md](theframe_controller/DOCS.md).

**Coming from the old YAML setup** (helpers, template switches, `rest_command`s and automations from earlier add-on versions): the integration replaces all of it. Remove those parts after installing the integration; otherwise old automations keep changing the slideshow through deprecated endpoints. The add-on log shows such changes as `slideshow changed via … (deprecated)`.

## Development

```
custom_components/theframe/   integration
theframe_controller/          add-on (config.yaml, Dockerfile, app/theframe.py, DOCS.md)
tests/                        add-on, API client and contract tests, fake TV (fake_tv.py)
tests_ha/                     Home Assistant tests for the integration
docs/DESIGN.md                architecture and decisions
```

**Tests.** `tests/` runs with Python 3.11 and the add-on's requirements:

```bash
pip install -r theframe_controller/requirements.txt pytest pyyaml httpx ruff
ruff check . && pytest
```

`tests/fake_tv.py` stands in for the TV, with responses recorded from a real 2022 Frame. `tests/test_contract.py` checks every endpoint the integration calls against the add-on's OpenAPI document. The Home Assistant tests need Python 3.13:

```bash
pip install pytest-homeassistant-custom-component
python -m pytest tests_ha -o asyncio_mode=auto
```

**CI** runs lint, both test suites, hassfest, HACS validation, the add-on linter and image builds for aarch64 and amd64 on every pull request. `main` is protected: changes go through pull requests with passing checks.

**Releases.** Set the same version in `theframe_controller/config.yaml` and `custom_components/theframe/manifest.json`, add a line to `theframe_controller/CHANGELOG.md`, merge, then push a tag `vX.Y.Z`. The release workflow checks the versions, publishes the add-on images to `ghcr.io/exploramus/theframe-controller-{arch}` and creates the GitHub release for HACS. Release tags cannot be moved or deleted.

## License

GPL-3.0, see [LICENSE](LICENSE). The add-on uses [samsungframetv](https://pypi.org/project/samsungframetv/) (LGPL-3.0).
