#!/bin/bash

OUTPUT=$(/config/scripts/rclone sync "OneDrive:ImageCollection" /media/TheFrameImages \
  --config /config/scripts/rclone.conf \
  --log-level INFO 2>&1)

if [ $? -ne 0 ]; then
    exit 1  # rclone error
fi

if echo "$OUTPUT" | grep -q "Copied\|Deleted\|Renamed"; then
    echo "changed"
else
    echo "unchanged"
fi

exit 0
