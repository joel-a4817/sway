#!/usr/bin/env bash
set -euo pipefail

OUT="eDP-1"

need() { command -v "$1" >/dev/null 2>&1 || exit 1; }
need swaymsg
need jq

# Capture current transform BEFORE reload
TRANSFORM="$(swaymsg -t get_outputs -r \
  | jq -r '.[] | select(.name=="'"$OUT"'") | (.transform // "normal")')"

# Reload sway (this resets output + input state)
swaymsg reload

# Reapply the previous transform IF it was not normal
if [[ "$TRANSFORM" != "normal" ]]; then
  swaymsg output "$OUT" transform "$TRANSFORM"
fi

# Reapply pointer calibration matrix to match output transform
case "$TRANSFORM" in
  normal)
    swaymsg 'input type:pointer calibration_matrix 1 0 0 0 1 0'
    ;;
  90)
    swaymsg 'input type:pointer calibration_matrix 0 1 0 -1 0 1'
    ;;
  180)
    swaymsg 'input type:pointer calibration_matrix -1 0 1 0 -1 1'
    ;;
  270)
    swaymsg 'input type:pointer calibration_matrix 0 -1 1 1 0 0'
    ;;
esac
