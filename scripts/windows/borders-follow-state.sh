#Changes border when I float/tile windows


#!/usr/bin/env bash
set -euo pipefail

# Match your toggle script's lock (optional, but now it actually works)
LOCKFILE="$HOME/.cache/sway-float/.lock"

TILED_BORDER='border pixel 2'
FLOAT_BORDER='border pixel 8'

get_floating_state_by_id() {
  local id="$1"
  swaymsg -t get_tree | jq -r --argjson id "$id" '
    .. | objects | select(.id? == $id) | .floating // empty
  ' | head -n1
}

apply_border() {
  local id="$1"
  local floating="$2"

  if [[ "$floating" == *_on ]]; then
    swaymsg "[con_id=$id] $FLOAT_BORDER" >/dev/null
  else
    swaymsg "[con_id=$id] $TILED_BORDER" >/dev/null
  fi
}

swaymsg -m -t subscribe '["window"]' \
  | stdbuf -oL -eL jq -cr '
      select(.change=="new" or .change=="floating")
      | {change: .change, id: .container.id, floating: (.container.floating // "")}
    ' \
  | while read -r ev; do
      # If your fullscreen->floating script is in the middle of a transition, skip
      [[ -e "$LOCKFILE" ]] && continue

      change="$(jq -r '.change' <<<"$ev")"
      id="$(jq -r '.id' <<<"$ev")"
      floating="$(jq -r '.floating' <<<"$ev")"

      if [[ "$change" == "new" ]]; then
        # Let Sway finish mapping + applying rules
        sleep 0.05
        floating="$(get_floating_state_by_id "$id")"
      fi

      # If we still couldn't read state, default to tiled
      [[ -z "${floating:-}" ]] && floating="user_off"

      apply_border "$id" "$floating"
    done

