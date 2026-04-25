#!/usr/bin/env bash
set -euo pipefail

OUT="eDP-1"

ROTATOR="/home/joel/.config/sway/scripts/rotate-touchpad.py"
TPDEV="/dev/input/touchpad-internal"
LOG="/tmp/rotate-touchpad.log"

MOUSE_ROTATOR="/home/joel/.config/sway/scripts/rotate-mouse.py"
MOUSE_DEV="/dev/input/mouse-internal"
MOUSE_LOG="/tmp/rotate-mouse.log"

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
  normal) NEXT="90" ;;
  90)     NEXT="180" ;;
  180)    NEXT="270" ;;
  270)    NEXT="normal" ;;
esac

# --- Apply output transform first ------------------------------
swaymsg -q "output $OUT transform $NEXT"
