# The Frame Controller — Home Assistant Integration

Companion integration for the [theframe-controller](https://github.com/tilo-boehmann/theframe-controller) add-on.
Replaces the manual `configuration.yaml` / `automations.yaml` setup with native Home Assistant entities, services, and a media source for image browsing.

## Status

First iteration — Variant A (hybrid). The integration talks to the add-on over HTTP; the add-on still does the heavy lifting (TV connection, sync, slideshow logic).

## Requirements

- `theframe-controller` add-on **v1.0.16+** (adds `/list_files`, `/thumbnail/{file_name}`, `/status`)
- Home Assistant 2024.x or newer

## Installation

Copy `custom_components/theframe/` into your Home Assistant `<config>/custom_components/` directory and restart Home Assistant. Then add the integration via **Settings → Devices & Services → Add Integration → "The Frame Controller"** and enter the add-on URL (default `http://homeassistant.local:5000`).

## What you get

| Entity / feature | Replaces in DOCS.md |
| --- | --- |
| `switch.the_frame_slideshow` | `input_boolean.the_frame_show` + the `time_pattern` slideshow automation |
| `switch.the_frame_art_<keyword>` (one per keyword, generated dynamically) | All `template:` switches |
| `number.the_frame_slideshow_interval` | `input_number.theframe_interval` + HomeKit-bridge automations |
| `button.the_frame_sync_files` | `rest_command.sync_files` + `shell_command.image_sync` |
| `button.the_frame_next_image` / `previous_image` | `rest_command.show_slideshow_*` |
| `select.the_frame_keyword` | `input_select.theframe_slideshow_keyword_select` + the two sync automations |
| `sensor.the_frame_current_image` (with file_name + keywords as attributes) | n/a — new |
| `sensor.the_frame_image_count`, `sensor.the_frame_slideshow_position` | n/a — new |
| `binary_sensor.the_frame_in_art_mode`, `binary_sensor.the_frame_sync_pending` | n/a — new |
| `image.the_frame_current_image` (live thumbnail of what's on the TV) | n/a — new |
| Services `theframe.show_file`, `show_image`, `sync_files`, `next`, `previous` | `rest_command.*` |
| Media Source "The Frame" | n/a — new feature: browse images with thumbnails |

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

## Starter dashboard

A ready-to-use Lovelace dashboard lives in [`dashboards/theframe.yaml`](dashboards/theframe.yaml). It uses the new `image.the_frame_current_image` entity for a live preview, the `switch`/`number`/`select` entities for slideshow control, a button that opens the Media Browser, and a Categories list of keyword switches. The file's header explains the two ways to install it (raw editor vs. YAML-mode dashboard) and which lines to adjust to match your keyword set.

## Migrating from the YAML setup

See [MIGRATION.md](MIGRATION.md) for a step-by-step guide that removes the DOCS.md helpers, template switches, automations, rest_command and sensor blocks in the right order — without breaking anything mid-migration.

## What still needs the YAML

After migration, all entities from DOCS.md can be removed. The HomeKit bridge configuration stays — it now exports the new `switch.*` and `number.*` entities directly.

The OneDrive sync via `rclone` still lives in the add-on (`image_sync.sh`). The integration's *Sync Files* button only triggers `/sync_files` (TV ↔ local folder reconciliation). If you want the rclone step to run from HA, keep the `shell_command: image_sync` line, or wrap it with a script.
