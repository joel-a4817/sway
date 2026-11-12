#!/usr/bin/env bash
set -euo pipefail
MINECRAFT="/home/joel/.config/sway/scripts/network/minecraft-server.py"
IPAD="/home/joel/.config/sway/scripts/network/start-ipad-webremote.sh"
VNC_UNIT="camilladsp-wayvnc.service"

mapfile -t SERVERS < <("$MINECRAFT" list)
declare -A MC_STATUS=()
MC_PEER_ERROR=""
while IFS=$'\t' read -r server location; do
  [[ -n "$server" ]] || continue
  if [[ "$server" == "!peer" ]]; then
    MC_PEER_ERROR="$location"
    continue
  fi
  current="${MC_STATUS[$server]:-}"
  if [[ -z "$current" ]]; then
    MC_STATUS["$server"]="$location"
  elif [[ ",$current," != *",$location,"* ]]; then
    MC_STATUS["$server"]="$current,$location"
  fi
done < <("$MINECRAFT" menu-status 2>&1 || true)

printf '%s\n' 'Toggle services'
printf '%s\n' 'Enter comma-separated numbers. [0] exits.'
printf '  [E] Exit\n'
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
  state="${MC_STATUS[$name]:-}"
  case "$state" in
    # Test combined states before the broad peer:* pattern. Previously a state
    # ordered as peer:<host>,local was swallowed by peer:* and falsely displayed
    # only "running on <host>" instead of reporting the real dual-run conflict.
    *local,*peer:*|*peer:*,*local*)
      peer="${state#*peer:}"; peer="${peer%%,*}"
      printf '  [%d] Minecraft: %s [CONFLICT: running locally and on %s]\n' "$number" "$name" "$peer"
      ;;
    local) printf '  [%d] Minecraft: %s [running locally]\n' "$number" "$name" ;;
    peer:*) printf '  [%d] Minecraft: %s [running on %s]\n' "$number" "$name" "${state#peer:}" ;;
    *) printf '  [%d] Minecraft: %s\n' "$number" "$name" ;;
  esac
done
if [[ -n "$MC_PEER_ERROR" ]]; then
  printf '  [!] Minecraft peer status unavailable: %s\n' "$MC_PEER_ERROR"
fi

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
