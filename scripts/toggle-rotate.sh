#!/usr/bin/env bash
set -euo pipefail

OUT="eDP-1"

ROTATOR="/home/joel/.config/sway/scripts/rotate-touchpad.py"
TPDEV="/dev/input/touchpad-internal"
LOG="/tmp/rotate-touchpad.log"

PKILL="/run/current-system/sw/bin/pkill"
SETSID="/run/current-system/sw/bin/setsid"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
need swaymsg
need jq

CUR="$(swaymsg -t get_outputs -r | jq -r --arg o "$OUT" '.[] | select(.name==$o) | (.transform // "normal")')"
case "$CUR" in normal|90|180|270) : ;; *) CUR="normal" ;; esac

case "$CUR" in
  normal) NEXT="90" ;;
  90)     NEXT="180" ;;
  180)    NEXT="270" ;;
  270)    NEXT="normal" ;;
esac

case "$NEXT" in
  normal) TPROT="0" ;;
  90)     TPROT="270" ;;
  180)    TPROT="180" ;;
  270)    TPROT="90" ;;
esac

swaymsg -q "output $OUT transform $NEXT"

sudo -n "$PKILL" -f "$ROTATOR" >/dev/null 2>&1 || true

sudo -n "$SETSID" -f "$ROTATOR" --dev "$TPDEV" --rot "$TPROT" >>"$LOG" 2>&1 || {
  echo "Touchpad rotator failed. Check: $LOG" >&2
  exit 0
}
