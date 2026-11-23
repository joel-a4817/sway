#!/usr/bin/env bash
set -euo pipefail
DIR="$HOME/.config/sway/scripts/servers"
VNC="$HOME/.config/sway/scripts/network/toggle-wayvnc-headless.sh"
mapfile -d '' -t scripts < <(find "$DIR" -maxdepth 1 -type f \( -name '*.sh' -o -name '*.py' \) ! -name 'start-servers.sh' -print0 | sort -z)
entries=("$VNC" "${scripts[@]}")
printf 'Server and remote-access scripts\n\n[E] Exit\n'
for i in "${!entries[@]}"; do
  if (( i == 0 )); then label='Toggle VNC'; else label="${entries[$i]##*/}"; fi
  printf '[%d] %s\n' "$((i+1))" "$label"
done
printf '\n'; read -r -p 'Selection: ' choice
[[ "$choice" =~ ^[Ee]$ ]] && exit 0
[[ "$choice" =~ ^[0-9]+$ ]] || { echo 'Invalid selection.' >&2; exit 2; }
index=$((10#$choice-1)); (( index >= 0 && index < ${#entries[@]} )) || { echo 'Selection out of range.' >&2; exit 2; }
[[ -x "${entries[$index]}" ]] || { echo "Script is missing or not executable: ${entries[$index]}" >&2; exit 1; }
exec "${entries[$index]}"
