#!/usr/bin/env bash

set -euo pipefail

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

mkdir -p "~/Media/Pictures/Screenshots"

swaynag \
    -t warning \
    -y overlay \
    -o "$OUT" \
    -m "Screenshot" \
    -z "Copy Fullscreen" \
        "grim -o "$OUT" -t png - | wl-copy" \
    -z "Save & Copy Fullscreen" \
        "grim -o "$OUT" -t png - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy" \
    -z "Copy Selection" \
        "grim -o "$OUT" -t png -g "\$(slurp)" - | wl-copy" \
    -z "Save & Copy Selection" \
        "grim -o "$OUT" -t png -g "\$(slurp)" - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy"
