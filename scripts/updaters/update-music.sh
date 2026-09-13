#!/usr/bin/env bash

set -euo pipefail

src="$HOME/Downloads/Music/favourites"
playlist="$HOME/Downloads/Music/favourites.m3u"

{
    echo '#EXTM3U'

    find "$src" -maxdepth 1 -type f \
        \( -iname "*.m4a" \
        -o -iname "*.aac" \
        -o -iname "*.mp3" \
        -o -iname "*.flac" \
        -o -iname "*.wav" \
        -o -iname "*.ogg" \
        -o -iname "*.opus" \) |
    sort -V -r |
    while read -r file; do
        printf 'favourites/%s\n' "$(basename "$file")"
    done
} > "$playlist"

echo "playlist updated"
echo

read -n 1 -rsp "Press any key to close..."
