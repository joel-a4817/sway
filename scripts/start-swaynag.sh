#!/usr/bin/env bash

pkill swaynag 2>/dev/null || true
pkill wvkbd-deskintl 2>/dev/null || true

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

# Get focused output dimensions
read WIDTH HEIGHT < <(
    swaymsg -t get_outputs -r |
    jq -r '.[] | select(.focused) | "\(.current_mode.width) \(.current_mode.height)"'
)

# Half-sized keyboard
L=$(( HEIGHT / 2 ))
H=$(( WIDTH / 2 ))

sleep 0.05

wvkbd-deskintl \
    --hidden \
    --alpha 216 \
    -L "$L" \
    -H "$H" \
    --fn "JetBrainsMono Nerd Font Mono 16"

exec swaynag \
    --edge bottom \
    --layer overlay \
    --output "$OUT" \
    --message "" \
    --button-no-terminal " ⌨ " "pkill -RTMIN wvkbd" \
    --background 000000AA \
    --button-background 222222CC \
    --text FFFFFF \
    --button-border-size 2 \
    --button-gap 48 \
    --border FF8800CC \
    --font "JetBrainsMono Nerd Font Mono 16"
