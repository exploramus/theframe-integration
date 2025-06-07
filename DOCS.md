# TheFrame Controller

## Purpose

Add-on to expose a web API to control 

## Instructions

Add this to configuration.yaml

```

sensor:
  - platform: rest
    name: TheFrame Keywords
    resource: http://localhost:5000/available_keywords
    method: GET
    value_template: "{{ value_json.status }}"
    json_attributes:
      - keywords
    scan_interval: 300  # fetch every 5 minutes

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


```