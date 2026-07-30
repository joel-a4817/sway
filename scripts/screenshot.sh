#!/usr/bin/env bash

set -euo pipefail

swaynag \
    -t warning \
    -y overlay \
    -o "$OUT" \
    -m "Screenshot" \
    -z "Copy Fullscreen" \
        "grim -t png - | wl-copy" \
    -z "Save & Copy Fullscreen" \
        "mkdir -p ~/Media/Pictures/Screenshots && grim -t png - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy" \
    -z "Copy Selection" \
        "grim -t png -g \"\$(slurp)\" - | wl-copy" \
    -z "Save & Copy Selection" \
        "mkdir -p ~/Media/Pictures/Screenshots && grim -t png -g \"\$(slurp)\" - | tee ~/Media/Pictures/Screenshots/screenshot-\$(date +%Y%m%d-%H%M%S).png | wl-copy"
