#!/usr/bin/env bash
set -u

PATH="/run/wrappers/bin:/home/joel/.nix-profile/bin:/etc/profiles/per-user/joel/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin"
export PATH

PROFILE="${1:-}"

case "$PROFILE" in
    cloud3)
        AIRPLAY_NAME="Joel Laptop - Cloud III"
        ;;
    earpods)
        AIRPLAY_NAME="Joel Laptop - EarPods"
        ;;
    *)
        echo "Usage: $0 cloud3|earpods" >&2
        exit 2
        ;;
esac

HOME_DIR="/home/joel"
AIRPLAY_DIR="$HOME_DIR/Documents/prefs/audio/airplay"
STACK_DIR="/run/user/1000/joel-airplay-stack"
CONFIG_DIR="$STACK_DIR/configs"
STATE_DIR="$HOME_DIR/.local/state/audio-suspend-toggle"

TEMPLATE="$AIRPLAY_DIR/camilladsp-hyperx-${PROFILE}.yml"
CONFIG="$CONFIG_DIR/camilladsp-hyperx-${PROFILE}.yml"
SHAIRPORT_CONFIG="$AIRPLAY_DIR/shairport-sync.conf"

HYPERX_PID_FILE="$STACK_DIR/camilladsp-hyperx.pid"
BUILTIN_PID_FILE="$STACK_DIR/camilladsp-builtin.pid"
SHAIRPORT_PID_FILE="$STACK_DIR/shairport-sync.pid"

HYPERX_STATE_FILE="$STACK_DIR/hyperx-profile"
BUILTIN_STATE_FILE="$STACK_DIR/builtin-profile"

ACTIVE_CONTROL_FILE="$STACK_DIR/active-control"
LAST_CONTROL_FILE="$STATE_DIR/last-airplay-control"

LOG_FILE="$STACK_DIR/camilladsp-hyperx.log"
SHAIRPORT_LOG="$STACK_DIR/shairport-sync.log"
NQPTP_LOG="$STACK_DIR/nqptp.log"
ACTION_LOG="$STATE_DIR/audio-toggle.log"
LOCK_DIR="$STATE_DIR/hyperx-profile-toggle-lock.d"

mkdir -p "$CONFIG_DIR" "$STATE_DIR"

exec >>"$ACTION_LOG" 2>&1

printf '\n[%s] profile=%s pid=%s\n' \
    "$(date -Iseconds 2>/dev/null || date)" \
    "$PROFILE" \
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

pid_file_running() {
    local pid_file="$1"
    local pid
    local exe

    [[ -s "$pid_file" ]] || return 1
    read -r pid <"$pid_file" || return 1
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1

    exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
    [[ "${exe##*/}" == "camilladsp" ]]
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

stop_all_camilla() {
    stop_pid_file_process "$HYPERX_PID_FILE"
    stop_pid_file_process "$BUILTIN_PID_FILE"

    rm -f \
        "$HYPERX_STATE_FILE" \
        "$BUILTIN_STATE_FILE" \
        "$ACTIVE_CONTROL_FILE"
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
        -a "$AIRPLAY_NAME" \
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

stop_pipewire() {
    systemctl --user stop \
        wireplumber.service \
        pipewire-pulse.service \
        pipewire-pulse.socket \
        pipewire.service \
        pipewire.socket \
        >/dev/null 2>&1 ||
        true

    for _ in {1..50}; do
        if ! pgrep -x pipewire >/dev/null 2>&1 &&
           ! pgrep -x wireplumber >/dev/null 2>&1; then
            return 0
        fi

        sleep 0.1
    done

    return 1
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

find_hyperx_device() {
    local info_file
    local pcm_dir
    local card_dir
    local card_number
    local device_number
    local metadata
    local lower
    local card_path
    local parent
    local is_usb

    for info_file in /proc/asound/card[0-9]*/pcm[0-9]*p/info; do
        [[ -r "$info_file" ]] || continue

        pcm_dir="${info_file%/info}"
        card_dir="${pcm_dir%/*}"

        card_number="${card_dir##*/card}"
        device_number="${pcm_dir##*/pcm}"
        device_number="${device_number%p}"

        metadata="$(
            {
                cat "/proc/asound/card${card_number}/id" 2>/dev/null || true
                cat "/proc/asound/card${card_number}/longname" 2>/dev/null || true
                cat "$info_file" 2>/dev/null || true
                udevadm info \
                    --query=property \
                    --path="/sys/class/sound/card${card_number}" \
                    2>/dev/null || true
            } |
            tr '\n' ' '
        )"

        lower="${metadata,,}"

        [[ "$lower" == *loopback* ]] && continue

        card_path="$(
            readlink -f \
                "/sys/class/sound/card${card_number}/device" \
                2>/dev/null || true
        )"

        is_usb=0
        parent="$card_path"

        while [[ -n "$parent" && "$parent" != "/" ]]; do
            if [[ -r "$parent/idVendor" &&
                  -r "$parent/idProduct" ]]; then
                is_usb=1
                break
            fi

            parent="${parent%/*}"
            [[ -n "$parent" ]] || parent="/"
        done

        if [[ "$lower" == *hyperx* ||
              "$lower" == *"cloud iii"* ||
              "$lower" == *cloud_iii* ]]; then

            printf 'hw:%s,%s\n' \
                "$card_number" \
                "$device_number"

            return 0
        fi

        if (( is_usb )) &&
           [[ "$lower" != *hdmi* &&
              "$lower" != *displayport* &&
              "$lower" != *sof* ]]; then

            printf 'hw:%s,%s\n' \
                "$card_number" \
                "$device_number"

            return 0
        fi
    done

    return 1
}

CURRENT_PROFILE=""

if pid_file_running "$HYPERX_PID_FILE" &&
   [[ -r "$HYPERX_STATE_FILE" ]]; then
    read -r CURRENT_PROFILE <"$HYPERX_STATE_FILE" || CURRENT_PROFILE=""
fi

if [[ "$CURRENT_PROFILE" == "$PROFILE" ]]; then
    echo "action=stop-hyperx-$PROFILE"

    stop_all_camilla
    stop_shairport
    stop_nqptp
    restore_pipewire

    echo "result=stopped"
    exit 0
fi

# Reaching here means the requested profile is being turned on,
# either from stopped or by switching from the other profile.
swaymsg \
    "workspace number 10" \
    >/dev/null 2>&1 ||
    true

PLAYBACK_DEVICE="$(find_hyperx_device)" || {
    echo "ERROR: HyperX playback device was not detected"
    exit 1
}

[[ -f "$TEMPLATE" ]] || {
    echo "ERROR: template missing: $TEMPLATE"
    exit 1
}

TEMPORARY="${CONFIG}.tmp.$$"

rm -f "$CONFIG" "$TEMPORARY"

sed \
    -e "s|__HOME__|$HOME_DIR|g" \
    -e "s|__USB_PLAYBACK_DEVICE__|$PLAYBACK_DEVICE|g" \
    -e "s|__BUILTIN_PLAYBACK_DEVICE__|$PLAYBACK_DEVICE|g" \
    "$TEMPLATE" \
    >"$TEMPORARY" || {
        rm -f "$TEMPORARY"
        exit 1
    }

if ! camilladsp -c "$TEMPORARY"; then
    echo "ERROR: invalid CamillaDSP config"
    echo "template=$TEMPLATE"
    echo "playback=$PLAYBACK_DEVICE"
    rm -f "$TEMPORARY"
    exit 1
fi

mv -f "$TEMPORARY" "$CONFIG"

stop_all_camilla

stop_pipewire || {
    echo "WARNING: PipeWire did not completely stop"
}

if ! start_nqptp; then
    stop_nqptp
    restore_pipewire
    exit 1
fi

# The receiver name depends on the selected profile. Restart only Shairport
# when changing profiles so the new AirPlay name is advertised. NQPTP stays up.
stop_shairport

if ! start_shairport; then
    stop_shairport
    stop_nqptp
    restore_pipewire
    exit 1
fi

card_number="$(
    sed -nE \
        's/^hw:([0-9]+),[0-9]+$/\1/p' \
        <<<"$PLAYBACK_DEVICE"
)"

if [[ "$card_number" =~ ^[0-9]+$ ]]; then
    amixer \
        -c "$card_number" \
        sset 'Speaker Volume' \
        100% \
        unmute \
        >/dev/null 2>&1 ||
        true
fi

: >"$LOG_FILE"

nohup camilladsp \
    --loglevel=debug \
    --logfile="$LOG_FILE" \
    "$CONFIG" \
    >>"$LOG_FILE" \
    2>&1 &

pid=$!

printf '%s\n' "$pid" >"$HYPERX_PID_FILE"

for _ in {1..40}; do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "ERROR: CamillaDSP exited during startup"
        tail -n 100 "$LOG_FILE" 2>/dev/null || true

        rm -f \
            "$HYPERX_PID_FILE" \
            "$HYPERX_STATE_FILE" \
            "$ACTIVE_CONTROL_FILE"

        stop_shairport
        stop_nqptp
        restore_pipewire
        exit 1
    fi

    sleep 0.1
done

printf '%s\n' "$PROFILE" >"$HYPERX_STATE_FILE"

temporary_control="${ACTIVE_CONTROL_FILE}.tmp.$$"

printf 'hyperx %s\n' \
    "$PROFILE" \
    >"$temporary_control"

mv -f \
    "$temporary_control" \
    "$ACTIVE_CONTROL_FILE"

umask 077

printf 'hyperx %s\n' \
    "$PROFILE" \
    >"$LAST_CONTROL_FILE"

echo "result=active"
echo "profile=hyperx/$PROFILE"
echo "playback=$PLAYBACK_DEVICE"
echo "nqptp=running"
echo "shairport=running"
echo "airplay-name=$AIRPLAY_NAME"
