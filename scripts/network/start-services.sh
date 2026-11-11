#!/usr/bin/env bash
set -euo pipefail
MINECRAFT="/home/joel/.config/sway/scripts/network/minecraft-server.py"
IPAD="/home/joel/.config/sway/scripts/network/start-ipad-webremote.sh"
mapfile -t SERVERS < <("$MINECRAFT" list)

printf '%s\n' 'Choose the services that should be running.'
printf '%s\n' 'Enter comma-separated numbers. Empty input cancels.'
if "$IPAD" --status >/dev/null 2>&1; then
  printf '  0) iPad Web Remote [running]\n'
else
  printf '  0) iPad Web Remote\n'
fi
for i in "${!SERVERS[@]}"; do
  name="${SERVERS[$i]}"
  if systemctl is-active --quiet "minecraft-server-${name}.service"; then
    printf '  %d) Minecraft: %s [running]\n' "$((i + 1))" "$name"
  else
    printf '  %d) Minecraft: %s\n' "$((i + 1))" "$name"
  fi
done
read -r -p 'Selection: ' INPUT
[[ -n "${INPUT//[[:space:]]/}" ]] || exit 0

WANT_IPAD=0
WANTED=()
IFS=',' read -ra TOKENS <<< "$INPUT"
for token in "${TOKENS[@]}"; do
  token="${token//[[:space:]]/}"
  [[ "$token" =~ ^[0-9]+$ ]] || { printf 'Invalid selection: %s\n' "$token" >&2; exit 2; }
  if (( token == 0 )); then
    WANT_IPAD=1
  elif (( token >= 1 && token <= ${#SERVERS[@]} )); then
    WANTED+=("${SERVERS[$((token - 1))]}")
  else
    printf 'Selection out of range: %s\n' "$token" >&2; exit 2
  fi
done

# Stop Minecraft servers omitted from the desired set.
mapfile -t RUNNING < <(systemctl list-units --state=active --type=service --no-legend 'minecraft-server-*.service' | sed -n 's/^[[:space:]]*minecraft-server-\([^ ]*\)\.service.*/\1/p')
for running in "${RUNNING[@]}"; do
  keep=0
  for wanted in "${WANTED[@]}"; do [[ "$running" == "$wanted" ]] && keep=1; done
  (( keep )) || "$MINECRAFT" stop "$running"
done

# Start selected servers that are not already running. Multiple are allowed.
for wanted in "${WANTED[@]}"; do
  systemctl is-active --quiet "minecraft-server-${wanted}.service" || "$MINECRAFT" start "$wanted"
done

if (( WANT_IPAD )); then
  "$IPAD" --status >/dev/null 2>&1 || "$IPAD" toggle
else
  "$IPAD" --status >/dev/null 2>&1 && "$IPAD" toggle || true
fi
