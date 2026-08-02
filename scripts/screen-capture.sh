#!/usr/bin/env bash

set -euo pipefail

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

mkdir -p "$HOME/Media/Videos/Screenrecordings"
mkdir -p "$HOME/Media/Pictures/Screenshots"

if pgrep -f wf-recorder >/dev/null; then
    swaynag \
        -t warning \
        -o $OUT \
        -y overlay \
        -m "Recording in progress on $OUT" \
        -z "Stop Recording" "pkill -f wf-recorder" \
        -z "Save & Copy Fullscreen" \
            "grim -o $OUT -t png - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy" \
        -z "Copy Fullscreen" \
            "grim -o $OUT -t png - | wl-copy" \
        -z "Save & Copy Selection" \
            "grim -t png -g \"\$(slurp)\" - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy" \
        -z "Copy Selection" \
            "grim -t png -g \"\$(slurp)\" - | wl-copy"

else
    swaynag \
        -t warning \
        -o $OUT \
        -y overlay \
        -m "Start recording on $OUT" \
        -z "With Audio" \
        "wf-recorder -a -o $OUT -f ~/Media/Videos/Screen-recordings/recording-\$(date +%Y%m%d-%H%M%S).mkv" \
        -z "Without Audio" \
            "wf-recorder -a --audio-backend=pipewire -o $OUT -f ~/Media/Videos/Screen-recordings/recording-\$(date +%Y%m%d-%H%M%S).mkv" \
        -z "Save & Copy Fullscreen" \
            "grim -o $OUT -t png - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy" \
        -z "Copy Fullscreen" \
            "grim -o $OUT -t png - | wl-copy" \
        -z "Save & Copy Selection" \
            "grim -t png -g \"\$(slurp)\" - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy" \
        -z "Copy Selection" \
        "grim -t png -g \"\$(slurp)\" - | wl-copy"



fi
