#!/usr/bin/env bash
set -euo pipefail
MINECRAFT="/home/joel/.config/sway/scripts/network/minecraft-server.py"
IPAD="/home/joel/.config/sway/scripts/network/start-ipad-webremote.sh"
VNC_UNIT="camilladsp-wayvnc.service"

mapfile -t SERVERS < <("$MINECRAFT" list)

printf '%s\n' 'Toggle services'
printf '%s\n' 'Enter comma-separated numbers. [0] exits.'
printf '  [0] Exit\n'
if "$IPAD" --status >/dev/null 2>&1; then
  printf '  [1] iPad Web Remote [running]\n'
else
  printf '  [1] iPad Web Remote\n'
fi
if systemctl --user is-active --quiet "$VNC_UNIT"; then
  printf '  [2] VNC [running]\n'
else
  printf '  [2] VNC\n'
fi
for i in "${!SERVERS[@]}"; do
  name="${SERVERS[$i]}"
  number=$((i + 3))
  if systemctl is-active --quiet "minecraft-server-${name}.service"; then
    printf '  [%d] Minecraft: %s [running]\n' "$number" "$name"
  else
    printf '  [%d] Minecraft: %s\n' "$number" "$name"
  fi
done

read -r -p 'Selection: ' input
[[ -n "${input//[[:space:]]/}" ]] || exit 0
IFS=',' read -ra tokens <<< "$input"
selected=()
for token in "${tokens[@]}"; do
  token="${token//[[:space:]]/}"
  [[ "$token" =~ ^[0-9]+$ ]] || { printf 'Invalid selection: %s\n' "$token" >&2; exit 2; }
  (( token == 0 )) && exit 0
  (( token >= 1 && token <= ${#SERVERS[@]} + 2 )) || { printf 'Selection out of range: %s\n' "$token" >&2; exit 2; }
  for seen in "${selected[@]:-}"; do [[ "$seen" == "$token" ]] && continue 2; done
  selected+=("$token")
done

for token in "${selected[@]}"; do
  case "$token" in
    1) "$IPAD" toggle ;;
    2)
      if systemctl --user is-active --quiet "$VNC_UNIT"; then
        systemctl --user stop "$VNC_UNIT"
      else
        systemctl --user start "$VNC_UNIT"
      fi
      ;;
    *) "$MINECRAFT" toggle "${SERVERS[$((token - 3))]}" ;;
  esac
done
