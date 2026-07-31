#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/yt-update-complete.$$"
rm -f "$RESULT_FILE"

swaynag \
    -t warning \
    -y overlay \
    -m "Music Library" \
    -z "Download latest tracks from YouTube playlist" \
'
yt-dlp \
    --yes-playlist \
    -x \
    --audio-format mp3 \
    --audio-quality 0 \
    --force-overwrites \
    -P ~/Media/Music \
    "https://youtube.com/playlist?list=PLPxzU5kNqyqU&si=LJw9jQ6tvBfz5qAe"

touch "'"$RESULT_FILE"'"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

rm -f "$RESULT_FILE"

echo
read -n 1 -rsp "Press any key to close..."
