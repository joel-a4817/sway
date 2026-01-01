# The new bind for toggling floating on and off
#!/usr/bin/env bash

STATE_DIR="$HOME/.cache/sway-float"
mkdir -p "$STATE_DIR"
LOCKFILE="$STATE_DIR/.lock"

# Get focused window info
info=$(swaymsg -t get_tree | jq -r '
    .. | objects | select(.focused == true)
    | {
        floating: .floating,
        fullscreen: .fullscreen_mode,
        rect: .rect,
        app_id: .app_id,
        class: .window_properties.class,
        pid: .pid
    }')

floating=$(jq -r '.floating' <<<"$info")
fullscreen=$(jq -r '.fullscreen' <<<"$info")
app_id=$(jq -r '.app_id // empty' <<<"$info")
class=$(jq -r '.class // empty' <<<"$info")
pid=$(jq -r '.pid // empty' <<<"$info")

# Determine key for geometry
if [[ -n "$app_id" ]]; then
    key="app_$app_id"
elif [[ -n "$class" ]]; then
    key="class_$class"
else
    key="pid_$pid"
fi

restore_geometry() {
    geom="$STATE_DIR/$key.json"
    [[ ! -f "$geom" ]] && return

    x=$(jq '.x' "$geom")
    y=$(jq '.y' "$geom")
    w=$(jq '.width' "$geom")
    h=$(jq '.height' "$geom")

    swaymsg "resize set width $w height $h"
    swaymsg "move position $x $y"
}

# --- Fullscreen → Floating ---
if [[ "$fullscreen" != "0" ]]; then
    # Lock watcher to prevent interference
    touch "$LOCKFILE"

    # Exit fullscreen first
    swaymsg fullscreen disable

    # Short delay to let Sway process fullscreen exit
    sleep 0.08

    # Enable floating and restore geometry
    swaymsg floating enable
    restore_geometry

    # Small extra delay to let floating settle
    sleep 0.07
    rm "$LOCKFILE"
    exit 0
fi

# --- Floating → Tiling ---
if [[ "$floating" == "user_on" ]]; then
    swaymsg floating disable
    exit 0
fi

# --- Tiling → Floating ---
swaymsg floating enable
restore_geometry

