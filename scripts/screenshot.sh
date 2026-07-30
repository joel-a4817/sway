#!/usr/bin/env bash

set -euo pipefail

swaynag \
    -t warning \
    -y overlay \
    -o "$OUT" \
    -m "Screenshot" \
    -b "Copy Fullscreen" \
        "grim -t png - | wl-copy" \
    -b "Save & Copy Fullscreen" \
        "mkdir -p ~/Media/Pictures/Screenshots && grim -t png - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy" \
    -b "Copy Selection" \
        "grim -t png -g \"\$(slurp)\" - | wl-copy" \
    -b "Save & Copy Selection" \
        "mkdir -p ~/Media/Pictures/Screenshots && grim -t png -g \"\$(slurp)\" - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy"
