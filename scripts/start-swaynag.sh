#!/usr/bin/env bash

# Kill any existing swaynag
pkill swaynag 2>/dev/null || true
pkill wvkbd-mobintl 2>/dev/null ||true

# Small delay to ensure the old layer surface is gone
sleep 0.05

wvkbd-mobintl --hidden --alpha 216 -L 360 -H 640

exec swaynag \
  --edge bottom \
  --layer overlay \
  --output eDP-1 \
  --message "" \
  --button-no-terminal "⌨" "pkill -RTMIN wvkbd" \
  --background 000000AA \
  --button-background 222222CC \
  --text FFFFFF \
  --button-border-size 2 \
  --button-gap 48 \
  --border FF8800CC

