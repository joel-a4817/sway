#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/nixos-rebuild-complete.$$"
rm -f "$RESULT_FILE"

export RESULT_FILE

swaynag \
    -t warning \
    -y overlay \
    -m "NixOS Rebuild" \
    -z "Update flake and rebuild" \
'
nix flake update --flake ~/nixos
sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817
touch "$RESULT_FILE"
' \
    -z "Update flake and rebuild (low system load)" \
'
nix flake update --flake ~/nixos
sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817 --cores 1 --max-jobs 1
touch "$RESULT_FILE"
' \
    -z "Rebuild current configuration" \
'
sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817
touch "$RESULT_FILE"
' \
    -z "Rebuild current configuration (low system load)" \
'
sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817 --cores 1 --max-jobs 1
touch "$RESULT_FILE"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

rm -f "$RESULT_FILE"

echo
read -n 1 -rsp "Press any key to close..."
