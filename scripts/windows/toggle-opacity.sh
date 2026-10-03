#!/usr/bin/env bash
set -euo pipefail

# Sway does not report opacity in get_tree. The mark is the tracked state.
mark=opacity-dimmed
dim=0.85
full=1.0

tree=$(swaymsg -r -t get_tree)
window=$(jq -cer 'recurse(.nodes[]?, .floating_nodes[]?) | select(.focused == true and (.type == "con" or .type == "floating_con")) | {id, marks}' <<< "$tree") || exit 0
id=$(jq -r '.id' <<< "$window")
if jq -e --arg mark "$mark" '.marks | index($mark) != null' <<< "$window" >/dev/null; then
    swaymsg -q "[con_id=$id] opacity set $full, unmark $mark"
else
    swaymsg -q "[con_id=$id] opacity set $dim, mark --add $mark"
fi
