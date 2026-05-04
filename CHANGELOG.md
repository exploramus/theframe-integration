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