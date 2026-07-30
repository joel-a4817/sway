#!/usr/bin/env bash

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

swaynag \
  -t warning \
  -y overlay \
  -o "$OUT" \
  -m 'Device Off Options' \
  -B 'Sleep' 'rm -rf ~/.local/state/battery-interval-swaynag.log && swaylock -F -fi /home/joel/.config/sway/mcqueen.jpeg && systemctl suspend' \
  -B 'Exit Sway' 'rm -rf ~/.local/state/battery-interval-swaynag.log && swaymsg exit' \
  -B 'Reboot' 'rm -rf ~/.local/state/battery-interval-swaynag.log && reboot' \
