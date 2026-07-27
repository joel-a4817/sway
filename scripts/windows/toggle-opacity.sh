#!/usr/bin/env bash

MARK="dimmed"

if swaymsg -t get_marks | jq -e 'index("dimmed")' >/dev/null; then
    swaymsg unmark dimmed
    swaymsg opacity 1.0
else
    swaymsg mark dimmed
    swaymsg opacity 0.85
fi
