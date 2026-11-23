#!/usr/bin/env bash
set -euo pipefail
DIR="$HOME/.config/sway/scripts/servers"
mapfile -d '' -t scripts < <(find "$DIR" -maxdepth 1 -type f \( -name '*.sh' -o -name '*.py' \) ! -name 'start-servers.sh' -print0 | sort -z)
printf 'Server scripts\n\n[E] Exit\n'
for i in "${!scripts[@]}"; do printf '[%d] %s\n' "$((i+1))" "${scripts[$i]##*/}"; done
printf '\n'; read -r -p 'Selection: ' choice
[[ "$choice" =~ ^[Ee]$ ]] && exit 0
[[ "$choice" =~ ^[0-9]+$ ]] || { echo 'Invalid selection.' >&2; exit 2; }
index=$((10#$choice-1)); (( index >= 0 && index < ${#scripts[@]} )) || { echo 'Selection out of range.' >&2; exit 2; }
exec "${scripts[$index]}"
