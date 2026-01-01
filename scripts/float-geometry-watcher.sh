#!/usr/bin/env bash

STATE_DIR="$HOME/.cache/sway-float"
mkdir -p "$STATE_DIR"

LOCKFILE="$STATE_DIR/.lock"

while true; do
    # Get currently focused window
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

    # Only record REAL floating geometry and if watcher is not locked
    if [[ "$floating" == "user_on" && "$fullscreen" == "0" && ! -f "$LOCKFILE" ]]; then
        app_id=$(jq -r '.app_id // empty' <<<"$info")
        class=$(jq -r '.class // empty' <<<"$info")
        pid=$(jq -r '.pid // empty' <<<"$info")

        if [[ -n "$app_id" ]]; then
            key="app_$app_id"
        elif [[ -n "$class" ]]; then
            key="class_$class"
        else
            key="pid_$pid"
        fi

        # Only write if rect changed
        old_rect_file="$STATE_DIR/$key.json"
        new_rect=$(jq '.rect' <<<"$info")
        if [[ ! -f "$old_rect_file" ]] || ! cmp -s <(echo "$new_rect") "$old_rect_file"; then
            echo "$new_rect" > "$old_rect_file"
        fi
    fi

    sleep 0.15
done

