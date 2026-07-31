#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/music-dvd-complete.$$"
rm -f "$RESULT_FILE"

swaynag \
    -t warning \
    -y overlay \
    -m "Music DVD" \
    -z "Erase disc and burn current music library" \
'
dvd+rw-format -blank /dev/sr0

mkisofs -J -R -o /tmp/music.iso ~/Media/Music

growisofs -dvd-compat -Z /dev/sr0=/tmp/music.iso

rm -f /tmp/music.iso

touch "'"$RESULT_FILE"'"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

rm -f "$RESULT_FILE"

echo
read -n 1 -rsp "Press any key to close..."
