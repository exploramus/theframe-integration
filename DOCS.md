# TheFrame Controller

## Purpose

Add-on to expose a web API to control Samsung TheFrame in artmode, adding image synchronization and slideshow capabilities

Key controls such as the interval between images, the keywords to select images, the option to advance to the next image and the toggle for the slideshow mode are exposed to the HomeKit integration for inclusion in Apple Home



## How it works

### File synchronisation

Calling `/sync_files` compares the images in the configured folder against what is uploaded on the TV and reconciles the difference — uploading new files, removing deleted ones, and re-uploading modified ones. This operation requires the TV to be on and in Art Mode.

If the TV is off or not in Art Mode when `/sync_files` is called, the sync is queued rather than dropped. The response will be `{"status": "queued"}` instead of `{"status": "success"}`. Any Home Assistant automation that checks this status should handle both values. The queued sync runs automatically within 60 seconds once the TV enters Art Mode — no further action is needed.

### Image sync from OneDrive

`image_sync.sh` uses rclone to mirror `OneDrive:ImageCollection` into the local image folder (`/media/TheFrameImages`). It is invoked via the `image_sync` shell command defined in `configuration.yaml`. The script prints `changed` if rclone copied, deleted, or renamed any files, or `unchanged` if the folder was already up to date. An automation can check this output to decide whether to call `/sync_files` afterwards. If rclone fails the script exits with a non-zero status so the caller can detect and handle the error.

rclone must be installed at `/config/scripts/rclone` and configured at `/config/scripts/rclone.conf` with a remote named `OneDrive`.

### Startup reconciliation

If PIL (Pillow) is installed, the controller will reconcile `uploaded_files.json` against the TV's actual uploaded content on first connection by downloading and comparing thumbnails. This recovers content IDs that may be missing from the JSON file, for example after a reinstall or if the file was lost. The reconciliation runs in the background and does not block the API from starting.

### Image keywords

Images are filtered for slideshows by keyword. Keywords are read from JPEG metadata — either IPTC `subject` tags (written by older macOS Photos exports) or XMP `dc:subject` tags (written by newer macOS Photos exports). PNG files are supported for upload but carry no keywords. To make an image appear under a given keyword in the slideshow, tag it in Photos (or another IPTC/XMP-compatible tool) before placing it in the sync folder.

## Instructions

### Configuration.yaml

Add this to configuration.yaml

```

automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml

input_select:
  theframe_interval_homekit:
    name: TheFrame Interval 
    options:
      - "01"
      - "02"
      - "03"
      - "04"
      - "05"
      - "10"
      - "15"
      - "20"
      - "30"
    initial: "02"
    icon: mdi:timer-outline

template:
  - switch:
      - name: Art Architektur
        default_entity_id: switch.frame_art_architektur
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Architektur
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Architektur
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Architektur' in keywords }}

      - name: Art PhotoArt
        default_entity_id: switch.frame_art_photoart
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: PhotoArt
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: PhotoArt
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'PhotoArt' in keywords }}

      - name: Art Natur
        default_entity_id: switch.frame_art_natur
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Natur
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Natur
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Natur' in keywords }}

      - name: Art Kunst
        default_entity_id: switch.frame_art_kunst
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Kunst
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Kunst
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Kunst' in keywords }}

      - name: Art Blumen
        default_entity_id: switch.frame_art_blumen
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Blumen
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Blumen
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Blumen' in keywords }}

      - name: Art Blüte
        default_entity_id: switch.frame_art_bluete
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Blüte
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Blüte
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Blüte' in keywords }}

      - name: Art Luftaufnahme
        default_entity_id: switch.frame_art_luftaufnahme
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Luftaufnahme
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Luftaufnahme
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Luftaufnahme' in keywords }}

      - name: Art Landschaft
        default_entity_id: switch.frame_art_landschaft
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Landschaft
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Landschaft
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Landschaft' in keywords }}

      - name: Art Dynamics
        default_entity_id: switch.frame_art_dynamics
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Dynamics
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Dynamics
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Dynamics' in keywords }}

      - name: Art ShoreShades
        default_entity_id: switch.frame_art_shoreshades
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: ShoreShades
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: ShoreShades
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'ShoreShades' in keywords }}

      - name: Art Hamburg
        default_entity_id: switch.frame_art_hamburg
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: hamburg
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: hamburg
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'hamburg' in keywords }}

      - name: Art Tiere
        default_entity_id: switch.frame_art_tiere
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Tiere
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Tiere
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Tiere' in keywords }}

      - name: Art Julia
        default_entity_id: switch.frame_art_julia
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Julia
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Julia
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Julia' in keywords }}
          
      - name: Art TheFrame
        default_entity_id: switch.frame_art_theframe
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: TheFrame
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: TheFrame
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'TheFrame' in keywords }}

      - name: Art Naturmotive
        default_entity_id: switch.frame_art_naturmotive
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: Natur
              action: add
          - action: script.update_frame_keywords
            data:
              keyword: Blumen
              action: add
          - action: script.update_frame_keywords
            data:
              keyword: Blüte
              action: add
          - action: script.update_frame_keywords
            data:
              keyword: Landschaft
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: Natur
              action: remove
          - action: script.update_frame_keywords
            data:
              keyword: Blumen
              action: remove
          - action: script.update_frame_keywords
            data:
              keyword: Blüte
              action: remove
          - action: script.update_frame_keywords
            data:
              keyword: Landschaft
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'Natur' in keywords and 'Blumen' in keywords and 'Blüte' in keywords and 'Landschaft' in keywords }}
      - name: Art Shades
        default_entity_id: switch.frame_art_shades
        turn_on:
          - action: script.update_frame_keywords
            data:
              keyword: ShoreShades
              action: add
          - action: script.update_frame_keywords
            data:
              keyword: BuildShades
              action: add
        turn_off:
          - action: script.update_frame_keywords
            data:
              keyword: ShoreShades
              action: remove
          - action: script.update_frame_keywords
            data:
              keyword: BuildShades
              action: remove
        state: >
          {% set keywords = states('input_text.theframe_slideshow_keywords').split(',') | map('trim') | list %}
          {{ 'ShoreShades' in keywords and 'BuildShades' in keywords }}

        

sensor:
  - platform: rest
    name: TheFrame Keywords
    resource: http://localhost:5000/available_keywords
    method: GET
    value_template: "{{ value_json.status }}"
    json_attributes:
      - keywords
    scan_interval: 300  # fetch every 5 minutes
  - platform: template
    sensors:
      theframe_keyword_list:
        friendly_name: "TheFrame Keyword List"
        value_template: "{{ state_attr('sensor.theframe_keywords', 'keywords') | join(', ') }}"


shell_command:
  image_sync: "bash /addons/theframe-controller/image_sync.sh"        

rest_command:
  initialize_slideshow:
    url: "http://localhost:5000/initialize_slideshow"
    method: POST
    headers:
      Content-Type: application/json
    payload: >
      {
        "keyword_list": {{ states('input_text.theframe_slideshow_keywords').split(',') | tojson }},
        "slideshow_index": {{ states('input_number.theframe_slideshow_index') | int }},
        "slideshow_interval_minutes": {{ states('input_number.theframe_interval') | int(5) }}
      }

  show_slideshow_next:
    url: "http://localhost:5000/show_slideshow_next"
    method: POST

  show_slideshow_previous:
    url: "http://localhost:5000/show_slideshow_previous"
    method: POST

  show_slideshow_index:
    url: "http://localhost:5000/show_slideshow_index"
    method: POST
    headers:
      Content-Type: application/json
    payload: >
      {
        "slideshow_index": {{ states('input_number.theframe_slideshow_index') | int }}
      }

  sync_files:
    url: "http://localhost:5000/sync_files"
    method: POST

```

### Automation.yaml

Add this to automation.yaml

```

- id: '1745762158906'
  alias: The Frame Show
  description: Regularly advances slide show
  triggers:
  - trigger: time_pattern
    minutes: '*'
  conditions:
  - condition: state
    entity_id: input_boolean.the_frame_show
    state: 'on'
  - condition: time
    after: 08:00:00
    before: '23:59:00'
  - condition: template
    value_template: '{{ now().minute % (states(''input_number.theframe_interval'')
      | int) == 0 }}'
  - condition: not
    conditions:
    - condition: device
      device_id: 8d5fffb8937ef668a9d271116365552d
      domain: media_player
      entity_id: ceee7c276a81d97be7a153daf2a5291d
      type: is_playing
  actions:
  - action: rest_command.show_slideshow_next
    data: {}
  mode: single

- id: f98e1761602d49f88d2a8be7bc61c3d3
  alias: Update Frame Keyword Dropdown
  triggers:
  - trigger: time_pattern
    hours: /1
  conditions: []
  actions:
  - data_template:
      entity_id: input_select.theframe_slideshow_keyword_select
      options: "{{ state_attr('sensor.theframe_keywords', 'keywords') or ['default']
        }} \n"
    action: input_select.set_options
  - action: homekit.reload
    metadata: {}
    data: {}
- id: '1748523904241'
  alias: TheFrame Update Slideshow Keywords from Selection
  description: ''
  triggers:
  - trigger: state
    entity_id:
    - input_select.theframe_slideshow_keyword_select
  conditions: []
  actions:
  - action: input_text.set_value
    metadata: {}
    data:
      value: '{{ states(''input_select.theframe_slideshow_keyword_select'') }}'
    target:
      entity_id: input_text.theframe_slideshow_keywords
  - delay: 00:00:01
  - action: rest_command.initialize_slideshow
    data: {}
  mode: single

- id: '1771791711050'
  alias: TheFrame Update Slideshow Keywords
  description: ''
  triggers:
  - trigger: state
    entity_id:
    - input_text.theframe_slideshow_keywords
  conditions: []
  actions:
  - action: rest_command.initialize_slideshow
    data: {}
  mode: single

- id: theframe_interval_homekit_to_input_number
  alias: TheFrame Interval HomeKit to Input Number
  trigger:
  - platform: state
    entity_id: input_select.theframe_interval_homekit
  condition:
  - condition: template
    value_template: '{{ states(''input_number.theframe_interval'') | int(-1) != states(''input_select.theframe_interval_homekit'')
      | int(-1) }}'
  action:
  - service: input_number.set_value
    target:
      entity_id: input_number.theframe_interval
    data:
      value: '{{ states(''input_select.theframe_interval_homekit'') | int(5) }}'
  mode: single

- id: theframe_interval_input_number_to_homekit
  alias: TheFrame Interval Input Number to HomeKit
  trigger:
  - platform: state
    entity_id: input_number.theframe_interval
  condition:
  - condition: template
    value_template: '{{ 1 <= (states(''input_number.theframe_interval'') | int(0))
      <= 30 and states(''input_select.theframe_interval_homekit'') != (states(''input_number.theframe_interval'')
      | int | string) }}'
  action:
  - service: input_select.select_option
    target:
      entity_id: input_select.theframe_interval_homekit
    data:
      option: '{{ states(''input_number.theframe_interval'') | int | string }}'
  mode: single

```
