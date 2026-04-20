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

# --- Determine current and next rotation (ANTI-CLOCKWISE) -----
CUR="$(swaymsg -t get_outputs -r |
  jq -r --arg o "$OUT" '.[] | select(.name==$o) | (.transform // "normal")')"
case "$CUR" in normal|90|180|270) : ;; *) CUR="normal" ;; esac

case "$CUR" in
  normal) NEXT="270" ;;
  270)    NEXT="180" ;;
  180)    NEXT="90" ;;
  90)     NEXT="normal" ;;
esac

# Touchpad & mouse evdev rotators expect inverse screen rotation
case "$NEXT" in
  normal) TPROT="0" ;;
  270)    TPROT="90" ;;
  180)    TPROT="180" ;;
  90)     TPROT="270" ;;
esac

# --- Apply output transform first ------------------------------
swaymsg -q "output $OUT transform $NEXT"

# --- Apply pointer calibration matrix (ALL pointers) -----------
case "$NEXT" in
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

# --- Kick (restart) your touchpad rotator ----------------------
sudo -n "$PKILL" -f "$ROTATOR" >/dev/null 2>&1 || true
sudo -n "$SETSID" -f "$ROTATOR" \
  --dev "$TPDEV" \
  --rot "$TPROT" >>"$LOG" 2>&1 || {
    echo "Touchpad rotator failed. Check: $LOG" >&2
    exit 0
  }

# --- Kick (restart) your mouse rotator -------------------------
sudo -n "$PKILL" -f "$MOUSE_ROTATOR" >/dev/null 2>&1 || true
sudo -n "$SETSID" -f "$MOUSE_ROTATOR" \
  --dev "$MOUSE_DEV" \
  --rot "$TPROT" >>"$MOUSE_LOG" 2>&1 || {
    echo "Mouse rotator failed. Check: $MOUSE_LOG" >&2
    exit 0
  }
