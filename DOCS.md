# TheFrame Controller

## Purpose

Add-on to expose a web API to control Samsung TheFrame in artmode, adding image synchronoization and slideshow capabilities

## Instructions

### Configuration.yaml

Add this to configuration.yaml

```

ssensor:
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


rest_command:
  initialize_slideshow:
    url: "http://localhost:5000/initialize_slideshow"
    method: POST
    headers:
      Content-Type: application/json
    payload: >
      {
        "keyword_list": {{ states('input_text.theframe_slideshow_keywords').split(',') | tojson }},
        "slideshow_index": {{ states('input_number.theframe_slideshow_index') | int }}
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

alias: The Frame Show
description: Regularly advances slide show
triggers:
  - trigger: time_pattern
    minutes: "*"
conditions:
  - condition: state
    entity_id: input_boolean.the_frame_show
    state: "on"
  - condition: time
    after: "10:00:00"
    before: "23:59:00"
  - condition: template
    value_template: "{{ now().minute % (states('input_number.theframe_interval') | int) == 0 }}"
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

alias: TheFrame Update Slideshow Keywords from Selection
description: ""
triggers:
  - trigger: state
    entity_id:
      - input_select.theframe_slideshow_keyword_select
conditions: []
actions:
  - action: input_text.set_value
    metadata: {}
    data:
      value: "{{ states('input_select.theframe_slideshow_keyword_select') }}"
    target:
      entity_id: input_text.theframe_slideshow_keywords
  - delay: "00:00:01"
  - action: rest_command.initialize_slideshow
    data: {}
mode: single

alias: Update Frame Keyword Dropdown
triggers:
  - minutes: /5
    trigger: time_pattern
conditions: []
actions:
  - data_template:
      entity_id: input_select.theframe_slideshow_keyword_select
      options: |
        {{ state_attr('sensor.theframe_keywords', 'keywords') or ['default'] }} 
    action: input_select.set_options

```
