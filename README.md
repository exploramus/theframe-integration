# The Frame Controller — Home Assistant Integration

Companion integration for the [theframe-controller](theframe_controller/) add-on (in this repository).
Replaces the manual `configuration.yaml` / `automations.yaml` setup with native Home Assistant entities, services, and a media source for image browsing.

## Status

First iteration — Variant A (hybrid). The integration talks to the add-on over HTTP; the add-on still does the heavy lifting (TV connection, sync, slideshow logic).

## Requirements

- `theframe-controller` add-on **v1.0.16+** (adds `/list_files`, `/thumbnail/{file_name}`, `/status`)
- Home Assistant 2024.x or newer

## Repository layout

This repository holds both parts, released together under one version (tag `vX.Y.Z`):

- `custom_components/theframe/` — the integration (HACS)
- `theframe_controller/` — the add-on (Home Assistant add-on repository, prebuilt images on GHCR)
- `tests/`, `.github/workflows/` — tests and CI

## Installation

**Add-on:** in Home Assistant go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add `https://github.com/exploramus/theframe-integration`, then install **The Frame Controller**. If you used the old `exploramus/theframe-controller` repository, uninstall the old add-on first and re-enter its options; tokens and `uploaded_files.json` live in `/media` and are kept.

**Integration:** add this repository to HACS as a custom repository (category *Integration*), or copy `custom_components/theframe/` into your Home Assistant `<config>/custom_components/` directory and restart Home Assistant. Then add the integration via **Settings → Devices & Services → Add Integration → "The Frame Controller"** and enter the add-on URL (default `http://homeassistant.local:5000`).

## What you get

| Entity / feature | Replaces in DOCS.md |
| --- | --- |
| `switch.the_frame_slideshow` | `input_boolean.the_frame_show` + the `time_pattern` slideshow automation |
| `switch.the_frame_art_<keyword>` (one per keyword, generated dynamically; German umlauts are transliterated so `Blüte` becomes `bluete` instead of `blute`) | All `template:` switches |
| `number.the_frame_slideshow_interval` | `input_number.theframe_interval` + HomeKit-bridge automations |
| `button.the_frame_sync_files` | `rest_command.sync_files` + `shell_command.image_sync` |
| `button.the_frame_next_image` / `previous_image` | `rest_command.show_slideshow_*` |
| `sensor.the_frame_current_image` (with file_name + keywords as attributes) | n/a — new |
| `sensor.the_frame_image_count`, `sensor.the_frame_slideshow_position` | n/a — new |
| `binary_sensor.the_frame_in_art_mode`, `binary_sensor.the_frame_sync_pending` | n/a — new |
| `image.the_frame_current_image` (live thumbnail of what's on the TV) | n/a — new |
| `media_player.the_frame_player` — accepts Play actions from the media browser, mapped to `show_file` | n/a — new |
| Services `theframe.show_file`, `show_image`, `sync_files`, `next`, `previous` | `rest_command.*` |
| Media Source "The Frame" | n/a — new feature: browse images with thumbnails |

> **Note on "Play" in the media browser:** The Frame's art mode is *not* a DLNA renderer — pushing an image to the TV via DLNA does not land in art mode. Pick **The Frame Player** as the target instead; it routes the selection to the `show_file` endpoint and the image appears in art mode within a second.

The keyword switches appear automatically as soon as the add-on reports new keywords through `/available_keywords`. No more handwritten `template: - switch:` blocks per keyword.

## Image browser

Open the **Media Browser** in HA, select **The Frame**, and you can browse images either flat (*All images*) or grouped by keyword. Thumbnails are served by the add-on's new `/thumbnail/<file_name>` endpoint and proxied through HA so they work in the mobile companion and over Nabu Casa.

To display a clicked image on the TV, call the `theframe.show_file` service with the file name shown in the browser. A simple Lovelace button-card template:

```yaml
type: button
tap_action:
  action: call-service
  service: theframe.show_file
  service_data:
    file_name: sunset.jpg
```

## Changing the add-on URL after install

If the add-on moves to a different host or port (or you mistyped the URL during initial setup), open **Settings → Devices & Services → The Frame → ⋮ → Reconfigure**. Enter the new URL — entities and their history are kept; only the connection target is swapped.

## Hiding unwanted keywords

Some keywords are housekeeping markers rather than real categories — e.g. `TheFrame` if you tag every uploaded photo with it, or `exportiert` if your photo app sets that as part of the export workflow. Keywords like these would otherwise produce empty-feeling switches and one big media-browser folder containing the whole library.

Open **Settings → Devices & Services → The Frame → Configure** and enter the keywords to hide as a comma-separated list (e.g. `TheFrame, exportiert`). The integration reloads automatically and:

- removes the corresponding `switch.the_frame_art_<keyword>` entities,
- excludes the per-keyword folders from the Media Browser source,
- strips the keyword from the file-level metadata exposed by sensors.

Per-image data on the add-on is untouched; the filter is applied client-side in the integration.

## Curated playlists

For ordered, hand-picked image sequences (something keyword filters can't express), call the `theframe.play_files` service with an ordered list of file names. The list becomes the active slideshow — the existing slideshow timer, the next/previous buttons and `media_player.the_frame_player` all walk through it in order.

Define playlists as plain HA scripts:

```yaml
script:
  the_frame_sunset_show:
    alias: "TheFrame: Sunset show"
    sequence:
      - action: theframe.play_files
        data:
          file_names:
            - sunset_01.jpg
            - sunset_02.jpg
            - harbor_evening.jpg
            - lighthouse.jpg
      - action: switch.turn_on
        target:
          entity_id: switch.the_frame_slideshow
```

Calling that script loads the four images in that exact order and turns the auto-advance on. The interval entity (`number.the_frame_slideshow_interval`) controls how long each image stays. To stop the auto-advance, turn the slideshow switch off — the playlist stays loaded so you can step through it manually with `button.the_frame_next_image` / `button.the_frame_previous_image` or via `media_player.the_frame_player`.

Service parameters (full list also visible in **Developer Tools → Services → theframe.play_files**):

| Field | Default | What it does |
| --- | --- | --- |
| `file_names` | required | Ordered list of file names exactly as the add-on knows them. Files not on the TV are skipped with a log warning. |
| `slideshow_index` | `0` | Which item in the list to start at (0-based). |
| `start` | `true` | Show the first item immediately after loading. Set to `false` if you want to load a playlist but only have it advance on the next timer tick. |

### Playlist mode vs. keyword mode

The slideshow is in exactly one of two modes at any time, and they don't compose:

- **Keyword mode** — driven by the `switch.the_frame_art_*` switches. The set of active switches becomes the keyword filter; the add-on builds the slideshow from images that match any of those keywords.
- **Playlist mode** — driven by `theframe.play_files`. The slideshow runs through the explicit, ordered file list you handed in.

Switching modes is a one-way replacement, never an additive operation:

- Calling `theframe.play_files` clears all active keyword switches (they show OFF afterwards) and the playlist takes over.
- Toggling any keyword switch re-enters keyword mode and replaces the playlist with the keyword-filtered set.

In both modes, `switch.the_frame_slideshow`, `number.the_frame_slideshow_interval`, the next/previous buttons and the media player work the same way — they just walk through whichever set of files is currently loaded.

Add-on requirement: v1.0.17+ (introduces the `/initialize_slideshow_files` endpoint).

## Starter dashboard

A ready-to-use Lovelace dashboard lives in [`dashboards/theframe.yaml`](dashboards/theframe.yaml). It uses the new `image.the_frame_current_image` entity for a live preview, the `switch`/`number`/`select` entities for slideshow control, a button that opens the Media Browser, and a Categories list of keyword switches. The file's header explains the two ways to install it (raw editor vs. YAML-mode dashboard) and which lines to adjust to match your keyword set.

## Migrating from the YAML setup

See [MIGRATION.md](MIGRATION.md) for a step-by-step guide that removes the DOCS.md helpers, template switches, automations, rest_command and sensor blocks in the right order — without breaking anything mid-migration.

## What still needs the YAML

After migration, all entities from DOCS.md can be removed. The HomeKit bridge configuration stays — it now exports the new `switch.*` and `number.*` entities directly.

The OneDrive sync via `rclone` still lives in the add-on (`image_sync.sh`). The integration's *Sync Files* button only triggers `/sync_files` (TV ↔ local folder reconciliation). If you want the rclone step to run from HA, keep the `shell_command: image_sync` line, or wrap it with a script.
