#!/usr/bin/env bash
set -euo pipefail

OUT="eDP-1"   # keep it simple; you only use this display

ROTATOR="/home/joel/.config/sway/scripts/rotate-touchpad.py"
TPDEV="/dev/input/syna-touchpad"
LOG="/tmp/rotate-touchpad.log"

# EXACT paths from `sudo -l`
PKILL="/nix/store/k870vxfbxawhyx2726aygb5v5is4si4b-procps-4.0.6/bin/pkill"
SETSID="/nix/store/ilk5qzvkadnj7lx58hfinfvl7jmhriq6-util-linux-2.41.3-bin/bin/setsid"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
need swaymsg
need jq

# Current transform (normal|90|180|270)
CUR="$(swaymsg -t get_outputs -r | jq -r --arg o "$OUT" '.[] | select(.name==$o) | (.transform // "normal")')"
case "$CUR" in normal|90|180|270) : ;; *) CUR="normal" ;; esac

# Next transform (4-way cycle)
case "$CUR" in
  normal) NEXT="90" ;;
  90)     NEXT="180" ;;
  180)    NEXT="270" ;;
  270)    NEXT="normal" ;;
esac

# Inverse mapping so touchpad always FEELS like rotation=0
case "$NEXT" in
  normal) TPROT="0" ;;
  90)     TPROT="270" ;;
  180)    TPROT="180" ;;
  270)    TPROT="90" ;;
esac

# Rotate screen (sway supports normal/90/180/270) [3](https://www.mankier.com/1/swaymsg)
swaymsg -q "output $OUT transform $NEXT"

# Kill previous rotator (NOPASSWD-allowed), ignore if not running
sudo -n "$PKILL" -f "$ROTATOR" >/dev/null 2>&1 || true

# Start new rotator detached (NOPASSWD-allowed)
# The rotator must keep running to keep the uinput virtual device alive. [4](https://download.autodesk.com/global/docs/maya2014/en_us/files/Transforming_objects_Move_rotate_or_scale_components_with_reflection.htm)[5](https://www.youtube.com/watch?v=zkCnwc5i1GU)
sudo -n "$SETSID" -f "$ROTATOR" --dev "$TPDEV" --rot "$TPROT" >>"$LOG" 2>&1 || {
  echo "Touchpad rotator failed. Check: $LOG" >&2
  exit 0
}
