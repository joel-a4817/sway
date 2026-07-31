#!/usr/bin/env bash

term=foot

swaynag -t warning -y overlay \
    -m "NixOS Maintenance" \
    -z "Clean old generations and optimise store" \
    "$term -e bash -c 'sudo nix-collect-garbage -d; echo; sudo nix store optimise; rm -f ~/.local/state/battery-interval-swaynag.log; echo; read -n1 -rsp \"Press any key to close...\"'"
