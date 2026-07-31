#!/usr/bin/env bash

swaynag -t warning -y overlay \
    -m "NixOS Rebuild" \
    -z "Update flake and rebuild" \
    "$term -e bash -c 'nix flake update --flake ~/nixos; sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817; echo; read -n1 -rsp \"Press any key to close...\"'" \
    -z "Update flake and rebuild (low system load)" \
    "$term -e bash -c 'nix flake update --flake ~/nixos; sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817 --cores 1 --max-jobs 1; echo; read -n1 -rsp \"Press any key to close...\"'" \
    -z "Rebuild current configuration" \
    "$term -e bash -c 'sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817; echo; read -n1 -rsp \"Press any key to close...\"'" \
    -z "Rebuild current configuration (low system load)" \
    "$term -e bash -c 'sudo nixos-rebuild switch --impure --flake ~/nixos#rt4817 --cores 1 --max-jobs 1; echo; read -n1 -rsp \"Press any key to close...\"'"
