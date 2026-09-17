#!/usr/bin/env bash
set -u

PATH="/run/wrappers/bin:/home/joel/.nix-profile/bin:/etc/profiles/per-user/joel/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin"
export PATH

HOME_DIR="/home/joel"
SCRIPT_DIR="$HOME_DIR/.config/sway/scripts/audio"
STATE_DIR="$HOME_DIR/.local/state/audio-suspend-toggle"
STACK_DIR="/run/user/1000/joel-airplay-stack"

AIRPLAY_TOGGLE="$SCRIPT_DIR/airplay-toggle.sh"

HYPERX_PID_FILE="$STACK_DIR/camilladsp-hyperx.pid"
BUILTIN_PID_FILE="$STACK_DIR/camilladsp-builtin.pid"

HYPERX_STATE_FILE="$STACK_DIR/hyperx-profile"
BUILTIN_STATE_FILE="$STACK_DIR/builtin-profile"
ACTIVE_CONTROL_FILE="$STACK_DIR/active-control"

DISPLAY_OFF_STATE_FILE="$STATE_DIR/displays-powered-off"

LOCK_DIR="$STATE_DIR/suspend-display-lock.d"
LOG_FILE="$STATE_DIR/suspend-display-toggle.log"

COOLDOWN_MILLISECONDS=2000
COOLDOWN_FILE="$STATE_DIR/suspend-display-last-run-ms"

mkdir -p \
    "$STATE_DIR" \
    "$STACK_DIR"

exec >>"$LOG_FILE" 2>&1

printf '\n[%s] invoked pid=%s\n' \
    "$(date -Iseconds 2>/dev/null || date)" \
    "$$"

###############################################################################
# SINGLE-INSTANCE LOCK
###############################################################################

acquire_lock() {
    local old_pid=""

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" >"$LOCK_DIR/pid"
        return 0
    fi

    if [[ -r "$LOCK_DIR/pid" ]]; then
        read -r old_pid <"$LOCK_DIR/pid" || old_pid=""
    fi

    if [[ "$old_pid" =~ ^[0-9]+$ ]] &&
       kill -0 "$old_pid" 2>/dev/null; then

        echo "ignored: already running as PID $old_pid"
        return 1
    fi

    echo "removing stale lock"

    rm -rf "$LOCK_DIR"

    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        echo "ERROR: unable to acquire lock"
        return 1
    fi

    printf '%s\n' "$$" >"$LOCK_DIR/pid"

    return 0
}

release_lock() {
    rm -rf "$LOCK_DIR"
}

acquire_lock || exit 0

trap release_lock EXIT INT TERM HUP

###############################################################################
# PRECISE ANTI-SPAM COOLDOWN
###############################################################################

now_ms="$(date +%s%3N)"
last_ms=0

if [[ -r "$COOLDOWN_FILE" ]]; then
    read -r last_ms <"$COOLDOWN_FILE" || last_ms=0
fi

[[ "$now_ms" =~ ^[0-9]+$ ]] || {
    echo "ERROR: unable to obtain current time in milliseconds"
    exit 1
}

[[ "$last_ms" =~ ^[0-9]+$ ]] || last_ms=0

if (( now_ms - last_ms < COOLDOWN_MILLISECONDS )); then
    echo "ignored: suspend-display cooldown"
    exit 0
fi

temporary_cooldown="${COOLDOWN_FILE}.tmp.$$"

printf '%s\n' "$now_ms" >"$temporary_cooldown"

mv -f \
    "$temporary_cooldown" \
    "$COOLDOWN_FILE"

###############################################################################
# DISPLAY STATE
###############################################################################

displays_marked_off() {
    [[ -f "$DISPLAY_OFF_STATE_FILE" ]]
}

mark_displays_off() {
    local temporary="${DISPLAY_OFF_STATE_FILE}.tmp.$$"

    printf '%s\n' \
        "$(date -Iseconds 2>/dev/null || date)" \
        >"$temporary"

    mv -f \
        "$temporary" \
        "$DISPLAY_OFF_STATE_FILE"
}

mark_displays_on() {
    rm -f "$DISPLAY_OFF_STATE_FILE"
}

power_displays_off() {
    if ! swaymsg \
        "output * power off" \
        >/dev/null; then

        echo "ERROR: Sway rejected display power-off"
        return 1
    fi

    mark_displays_off

    return 0
}

power_displays_on() {
    if ! swaymsg \
        "output * power on" \
        >/dev/null; then

        echo "ERROR: Sway rejected display power-on"
        return 1
    fi

    mark_displays_on

    return 0
}

###############################################################################
# CAMILLADSP SHUTDOWN
###############################################################################

stop_pid_file_process() {
    local pid_file="$1"
    local pid=""

    if [[ -r "$pid_file" ]]; then
        read -r pid <"$pid_file" || pid=""
    fi

    if [[ "$pid" =~ ^[0-9]+$ ]]; then
        echo "stopping PID $pid from $pid_file"

        kill -TERM "$pid" 2>/dev/null || true

        for _ in {1..30}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done

        if kill -0 "$pid" 2>/dev/null; then
            echo "forcing PID $pid to stop"
            kill -KILL "$pid" 2>/dev/null || true
        fi
    fi

    rm -f "$pid_file"
}

stop_all_camilla() {
    stop_pid_file_process "$HYPERX_PID_FILE"
    stop_pid_file_process "$BUILTIN_PID_FILE"

    pkill -TERM -x camilladsp 2>/dev/null || true

    for _ in {1..30}; do
        pgrep -x camilladsp >/dev/null 2>&1 || break
        sleep 0.1
    done

    if pgrep -x camilladsp >/dev/null 2>&1; then
        echo "forcing remaining CamillaDSP processes to stop"
        pkill -KILL -x camilladsp 2>/dev/null || true
    fi

    rm -f \
        "$HYPERX_PID_FILE" \
        "$BUILTIN_PID_FILE" \
        "$HYPERX_STATE_FILE" \
        "$BUILTIN_STATE_FILE" \
        "$ACTIVE_CONTROL_FILE"
}

restore_pipewire() {
    systemctl --user start \
        pipewire.socket \
        pipewire-pulse.socket \
        pipewire.service \
        pipewire-pulse.service \
        wireplumber.service \
        >/dev/null 2>&1 ||
        true

    for _ in {1..100}; do
        if pactl info >/dev/null 2>&1; then
            echo "PipeWire restored"
            return 0
        fi

        sleep 0.1
    done

    echo "WARNING: PipeWire did not become ready"
    return 1
}

###############################################################################
# FIRST PRESS: DISPLAY ON -> DISPLAY OFF ONLY
###############################################################################

if ! displays_marked_off; then
    echo "state=display-on"
    echo "action=display-off-only"

    if ! power_displays_off; then
        exit 1
    fi

    echo "result=display-off"
    exit 0
fi

###############################################################################
# SECOND PRESS: DISPLAY OFF -> DISPLAY ON, AUDIO OFF, SUSPEND
###############################################################################

echo "state=display-off"
echo "action=display-on-audio-off-suspend"

echo "turning displays on"

if ! power_displays_on; then
    exit 1
fi

###############################################################################
# FORCE AIRPLAY OFF
###############################################################################

echo "forcing AirPlay off"

if [[ -x "$AIRPLAY_TOGGLE" ]]; then
    if ! "$AIRPLAY_TOGGLE" off; then
        echo "WARNING: airplay-toggle.sh off returned an error"
        echo "continuing with direct audio cleanup"
    fi
else
    echo "WARNING: AirPlay toggle is missing or not executable:"
    echo "$AIRPLAY_TOGGLE"
fi

###############################################################################
# FORCE CAMILLADSP OFF
###############################################################################

echo "ensuring CamillaDSP is stopped"

stop_all_camilla

if pgrep -x camilladsp >/dev/null 2>&1; then
    echo "ERROR: CamillaDSP is still running"
    exit 1
fi

###############################################################################
# RESTORE PIPEWIRE
###############################################################################

echo "restoring PipeWire"

restore_pipewire || true

###############################################################################
# PRE-SUSPEND VERIFICATION
###############################################################################

echo "pre-suspend display=on"
echo "pre-suspend camilladsp=stopped"

if pgrep -x shairport-sync >/dev/null 2>&1; then
    echo "WARNING: Shairport Sync is still running"
else
    echo "pre-suspend shairport=stopped"
fi

if pgrep -x nqptp >/dev/null 2>&1; then
    echo "WARNING: NQPTP is still running"
else
    echo "pre-suspend nqptp=stopped"
fi

sync

###############################################################################
# SUSPEND
###############################################################################

echo "suspending"

release_lock
trap - EXIT INT TERM HUP

systemctl suspend
status=$?

# The display-off marker was already removed before suspending. Therefore,
# after resume, the next press begins a new display-off/display-suspend cycle.
exit "$status"
