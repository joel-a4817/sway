#!/usr/bin/env bash
set -euo pipefail
MINECRAFT="/home/joel/.config/sway/scripts/network/minecraft-server.py"
IPAD="/home/joel/.config/sway/scripts/network/start-ipad-webremote.sh"
mc=stopped
ipad=stopped
"$MINECRAFT" status --machine >/dev/null 2>&1 && mc=running || true
"$IPAD" --status >/dev/null 2>&1 && ipad=running || true
if [[ "$mc" == running || "$ipad" == running ]]; then
  [[ "$mc" == running ]] && "$MINECRAFT" stop
  [[ "$ipad" == running ]] && "$IPAD" toggle
else
  "$IPAD" toggle
  if ! "$MINECRAFT" start; then
    "$IPAD" toggle || true
    exit 1
  fi
fi
