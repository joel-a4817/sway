#!/usr/bin/env bash

set -euo pipefail

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

mkdir -p ~/Media/Videos/Screen-recordings

FILE="$HOME/Media/Videos/Screen-recordings/recording-$(date +%Y%m%d-%H%M%S).mkv"

if pgrep -x wf-recorder >/dev/null; then
    swaynag \
        -t warning \
        -o "$OUT" \
        -y overlay \
        -m "Recording in progress on $OUT" \
        -b "Stop Recording" "pkill -x wf-recorder"
else
    swaynag \
        -t warning \
        -o "$OUT" \
        -y overlay \
        -m "Start recording on $OUT" \
        -b "With Audio" \
            "wf-recorder -a --audio-backend=pipewire -o \"$OUT\" -f \"$FILE\"" \
        -b "Without Audio" \
            "wf-recorder -o \"$OUT\" -f \"$FILE\""
fi

