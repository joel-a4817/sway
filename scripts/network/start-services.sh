#!/usr/bin/env bash
set -euo pipefail
MINECRAFT="/home/joel/.config/sway/scripts/network/minecraft-server.py"
IPAD="/home/joel/.config/sway/scripts/network/start-ipad-webremote.sh"
VNC_UNIT="camilladsp-wayvnc.service"
PEERS="$HOME/.config/minecraft-server/peers"

mapfile -t SERVERS < <("$MINECRAFT" list)
declare -A LOCAL_RUNNING=()
declare -A PEER_RUNNING=()
peer_errors=()

# Local state comes straight from systemd, not from cached manager output.
for name in "${SERVERS[@]}"; do
  if systemctl is-active --quiet "minecraft-server-${name}.service"; then
    LOCAL_RUNNING["$name"]=1
  fi
done

# Remote state comes directly from each peer listed in the peers file.
if [[ -r "$PEERS" ]]; then
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    raw="${raw%%#*}"
    read -ra fields <<< "$raw"
    ((${#fields[@]} >= 2)) || continue
    label="${fields[0]//[|,;]/}"
    status=""
    detail=""
    for address in "${fields[@]:1}"; do
      address="${address//[|,;]/}"
      [[ -n "$address" ]] || continue
      output="$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$address" \
        '~/.config/sway/scripts/network/minecraft-server.py status --machine' 2>&1)" || rc=$?
      rc="${rc:-0}"
      # Select only a real machine-protocol line. Ignore login banners/noise.
      status="$(printf '%s\n' "$output" | awk '/^(running|stopped|transitioning)( |$)/ {line=$0} END {print line}')"
      if [[ -n "$status" ]]; then
        break
      fi
      detail="${address}: ${output:-exit $rc}"
      rc=0
    done
    if [[ -z "$status" ]]; then
      peer_errors+=("$label (${detail:-no usable address})")
      continue
    fi
    if [[ "$status" == running* ]]; then
      read -ra words <<< "$status"
      for word in "${words[@]:1}"; do
        [[ "$word" == floating=* ]] && continue
        PEER_RUNNING["$word"]="${PEER_RUNNING[$word]:+${PEER_RUNNING[$word]},}$label"
      done
    fi
  done < "$PEERS"
fi

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
  local_running="${LOCAL_RUNNING[$name]:-}"
  peer_running="${PEER_RUNNING[$name]:-}"
  if [[ -n "$local_running" && -n "$peer_running" ]]; then
    printf '  [%d] Minecraft: %s [CONFLICT: running locally and on %s]\n' "$number" "$name" "$peer_running"
  elif [[ -n "$local_running" ]]; then
    printf '  [%d] Minecraft: %s [running locally]\n' "$number" "$name"
  elif [[ -n "$peer_running" ]]; then
    printf '  [%d] Minecraft: %s [running on %s]\n' "$number" "$name" "$peer_running"
  else
    printf '  [%d] Minecraft: %s\n' "$number" "$name"
  fi
done
if ((${#peer_errors[@]})); then
  printf '  [!] Minecraft peer status unavailable: %s\n' "$(IFS='; '; echo "${peer_errors[*]}")"
fi

read -r -p 'Selection: ' input
[[ -n "${input//[[:space:]]/}" ]] || exit 0
[[ "$input" =~ ^[Ee]$ ]] && exit 0
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
