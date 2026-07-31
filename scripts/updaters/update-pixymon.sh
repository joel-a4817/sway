#!/usr/bin/env bash

term=foot

swaynag -t warning -y overlay \
    -m "Pixymon Updater" \
    -z "Update Pixymon and copy udev rules" \
    "$term -e bash -c 'cd ~/pixy2 && git pull --ff-only && cd scripts && ./build_pixymon_src.sh && cp ~/pixy2/src/host/linux/pixy.rules ~/nixos; echo; echo \"Remember to run a NixOS rebuild.\"; echo; read -n1 -rsp \"Press any key to close...\"'"
