1.0.0 Initial version
1.0.3 Stable version to display image
1.0.4 Expanded version
1.0.6 Including Sync
1.0.7 Including IPTC keywords
1.0.8 Refreshing keywords
1.0.9 Refreshing files
1.0.10 Recompiled to update includes
1.0.12 Improved response to TV turned off
1.0.13 XMP keywords readout
1.0.14 Asynchronous queued sync in case TV is off or in art modes
1.0.15 Initialize PIL methods during lifespan and improve error handling in image selection
1.0.16 New endpoints /list_files, /thumbnail/{file_name}, /status for the companion HA integration
1.0.17 New endpoint /initialize_slideshow_files for ordered, file-based playlists (curated slideshows)
1.0.17.001 Fix /initialize_slideshow keyword matching: switched from substring-on-JSON-string to proper list membership so escaped Unicode keywords (e.g. "Blüte") match again. Also keeps the previously loaded slideshow_files when a filter returns zero matches.
1.0.17.002 Hotfix: depend on samsungframetv==4.1.4 from PyPI instead of the samsung-tv-ws-api git head (renamed package since 4.0.0, broke rebuilds); imports changed from samsungtvws to samsungframetv. No functional changes.
1.1.0 Moved into the exploramus/theframe-integration monorepo, released together with the integration. Installed as a prebuilt image from GHCR (aarch64, amd64); armv7 dropped. Options tv_ip (required) and tv_mac (optional, WOL skipped without it) no longer have defaults and must be entered at install. No other functional changes.
1.2.0 Starts without the TV: background monitor tracks the power state with get_power_state(), /status answers from its cache, art-mode commands fail fast, TV client created off the event loop with timeouts. No Wake-on-LAN at startup; new POST /power_on (IP Control or WOL) and optional IP Control with POST /ip_control/pair. Runs on the host network. Options file can be set with THEFRAME_OPTIONS. Unused tv_connect_timeout/tv_connect_interval options removed. Sync: a failed sync (e.g. connection drop) or one interrupted by leaving art mode stays queued and is retried; an upload the TV added without confirming is found in My Photos instead of being uploaded again.
1.3.0 Versioned API under /api/v1 with HTTP error codes; sync runs as a background job (202 + job, progress in status and events, one at a time); event stream /api/v1/events; slideshow owned by the add-on: settings persisted in /data/state.json, file names resolved to content IDs when shown (survives re-uploads), timer in the add-on that pauses outside art mode and during syncs. Older endpoints kept as deprecated wrappers. Fresh installs without uploaded_files.json no longer fail.
1.3.1 Real-time updates: the add-on reacts to the events the TV pushes (art_mode_changed, image_selected, go_to_standby, wakeup), so changes show up within about a second; checks remain as a safety net (30 s while the event connection is open, 10 s otherwise). The TV is checked every 10 s also in standby (back-off to 30 s only while it does not answer); the integration's current-image entity now changes as soon as the image changes, so dashboards reload the picture at once instead of after up to 5 minutes. /status reports tv_events and event_clients. Slideshow changes are logged with the endpoint that made them.