#!/usr/bin/env bash
set -u

HOME_DIR="${HOME:?HOME is required}"
STATE_DIR="$HOME_DIR/.local/state/sway/media-control"
REMOTE_STATE="$HOME_DIR/.local/state/sway/audio-runtime"
LEGACY_STATE="$HOME_DIR/.local/state/sway/audio"

# Prevent overlapping selectors from changing the graph concurrently.
mkdir -p "$STATE_DIR" "$REMOTE_STATE"
# Process replacement belongs to the installed launchers. This sourced
# backend owns session locking, migration, rollback and cleanup only.
# First-run migration: copy persistent values without overwriting new state.
if [[ ! -e "$STATE_DIR/state-migrated" && -d "$LEGACY_STATE" ]]; then
    for old in "$LEGACY_STATE"/*; do
        [[ -f "$old" && ! -L "$old" ]] || continue
        case "${old##*/}" in audio-stopped|audio-stop-complete|audio-stop-capable.pid) target="$REMOTE_STATE/${old##*/}" ;; *) target="$STATE_DIR/${old##*/}" ;; esac
        [[ -e "$target" ]] || mv -- "$old" "$target" 2>/dev/null || true
    done
    if [[ -d "$LEGACY_STATE/audio-runtime" ]]; then
        for old in "$LEGACY_STATE/audio-runtime"/*; do
            [[ -f "$old" && ! -L "$old" ]] || continue
            target="$REMOTE_STATE/${old##*/}"
            [[ -e "$target" ]] || mv -- "$old" "$target" 2>/dev/null || true
        done
    fi
    touch "$STATE_DIR/state-migrated"
    rmdir "$LEGACY_STATE/audio-runtime" "$LEGACY_STATE" 2>/dev/null || true
fi
# Serialize new launches separately from the lock shared with webremote startup.
LAUNCH_LOCK="$STATE_DIR/media-control-launch.lock"
OWNER_FILE="$STATE_DIR/media-control-owner"
LOCK_FILE="$STATE_DIR/media-control.lock"
exec 8>"$LAUNCH_LOCK"
flock -x 8 || exit 1
process_start() {
    local stat
    stat="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
    stat="${stat##*) }"
    set -- $stat
    printf '%s\n' "${20:-}"
}
exec 9>"$LOCK_FILE"
if ! flock -w 10 9; then
    echo 'Previous Media Control action still holds the audio lock.' >&2
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
declare -a AUTO_SELECTED_SUMMARY=()

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
    # Media Control is often launched with stdin redirected even though its
    # terminal still exists. Testing `-t 0` made completed actions skip this
    # acknowledgement and let foot close immediately. Read from the controlling
    # terminal directly for every completed action, whether it succeeded or not.
    if [[ -r /dev/tty && -w /dev/tty ]]; then
        printf '%s' "Press any key to close..." >/dev/tty
        IFS= read -n 1 -r -s </dev/tty || true
        printf '\n' >/dev/tty
    else
        printf '%s\n' "Press Enter to close..."
        IFS= read -r || true
    fi
}
handle_script_exit() {
    local status=$?
    (( EXIT_HANDLER_RUNNING )) && return
    EXIT_HANDLER_RUNNING=1
    trap - EXIT INT TERM HUP QUIT
    if [[ ${OUTPUT_PREVIEW_ACTIVE:-0} == 1 ]]; then
        python3 "$TOPOLOGY_SCRIPT" --cancel-output-preview >/dev/null 2>>"$ACTION_LOG" || true
    fi
    if (( ! FINAL_PAUSE_REACHED && ! ${REPLACED:-0} )); then
        # Ctrl-C is a user cancellation, not a backend failure.
        if (( status != 0 && status != 130 )); then
            echo; echo "Audio script exited unexpectedly."; echo "Exit status: $status"
            [[ -s "$ACTION_LOG" ]] && echo "Details: $ACTION_LOG"
        fi
        if ((${#AUTO_SELECTED_SUMMARY[@]})); then
            echo
            echo "Automatically selected because only one valid option was available:"
            printf '%s\n' "${AUTO_SELECTED_SUMMARY[@]}" | awk '!seen[$0]++ { print "  " $0 }'
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

# Media Control executes the shared backend locally. The web server loads the
# same manifest independently, but it is not a dependency of this interface.
TOPOLOGY_SCRIPT="$HOME_DIR/.config/sway/scripts/audio-backends/audio-backend-cli.py"
[[ -f "$TOPOLOGY_SCRIPT" ]] || { echo "Missing local audio backend CLI: $TOPOLOGY_SCRIPT" >&2; exit 1; }
if ! declare -F pause_all_playback >/dev/null 2>&1; then
    pause_all_playback() {
        command -v playerctl >/dev/null 2>&1 && playerctl --all-players pause >/dev/null 2>&1 || true
        command -v python3 >/dev/null 2>&1 || return 0
        python3 - <<'PYPAUSE' >/dev/null 2>&1 || true
import glob,json,os,socket
paths={'/tmp/mpv-socket','/tmp/mpvsocket','/tmp/mpv.sock'}
runtime=os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
paths.update(glob.glob(runtime+'/mpv*'));paths.update(glob.glob(runtime+'/media-control-mpv/*.sock'))
try:
    for line in open('/proc/net/unix',encoding='utf-8',errors='ignore'):
        fields=line.split()
        if len(fields)>=8 and 'mpv' in fields[-1].lower() and fields[-1].startswith('/'):
            paths.add(fields[-1])
except OSError: pass
payload=(json.dumps({'command':['set_property','pause',True]})+'\n').encode()
for path in paths:
    try:
        client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);client.settimeout(.25)
        client.connect(path);client.sendall(payload);client.close()
    except OSError: pass
PYPAUSE
    }
fi
normalize_menu_step() {
    # This file executes before 20_menu_helpers.sh is sourced, so the shared
    # pause/normalize boundary must be defined here rather than in that later file.
    pause_all_playback
    python3 "$TOPOLOGY_SCRIPT" --normalize-menu-open >>"$ACTION_LOG" 2>&1 || {
        printf '%s\n' 'Volume normalization failed; the selection was not applied.' >&2
        return 1
    }
}
# A deleted baseline is repaired through the same backend used by server startup.
# The shared Python backend owns the complete expected-state list. Always ask it
# to validate the baseline so one missing file cannot be overlooked here.
python3 "$TOPOLOGY_SCRIPT" --ensure-audio-baseline >>"$ACTION_LOG" 2>&1 || {
    cat "$ACTION_LOG" >&2
    exit 1
}
# Opening Media Control verifies gains through the standard transaction:
# pause, save master, no-op function boundary, normalize, restore master last.
normalize_menu_step || {
    cat "$ACTION_LOG" >&2
    exit 1
}
# Once a baseline exists, the menu itself is read-only.
output_topology() { python3 "$TOPOLOGY_SCRIPT" --output-topology; }
run_quiet_action() {
    local output status
    output="$("$@" 2>&1)"; status=$?
    if (( status != 0 )); then
        printf '%s\n' "$output" >&2
        printf '%s\n' "$output" >>"$ACTION_LOG"
        pause_before_close
        return "$status"
    fi
}
