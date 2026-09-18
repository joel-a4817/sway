#!/usr/bin/env bash
set -euo pipefail
LOG_DIR="$HOME/.local/state/audio"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/hyperx-cloud3.log"

mapfile -t candidates < <(pactl list short sinks | awk '{print $2}' | grep -Ei 'HyperX_Cloud_III|hyperx.*cloud.*iii' || true)
((${#candidates[@]})) || { echo "No HyperX Cloud III PipeWire sink found." >&2; exit 1; }
sink="${candidates[0]}"
pactl set-default-sink "$sink"
pactl set-sink-mute "$sink" 0 || true
while read -r id; do [[ -n "$id" ]] && pactl move-sink-input "$id" "$sink" || true; done < <(pactl list short sink-inputs | awk '{print $1}')
printf '[%s] sink=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$sink" >>"$LOG"
echo "HyperX Cloud III selected: $sink"
