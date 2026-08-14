#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/yt-update-complete.$$"
MODE_FILE="/tmp/yt-mode.$$"

rm -f "$RESULT_FILE" "$MODE_FILE"

swaynag \
    -t warning \
    -y overlay \
    -m "Music Library" \
    -z "Download latest tracks from YouTube playlist" '
echo playlist > "'"$MODE_FILE"'"
touch "'"$RESULT_FILE"'"
' \
    -z "Download single track" '
echo single > "'"$MODE_FILE"'"
touch "'"$RESULT_FILE"'"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

MODE=$(cat "$MODE_FILE")

rm -f "$RESULT_FILE" "$MODE_FILE"

case "$MODE" in
    playlist)
        yt-dlp \
            --yes-playlist \
            -x \
            --audio-format mp3 \
            --audio-quality 0 \
            --force-overwrites \
            -P ~/Downloads/Music \
            "https://youtube.com/playlist?list=PLPxzU5kNqyqU&si=LJw9jQ6tvBfz5qAe"
        ;;
    single)
        printf "\nPaste YouTube URL: "
        read -r URL

        [[ -n "$URL" ]] || exit 0

        yt-dlp \
            -x \
            --audio-format mp3 \
            --audio-quality 0 \
            --force-overwrites \
            -P ~/Downloads/Music \
            "$URL"
        ;;
esac

echo
read -n 1 -rsp "Press any key to close..."
