#!/usr/bin/env bash
set -euo pipefail
mkdir -p "/home/joel/.local/state/sway/rotation"
OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"
ROTATOR="/home/joel/.config/sway/scripts/rotation/rotate-touchpad.py"
TPDEV="/dev/input/touchpad-internal"
LOG="/home/joel/.local/state/sway/rotation/rotate-touchpad.log"
MOUSE_ROTATOR="/home/joel/.config/sway/scripts/rotation/rotate-mouse.py"
MOUSE_DEV="/dev/input/mouse-internal"
MOUSE_LOG="/home/joel/.local/state/sway/rotation/rotate-mouse.log"
PKILL="/run/current-system/sw/bin/pkill"
SETSID="/run/current-system/sw/bin/setsid"
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
need swaymsg
need jq
# --- Helpers ---------------------------------------------------
# (kept for symmetry / future use; not used by design)
# --- Determine current and next rotation (CLOCKWISE) ----------
CUR="$(swaymsg -t get_outputs -r |
  jq -r --arg o "$OUT" '.[] | select(.name==$o) | (.transform // "normal")')"
case "$CUR" in normal|90|180|270) : ;; *) CUR="normal" ;; esac
case "$CUR" in
  normal) NEXT="270" ;;
  90)     NEXT="normal" ;;
  180)    NEXT="90" ;;
  270)    NEXT="180" ;;
esac
# --- Apply output transform first ------------------------------
swaymsg -q "output $OUT transform $NEXT"

# Apply this last: earlier output/input changes (or a Sway reload) can reset mapping.
# Check Sway's command reply rather than hiding it with -q.
VNC_POINTER="0:0:wlr_virtual_pointer_v1"
if ! swaymsg -t get_inputs -r | jq -e --arg id "$VNC_POINTER" \
    'any(.[]; .identifier == $id)' >/dev/null; then
  echo "wayvnc virtual pointer not present; map it after wayvnc connects" >&2
  exit 1
fi
reply="$(swaymsg -r "input \"$VNC_POINTER\" map_to_output $OUT")"
if ! printf '%s\n' "$reply" | jq -e \
    'type == "array" and length > 0 and all(.[]; .success == true)' >/dev/null; then
  echo "VNC pointer mapping failed: $reply" >&2
  exit 1
fi
