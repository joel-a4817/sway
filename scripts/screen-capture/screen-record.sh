#!/usr/bin/env bash

set -euo pipefail

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

mkdir -p ~/Media/Videos/Screen-recordings

FILE="~/Media/Videos/Screen-recordings/recording-$(date +%Y%m%d-%H%M%S).mkv"

if pgrep -x wf-recorder >/dev/null; then
    swaynag \
        -t warning \
        -o "$OUT" \
        -y overlay \
        -m "Recording in progress on $OUT" \
        -z "Stop Recording" "pkill -x wf-recorder"
else
    swaynag \
        -t warning \
        -o "$OUT" \
        -y overlay \
        -m "Start recording on $OUT" \
        -z "Without Audio" \
            "wf-recorder -a --audio-backend=pipewire -o \"$OUT\" -f \"$FILE\"" \
        -z "With Audio" \
            "wf-recorder -a -o \"$OUT\" -f \"$FILE\""
fi

