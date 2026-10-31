#!/usr/bin/env bash
# Toggle opacity of the focused window.
#   toggle-opacity.sh          toggle focused window
#   toggle-opacity.sh --watch  background: make for_window marks per-window
#
# Sway does not report opacity in get_tree, so marks track state. Sway marks
# are unique: when a for_window rule adds "opacity-dimmed" to a second window,
# sway removes it from the first. The watcher renames the shared mark to a
# per-window mark (opacity-dimmed_<con_id>) as soon as it is set.
set -euo pipefail

base=opacity-dimmed
dim=0.85
full=1.0
pid_file=${XDG_RUNTIME_DIR:-/tmp}/sway-toggle-opacity-watch.pid

watcher_running() {
    [[ -r $pid_file ]] || return 1
    local pid; pid=$(<"$pid_file")
    [[ $pid =~ ^[0-9]+$ && -r /proc/$pid/cmdline ]] || return 1
    tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q -- '__watch'
}

case ${1:-} in
--watch)
    # Replace any previous watcher (exec_always runs on every reload).
    if watcher_running; then kill -- "-$(<"$pid_file")" 2>/dev/null || true; fi
    setsid "$0" __watch </dev/null >/dev/null 2>&1 &
    echo $! >"$pid_file"
    exit 0 ;;
__watch)
    swaymsg -m -r -t subscribe '["window"]' \
        | jq --unbuffered -r --arg m "$base" '
            select(.change == "new" or .change == "mark")
            | .container | select((.marks // []) | index($m) != null) | .id' \
        | while read -r id; do
            [[ $id =~ ^[0-9]+$ ]] || continue
            swaymsg -q "[con_id=$id] unmark $base, mark --add ${base}_$id" || true
        done
    exit 0 ;;
"") ;;
*) echo "usage: ${0##*/} [--watch]" >&2; exit 2 ;;
esac

window=$(swaymsg -r -t get_tree | jq -ce '
    first(recurse(.nodes[]?, .floating_nodes[]?)
    | select(.focused == true and (.type == "con" or .type == "floating_con"))
    | {id, marks})') || exit 0
id=$(jq -r .id <<<"$window")
own=${base}_$id

# Dimmed if it has its own mark, or still holds the shared one (watcher race).
if jq -e --arg a "$own" --arg b "$base" \
    '.marks | (index($a) != null or index($b) != null)' <<<"$window" >/dev/null; then
    swaymsg -q "[con_id=$id] opacity set $full, unmark $own, unmark $base"
else
    swaymsg -q "[con_id=$id] opacity set $dim, mark --add $own"
fi
