#!/usr/bin/env bash
set -euo pipefail
LOG_DIR="$HOME/.local/state/audio"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/hyperx-earpods.log"

mapfile -t candidates < <(pactl list short sinks | awk '{print $2}' | grep -Ei 'earpods|Headphones|analog-stereo' || true)
((${#candidates[@]})) || { echo "No EarPods/headphones PipeWire sink found." >&2; exit 1; }
if ((${#candidates[@]} > 1)); then sink="$(printf '%s\n' "${candidates[@]}" | shuf -n1)"; else sink="${candidates[0]}"; fi
pactl set-default-sink "$sink"
pactl set-sink-mute "$sink" 0 || true
while read -r id; do [[ -n "$id" ]] && pactl move-sink-input "$id" "$sink" || true; done < <(pactl list short sink-inputs | awk '{print $1}')
printf '[%s] sink=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$sink" >>"$LOG"
echo "EarPods/headphones selected: $sink"
