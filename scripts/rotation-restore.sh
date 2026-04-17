#!/usr/bin/env bash
set -euo pipefail

OUT="eDP-1"

# Capture current transform BEFORE reload
TRANSFORM="$(swaymsg -t get_outputs -r \
  | jq -r '.[] | select(.name=="'"$OUT"'") | .transform')"

# Reload sway (this resets output to normal on your system)
swaymsg reload

# Reapply the previous transform IF it was not normal
if [[ "$TRANSFORM" != "normal" ]]; then
  swaymsg output "$OUT" transform "$TRANSFORM"
fi
