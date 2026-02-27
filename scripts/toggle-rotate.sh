#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   toggle-rotate.sh [--mode 2|4] [OUTPUT]
#   default: --mode=4 and auto-detect focused output
#
#  --mode=2 : cycles normal <-> 180   (friendlier with touchpads)
#  --mode=4 : cycles normal -> 90 -> 180 -> 270 -> normal

MODE=4
if [[ "${1:-}" == "--mode" ]]; then
  MODE="${2:-4}"
  shift 2
fi

# Ensure jq exists
if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required (install jq)" >&2
  exit 1
fi

# Pick output: arg, focused, or first active
if [[ $# -ge 1 ]]; then
  out="$1"
else
  out=$(
    swaymsg -t get_tree \
    | jq -r '.. | objects | select(.focused == true) | .output // empty' \
    | head -n1
  )
  if [[ -z "${out:-}" ]]; then
    out=$(
      swaymsg -t get_outputs \
      | jq -r '[.[] | select(.active==true)][0].name // empty'
    )
  fi
fi

if [[ -z "${out:-}" ]]; then
  echo "Error: could not determine target output" >&2
  exit 1
fi

# Current transform
cur=$(
  swaymsg -t get_outputs \
  | jq -r --arg OUT "$out" '.[] | select(.name == $OUT) | .transform // empty'
)

case "${cur:-}" in
  normal|90|180|270) ;;  # ok
  *) cur="normal" ;;
esac

# Next transform
next=""
if [[ "$MODE" == "2" ]]; then
  case "$cur" in
    normal) next="180" ;;
    180)    next="normal" ;;
    90)     next="180" ;;   # normalize back to the 2-step lane
    270)    next="normal" ;;
  esac
else
  case "$cur" in
    normal) next="90" ;;
    90)     next="180" ;;
    180)    next="270" ;;
    270)    next="normal" ;;
  esac
fi

# Apply rotation
swaymsg output "$out" transform "$next" >/dev/null

# Map absolute devices (touchscreen/tablet) to the chosen output
map_ids=$(
  swaymsg -t get_inputs --raw \
  | jq -r '.[] | select(.type=="touch" or .type=="tablet_tool" or .type=="tablet_pad") | .identifier'
)

while IFS= read -r id; do
  [[ -z "$id" ]] && continue
  swaymsg input "$id" map_to_output "$out" >/dev/null || true
done <<< "$map_ids"
