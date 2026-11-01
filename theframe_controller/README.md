# The Frame Controller

Runs your Samsung The Frame in Art Mode from Home Assistant: keeps a folder of images in sync with the TV, runs a keyword or playlist slideshow, and reports what the TV is doing in real time.

- **Image sync** — uploads new images from a folder (e.g. `/media/TheFrameImages`), re-uploads changed ones and removes deleted ones. Runs in the background and waits for Art Mode if the TV is off.
- **Slideshow** — by keywords from the images' metadata (Photos, Lightroom) or as an ordered playlist; interval, on/off and keywords survive restarts.
- **Real-time status** — power state, Art Mode and the current image within about a second, from the TV's own events.
- **Starts without the TV** — never wakes the TV by itself; optional Wake-on-LAN or Samsung IP Control to switch it on.

Pair it with the **The Frame Controller** integration (same repository, via HACS) for switches, sensors, a live image, the media browser and Apple Home.

See the **Documentation** tab for setup and options.
