#!/usr/bin/env bash
set -u

ACTION="${1:-toggle}"

case "$ACTION" in
    toggle|on|off)
        ;;
    *)
        echo "Usage: $0 [toggle|on|off]" >&2
        exit 2
        ;;
esac

PATH="/run/wrappers/bin:/home/joel/.nix-profile/bin:/etc/profiles/per-user/joel/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin"
export PATH

HOME_DIR="/home/joel"
AIRPLAY_CONFIG_DIR="$HOME_DIR/Documents/prefs/audio/airplay"
STACK_DIR="/run/user/1000/joel-airplay-stack"
STATE_DIR="$HOME_DIR/.local/state/audio-suspend-toggle"

AIRPLAY_CONNECTION="AirPlay Direct"
IPHONE_CONNECTION="iPhone4817"

SHAIRPORT_CONFIG="$AIRPLAY_CONFIG_DIR/shairport-sync.conf"
SHAIRPORT_PID_FILE="$STACK_DIR/shairport-sync.pid"
SHAIRPORT_LOG="$STACK_DIR/shairport-sync.log"
NQPTP_LOG="$STACK_DIR/nqptp.log"

HYPERX_PID_FILE="$STACK_DIR/camilladsp-hyperx.pid"
BUILTIN_PID_FILE="$STACK_DIR/camilladsp-builtin.pid"

HYPERX_STATE_FILE="$STACK_DIR/hyperx-profile"
BUILTIN_STATE_FILE="$STACK_DIR/builtin-profile"
ACTIVE_CONTROL_FILE="$STACK_DIR/active-control"

LOCK_DIR="$STATE_DIR/airplay-toggle-lock.d"
LOG_FILE="$STATE_DIR/airplay-toggle.log"

mkdir -p "$STACK_DIR" "$STATE_DIR"

exec >>"$LOG_FILE" 2>&1

printf '\n[%s] invoked pid=%s\n' \
    "$(date -Iseconds 2>/dev/null || date)" \
    "$$"

acquire_lock() {
    local pid=""

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" >"$LOCK_DIR/pid"
        return 0
    fi

    if [[ -r "$LOCK_DIR/pid" ]]; then
        read -r pid <"$LOCK_DIR/pid" || pid=""
    fi

    if [[ "$pid" =~ ^[0-9]+$ ]] &&
       kill -0 "$pid" 2>/dev/null; then
        echo "ignored: already running as PID $pid"
        return 1
    fi

    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || return 1
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
    rm -rf "$LOCK_DIR"
}

acquire_lock || exit 0
trap release_lock EXIT INT TERM HUP

COOLDOWN_SECONDS=2
COOLDOWN_FILE="$STATE_DIR/audio-controls-last-run"

now="$(date +%s)"
last=0

if [[ -r "$COOLDOWN_FILE" ]]; then
    read -r last <"$COOLDOWN_FILE" || last=0
fi

[[ "$last" =~ ^[0-9]+$ ]] || last=0

if (( now - last < COOLDOWN_SECONDS )); then
    echo "ignored: shared audio-controls cooldown"
    exit 0
fi

temporary_cooldown="${COOLDOWN_FILE}.tmp.$$"

printf '%s\n' "$now" >"$temporary_cooldown"

mv -f \
    "$temporary_cooldown" \
    "$COOLDOWN_FILE"

connection_active() {
    local connection="$1"

    nmcli \
        -t \
        -f NAME \
        connection show --active \
        2>/dev/null |
    grep -Fqx "$connection"
}

shairport_pids() {
    local proc
    local pid
    local exe

    for proc in /proc/[0-9]*; do
        [[ -d "$proc" ]] || continue

        pid="${proc##*/}"
        exe="$(readlink -f "$proc/exe" 2>/dev/null || true)"

        if [[ "${exe##*/}" == "shairport-sync" ]]; then
            printf '%s\n' "$pid"
        fi
    done
}

shairport_running() {
    [[ -n "$(shairport_pids)" ]]
}

nqptp_running() {
    pgrep -x nqptp >/dev/null 2>&1
}

stop_pid_file_process() {
    local pid_file="$1"
    local pid=""

    if [[ -r "$pid_file" ]]; then
        read -r pid <"$pid_file" || pid=""
    fi

    if [[ "$pid" =~ ^[0-9]+$ ]]; then
        kill -TERM "$pid" 2>/dev/null || true

        for _ in {1..30}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done

        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    fi

    rm -f "$pid_file"
}

stop_camilla() {
    stop_pid_file_process "$HYPERX_PID_FILE"
    stop_pid_file_process "$BUILTIN_PID_FILE"

    rm -f \
        "$HYPERX_STATE_FILE" \
        "$BUILTIN_STATE_FILE" \
        "$ACTIVE_CONTROL_FILE"
}

stop_shairport() {
    local pid

    while read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        kill -TERM "$pid" 2>/dev/null || true
    done < <(shairport_pids)

    for _ in {1..30}; do
        shairport_running || break
        sleep 0.1
    done

    while read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        kill -KILL "$pid" 2>/dev/null || true
    done < <(shairport_pids)

    rm -f "$SHAIRPORT_PID_FILE"
}

stop_nqptp() {
    sudo -n pkill -TERM -x nqptp 2>/dev/null ||
        pkill -TERM -x nqptp 2>/dev/null ||
        true

    for _ in {1..30}; do
        nqptp_running || break
        sleep 0.1
    done

    if nqptp_running; then
        sudo -n pkill -KILL -x nqptp 2>/dev/null ||
            pkill -KILL -x nqptp 2>/dev/null ||
            true
    fi
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
}

start_nqptp() {
    local binary

    nqptp_running && return 0

    binary="$(command -v nqptp)" || {
        echo "ERROR: nqptp was not found"
        return 1
    }

    : >"$NQPTP_LOG"

    sudo -n sh -c \
        "nohup '$binary' >>'$NQPTP_LOG' 2>&1 &" ||
        return 1

    for _ in {1..50}; do
        nqptp_running && return 0
        sleep 0.1
    done

    echo "ERROR: NQPTP failed to start"
    return 1
}

start_shairport() {
    local pid

    shairport_running && return 0

    [[ -f "$SHAIRPORT_CONFIG" ]] || {
        echo "ERROR: missing Shairport config: $SHAIRPORT_CONFIG"
        return 1
    }

    : >"$SHAIRPORT_LOG"

    nohup shairport-sync \
        -c "$SHAIRPORT_CONFIG" \
        -vv \
        >>"$SHAIRPORT_LOG" \
        2>&1 &

    pid=$!

    printf '%s\n' "$pid" >"$SHAIRPORT_PID_FILE"

    for _ in {1..30}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "ERROR: Shairport Sync exited"
            tail -n 80 "$SHAIRPORT_LOG" 2>/dev/null || true
            rm -f "$SHAIRPORT_PID_FILE"
            return 1
        fi

        sleep 0.1
    done

    return 0
}

airplay_active() {
    connection_active "$AIRPLAY_CONNECTION" &&
        shairport_running &&
        nqptp_running
}

if [[ "$ACTION" == "off" ]] ||
   { [[ "$ACTION" == "toggle" ]] && airplay_active; }; then

    stop_camilla
    stop_shairport
    stop_nqptp

    nmcli \
        --wait 15 \
        connection down \
        "$AIRPLAY_CONNECTION" \
        >/dev/null 2>&1 ||
        true

    restore_pipewire

    echo "connecting network=$IPHONE_CONNECTION"

    if ! nmcli \
        --wait 30 \
        connection up \
        "$IPHONE_CONNECTION"; then

        echo "ERROR: unable to connect to $IPHONE_CONNECTION"
        exit 1
    fi

    echo "result=airplay-off"
    echo "network=$IPHONE_CONNECTION"
    exit 0
fi

if [[ "$ACTION" == "on" ]] && airplay_active; then
    echo "result=already-on"
    echo "network=$AIRPLAY_CONNECTION"
    exit 0
fi

echo "action=enable-airplay"

nmcli \
    --wait 15 \
    connection down \
    "$IPHONE_CONNECTION" \
    >/dev/null 2>&1 ||
    true

if ! connection_active "$AIRPLAY_CONNECTION"; then
    if ! nmcli \
        --wait 30 \
        connection up \
        "$AIRPLAY_CONNECTION"; then

        echo "ERROR: unable to start $AIRPLAY_CONNECTION"

        nmcli \
            --wait 30 \
            connection up \
            "$IPHONE_CONNECTION" \
            >/dev/null 2>&1 ||
            true

        exit 1
    fi
fi

if ! start_nqptp; then
    stop_nqptp
    nmcli connection down "$AIRPLAY_CONNECTION" >/dev/null 2>&1 || true
    exit 1
fi

if ! start_shairport; then
    stop_shairport
    stop_nqptp
    nmcli connection down "$AIRPLAY_CONNECTION" >/dev/null 2>&1 || true
    exit 1
fi

echo "result=airplay-on"
echo "network=$AIRPLAY_CONNECTION"
echo "nqptp=running"
echo "shairport=running"
