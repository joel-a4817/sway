#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/nixos-clean-complete.$$"
rm -f "$RESULT_FILE"

swaynag \
    -t warning \
    -y overlay \
    -m "NixOS Maintenance" \
    -z "Clean old generations and optimise store" \
'
sudo nix-collect-garbage -d

sudo nix store optimise

rm -f ~/.local/state/battery-interval-swaynag.log

touch "'"$RESULT_FILE"'"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

rm -f "$RESULT_FILE"

echo
read -n 1 -rsp "Press any key to close..."

