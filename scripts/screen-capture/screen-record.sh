#!/usr/bin/env bash

set -euo pipefail

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

mkdir -p "$HOME/Media/Videos/Screenrecordings"

if pgrep -f wf-recorder >/dev/null; then
    swaynag \
        -t warning \
        -o $OUT \
        -y overlay \
        -m "Recording in progress on $OUT" \
        -z "Stop Recording" "pkill -f wf-recorder"
else
    swaynag \
        -t warning \
        -o $OUT \
        -y overlay \
        -m "Start recording on $OUT" \
        -z "Without Audio" \
        "wf-recorder -a --audio-backend=pipewire -o $OUT -f ~/Media/Videos/Screen-recordings/recording-\$(date +%Y%m%d-%H%M%S).mkv" \
        -z "With Audio" \
        "wf-recorder -a -o $OUT -f ~/Media/Videos/Screen-recordings/recording-\$(date +%Y%m%d-%H%M%S).mkv"
fi
