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

# --- Absolute input devices that must follow output rotation ---
# Built-in touchscreen (touch)
WACOM_TOUCH='1386:20983:Wacom_Pen_and_multitouch_sensor_Finger'
# Sunshine/Moonlight virtual touchscreen (touch)
SUN_TOUCH='48879:57005:Touch_passthrough'
# Sunshine/Moonlight absolute mouse (pointer, absolute) — optional transform
SUN_MOUSE_ABS='48879:57005:Mouse_passthrough_(absolute)'

INPUT_ABS_TOUCH_DEVICES=(
  "$WACOM_TOUCH"
  "$SUN_TOUCH"
)

INPUT_ABS_POINTER_DEVICES=(
  "$SUN_MOUSE_ABS"
)

# --- Helpers ---------------------------------------------------
exists_input() {
  local id="$1"
  swaymsg -t get_inputs -r | jq -e --arg id "$id" '.[] | select(.identifier==$id)' >/dev/null 2>&1
}

apply_input_mapping() {
  local id="$1" out="$2"
  if exists_input "$id"; then
    swaymsg -q input "$id" map_to_output "$out" || true
  fi
}

apply_input_transform() {
  local id="$1" xform="$2"
  if exists_input "$id"; then
    swaymsg -q input "$id" transform "$xform" || true
  fi
}

# --- Determine current and next rotation ----------------------
CUR="$(swaymsg -t get_outputs -r | jq -r --arg o "$OUT" '.[] | select(.name==$o) | (.transform // "normal")')"
case "$CUR" in normal|90|180|270) : ;; *) CUR="normal" ;; esac

case "$CUR" in
  normal) NEXT="90" ;;
  90)     NEXT="180" ;;
  180)    NEXT="270" ;;
  270)    NEXT="normal" ;;
esac

# Touchpad rotation (your Python tool wants the inverse mapping)
case "$NEXT" in
  normal) TPROT="0" ;;
  90)     TPROT="270" ;;
  180)    TPROT="180" ;;
  270)    TPROT="90" ;;
esac

# --- Apply output transform first ------------------------------
swaymsg -q "output $OUT transform $NEXT"

# --- Map/transform absolute devices to match output ------------
# 1) Ensure mapping to the correct output
for dev in "${INPUT_ABS_TOUCH_DEVICES[@]}"; do
  apply_input_mapping "$dev" "$OUT"
done
for dev in "${INPUT_ABS_POINTER_DEVICES[@]}"; do
  apply_input_mapping "$dev" "$OUT"
done

# 2) Apply transform matching the OUTPUT rotation
for dev in "${INPUT_ABS_TOUCH_DEVICES[@]}"; do
  apply_input_transform "$dev" "$NEXT"
done

# Optional: absolute mouse passthrough may also need transform
# If clicking from the phone is still off after the touch transforms above,
# uncomment the next line:
# apply_input_transform "$SUN_MOUSE_ABS" "$NEXT"

# --- Kick (restart) your touchpad rotator with the new angle ---
sudo -n "$PKILL" -f "$ROTATOR" >/dev/null 2>&1 || true
sudo -n "$SETSID" -f "$ROTATOR" --dev "$TPDEV" --rot "$TPROT" >>"$LOG" 2>&1 || {
  echo "Touchpad rotator failed. Check: $LOG" >&2
  exit 0
}

exit 0

