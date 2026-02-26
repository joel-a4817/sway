#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   toggle-rotate.sh            # auto-detect output of focused window
#   toggle-rotate.sh eDP-1      # or specify output explicitly

# Ensure jq is present
if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required (install jq)" >&2
  exit 1
fi

# Figure out which output to target
if [ $# -ge 1 ]; then
  out="$1"
else
  # Get the output name of the focused node
  out=$(
    swaymsg -t get_tree \
    | jq -r '.. | objects | select(.focused == true) | .output // empty' \
    | head -n1
  )
  # Fallback: pick the primary/first active output if focus lookup fails
  if [ -z "${out:-}" ]; then
    out=$(
      swaymsg -t get_outputs \
      | jq -r '[.[] | select(.active==true)][0].name // empty'
    )
  fi
fi

if [ -z "${out:-}" ]; then
  echo "Error: could not determine target output" >&2
  exit 1
fi

# Get current transform for that output
cur=$(
  swaymsg -t get_outputs \
  | jq -r --arg OUT "$out" '.[] | select(.name == $OUT) | .transform // empty'
)

# Normalize to our 4-step cycle
case "${cur:-}" in
  normal|90|180|270) ;;           # keep as-is
  *) cur="normal" ;;              # treat flipped/empty as normal
esac

# Compute next
case "$cur" in
  normal) next="90" ;;
  90)     next="180" ;;
  180)    next="270" ;;
  270)    next="normal" ;;
esac

# Apply
swaymsg output "$out" transform "$next" >/dev/null
