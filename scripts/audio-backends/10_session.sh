#!/usr/bin/env bash
set -u

HOME_DIR="${HOME:?HOME is required}"
STATE_DIR="$HOME_DIR/.local/state/sway/media-control"
REMOTE_STATE="$HOME_DIR/.local/state/sway/camilladsp-webremote"
LEGACY_STATE="$HOME_DIR/.local/state/sway/audio"

# Prevent overlapping selectors from changing the graph concurrently.
mkdir -p "$STATE_DIR" "$REMOTE_STATE"
# Do not migrate an active old server's PID/markers: restart the paired server first.
legacy_server_pid="$(cat "$LEGACY_STATE/camilladsp-webremote/web-server.pid" 2>/dev/null || true)"
if [[ "$legacy_server_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$legacy_server_pid" 2>/dev/null; then
    echo 'Restart the updated webremote server before using the updated media-control.' >&2
    exit 1
fi
# First-run migration: copy persistent values without overwriting new state.
if [[ ! -e "$STATE_DIR/state-migrated" && -d "$LEGACY_STATE" ]]; then
    for old in "$LEGACY_STATE"/*; do
        [[ -f "$old" && ! -L "$old" ]] || continue
        case "${old##*/}" in audio-stopped|audio-stop-complete|audio-stop-capable.pid) target="$REMOTE_STATE/${old##*/}" ;; *) target="$STATE_DIR/${old##*/}" ;; esac
        [[ -e "$target" ]] || mv -- "$old" "$target" 2>/dev/null || true
    done
    if [[ -d "$LEGACY_STATE/camilladsp-webremote" ]]; then
        for old in "$LEGACY_STATE/camilladsp-webremote"/*; do
            [[ -f "$old" && ! -L "$old" ]] || continue
            target="$REMOTE_STATE/${old##*/}"
            [[ -e "$target" ]] || mv -- "$old" "$target" 2>/dev/null || true
        done
    fi
    touch "$STATE_DIR/state-migrated"
    rmdir "$LEGACY_STATE/camilladsp-webremote" "$LEGACY_STATE" 2>/dev/null || true
fi
# Serialize new launches separately from the lock shared with webremote startup.
LAUNCH_LOCK="$STATE_DIR/media-control-launch.lock"
OWNER_FILE="$STATE_DIR/media-control-owner"
LOCK_FILE="$STATE_DIR/media-control.lock"
exec 8>"$LAUNCH_LOCK"
flock -x 8 || exit 1
# Match a switch process by its script argument, not by its terminal or audio engine.
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
switch_process() {
    local pid="$1" arg comm
    [[ "$pid" =~ ^[1-9][0-9]*$ && "$pid" != "$$" && -r "/proc/$pid/cmdline" ]] || return 1
    comm="$(cat "/proc/$pid/comm" 2>/dev/null)"
    [[ "$comm" == bash ]] || return 1
    local -a args=()
    mapfile -d '' -t args <"/proc/$pid/cmdline" 2>/dev/null || return 1
    # The script must be Bash's script operand, not a path in unrelated arguments.
    arg="${args[1]:-}"
    [[ "$arg" == "$SCRIPT_PATH" ]] && return 0
    [[ "$(readlink -f -- "$arg" 2>/dev/null)" == "$SCRIPT_PATH" ]]
}
process_start() {
    local stat
    stat="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
    stat="${stat##*) }"
    set -- $stat
    printf '%s\n' "${20:-}"
}
# Replace only a verified switch process. The /proc start time protects
# against PID reuse; the scan also covers the first launch after upgrading.
for entry in /proc/[0-9]*; do
    previous="${entry##*/}"
    switch_process "$previous" || continue
    [[ "$(stat -c %u "$entry" 2>/dev/null)" == "$(id -u)" ]] || continue
    start="$(process_start "$previous")"
    [[ -n "$start" ]] || continue
    # Older copies do not handle USR2 and would leave their Swaynag child behind.
    while read -r child; do
        [[ "$child" =~ ^[1-9][0-9]*$ ]] && kill -TERM "$child" 2>/dev/null || true
    done < <(pgrep -P "$previous" -x swaynag 2>/dev/null || true)
    kill -USR2 "$previous" 2>/dev/null || kill -TERM "$previous" 2>/dev/null || true
    for ((i=0;i<30;i++)); do
        switch_process "$previous" || break
        sleep 0.1
    done
    if switch_process "$previous" && [[ "$(process_start "$previous")" == "$start" ]]; then
        kill -KILL "$previous" 2>/dev/null || true
    fi
done
exec 9>"$LOCK_FILE"
if ! flock -w 10 9; then
    echo 'Previous media-control or webremote startup still holds the audio lock.' >&2
    exit 1
fi
printf '%s %s\n' "$$" "$(process_start "$$")" >"$OWNER_FILE"
flock -u 8
exec 8>&-

ACTION_LOG="$STATE_DIR/media-control.log"
RESULT_FILE="$STATE_DIR/audio-toggle-complete.$$"
ACTION_STARTED_FILE="$STATE_DIR/audio-toggle-started.$$"
CARD_SELECTION_FILE="$STATE_DIR/audio-card-selected.$$"
SWAYNAG_LOG="$STATE_DIR/media-control-swaynag.$$"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
printf '[%s] media-control pid=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$$" >"$ACTION_LOG"
FINAL_PAUSE_REACHED=0
EXIT_HANDLER_RUNNING=0

cleanup() {
    if [[ "${SWAYNAG_PID:-}" =~ ^[1-9][0-9]*$ ]] &&
       [[ "$(ps -o ppid= -p "$SWAYNAG_PID" 2>/dev/null | tr -d ' ')" == "$$" ]] &&
       [[ "$(cat "/proc/$SWAYNAG_PID/comm" 2>/dev/null)" == swaynag ]]; then
        kill "$SWAYNAG_PID" 2>/dev/null || true
        wait "$SWAYNAG_PID" 2>/dev/null || true
    fi
    rm -f "$RESULT_FILE" "$ACTION_STARTED_FILE" "$CARD_SELECTION_FILE" "$SWAYNAG_LOG"
    local owner start
    if [[ -r "$OWNER_FILE" ]]; then
        read -r owner start <"$OWNER_FILE" || true
        if [[ "$owner" == "$$" && "$start" == "$(process_start "$$")" ]]; then rm -f -- "$OWNER_FILE"; fi
    fi
}
pause_before_close() {
    FINAL_PAUSE_REACHED=1
    flock -u 9 2>/dev/null || true
    exec 9>&-
    echo
    if [[ -t 0 && -r /dev/tty && -w /dev/tty ]]; then
        read -n 1 -r -s -p "Press any key to close..." </dev/tty || true
        echo >/dev/tty
    else
        echo "No controlling terminal is available."
    fi
}
handle_script_exit() {
    local status=$?
    (( EXIT_HANDLER_RUNNING )) && return
    EXIT_HANDLER_RUNNING=1
    trap - EXIT INT TERM HUP QUIT
    if [[ ${OUTPUT_PREVIEW_ACTIVE:-0} == 1 ]]; then
        python3 "$TOPOLOGY_SCRIPT" --cancel-output-preview >/dev/null 2>>"$ACTION_LOG" || echo 'Output preview rollback failed; inspect the log.' >&2
    fi
    if (( ! FINAL_PAUSE_REACHED && ! ${REPLACED:-0} )); then
        if (( status != 0 )); then
            echo; echo "Audio script exited unexpectedly."; echo "Exit status: $status"
            [[ -s "$ACTION_LOG" ]] && echo "Details: $ACTION_LOG"
        fi
        pause_before_close
    fi
    cleanup
    flock -u 9 2>/dev/null || true
    exec 9>&-
    builtin exit "$status"
}
trap handle_script_exit EXIT
trap 'REPLACED=1; exit 0' USR2
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap 'exit 131' QUIT

TOPOLOGY_SCRIPT="$HOME_DIR/.config/sway/scripts/network/camilladsp-server-sonobus.py"
[[ -f "$TOPOLOGY_SCRIPT" ]] || { echo "Missing paired server: $TOPOLOGY_SCRIPT" >&2; exit 1; }
# Opening Media Control is read-only; actions perform their own required transactions.
output_topology() { python3 "$TOPOLOGY_SCRIPT" --output-topology; }
run_quiet_action() {
    local output status
    output="$("$@" 2>&1)"; status=$?
    if (( status != 0 )); then
        printf '%s\n' "$output" >&2
        return "$status"
    fi
}
