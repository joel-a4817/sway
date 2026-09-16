#!/usr/bin/env bash
set -u

PATH="/run/wrappers/bin:/home/joel/.nix-profile/bin:/etc/profiles/per-user/joel/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin"
export PATH

HOME_DIR="/home/joel"
AIRPLAY_CONFIG_DIR="$HOME_DIR/Documents/prefs/audio/airplay"
AUDIO_STACK_DIR="/run/user/1000/joel-airplay-stack"
CONFIG_DIR="$AUDIO_STACK_DIR/configs"
STATE_DIR="$HOME_DIR/.local/state/audio-suspend-toggle"

LAST_CONTROL_FILE="$STATE_DIR/last-airplay-control"
ACTIVE_CONTROL_FILE="$AUDIO_STACK_DIR/active-control"
LAST_RUN_FILE="$STATE_DIR/last-run-epoch"
LOCK_DIR="$STATE_DIR/lock.d"
LOG_FILE="$STATE_DIR/audio-suspend-toggle.log"

HYPERX_PID_FILE="$AUDIO_STACK_DIR/camilladsp-hyperx.pid"
BUILTIN_PID_FILE="$AUDIO_STACK_DIR/camilladsp-builtin.pid"

HYPERX_STATE_FILE="$AUDIO_STACK_DIR/hyperx-profile"
BUILTIN_STATE_FILE="$AUDIO_STACK_DIR/builtin-profile"

HYPERX_LOG="$AUDIO_STACK_DIR/camilladsp-hyperx.log"
BUILTIN_LOG="$AUDIO_STACK_DIR/camilladsp-builtin.log"

SHAIRPORT_PID_FILE="$AUDIO_STACK_DIR/shairport-sync.pid"
SHAIRPORT_LOG="$AUDIO_STACK_DIR/shairport-sync.log"
NQPTP_LOG="$AUDIO_STACK_DIR/nqptp.log"

SHAIRPORT_CONFIG="$AIRPLAY_CONFIG_DIR/shairport-sync.conf"

HOTSPOT_CONNECTION="AirPlay Direct"
COOLDOWN_SECONDS=2

mkdir -p \
    "$CONFIG_DIR" \
    "$STATE_DIR"

chmod 700 "$STATE_DIR" 2>/dev/null || true

exec >>"$LOG_FILE" 2>&1

printf '\n[%s] invoked pid=%s\n' \
    "$(date -Iseconds 2>/dev/null || date)" \
    "$$"

###############################################################################
# SINGLE-INSTANCE LOCK
###############################################################################

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
# COOLDOWN
###############################################################################

now="$(date +%s)"
last=0

if [[ -r "$LAST_RUN_FILE" ]]; then
    read -r last <"$LAST_RUN_FILE" || last=0
fi

[[ "$last" =~ ^[0-9]+$ ]] || last=0

if (( now - last < COOLDOWN_SECONDS )); then
    echo "ignored: cooldown"
    exit 0
fi

printf '%s\n' "$now" >"$LAST_RUN_FILE"

###############################################################################
# LIVE-STATE HELPERS
###############################################################################

hotspot_active() {
    nmcli \
        -t \
        -f NAME \
        connection show --active \
        2>/dev/null |
    grep -Fqx "$HOTSPOT_CONNECTION"
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

current_control() {
    local slot=""
    local profile=""
    local temporary=""

    if [[ -r "$ACTIVE_CONTROL_FILE" ]]; then
        read -r slot profile <"$ACTIVE_CONTROL_FILE" || {
            slot=""
            profile=""
        }

        case "$slot:$profile" in
            hyperx:cloud3|hyperx:earpods)
                if pid_file_running "$HYPERX_PID_FILE"; then
                    printf '%s %s\n' "$slot" "$profile"
                    return 0
                fi
                ;;

            builtin:cloud3|builtin:earpods)
                if pid_file_running "$BUILTIN_PID_FILE"; then
                    printf '%s %s\n' "$slot" "$profile"
                    return 0
                fi
                ;;
        esac

        rm -f "$ACTIVE_CONTROL_FILE"
    fi

    if pid_file_running "$HYPERX_PID_FILE"; then
        profile="$(
            cat "$HYPERX_STATE_FILE" \
                2>/dev/null || true
        )"

        case "$profile" in
            cloud3|earpods)
                temporary="${ACTIVE_CONTROL_FILE}.tmp.$$"

                printf 'hyperx %s\n' \
                    "$profile" \
                    >"$temporary"

                mv -f \
                    "$temporary" \
                    "$ACTIVE_CONTROL_FILE"

                printf 'hyperx %s\n' "$profile"
                return 0
                ;;
        esac
    fi

    if pid_file_running "$BUILTIN_PID_FILE"; then
        profile="$(
            cat "$BUILTIN_STATE_FILE" \
                2>/dev/null || true
        )"

        case "$profile" in
            cloud3|earpods)
                temporary="${ACTIVE_CONTROL_FILE}.tmp.$$"

                printf 'builtin %s\n' \
                    "$profile" \
                    >"$temporary"

                mv -f \
                    "$temporary" \
                    "$ACTIVE_CONTROL_FILE"

                printf 'builtin %s\n' "$profile"
                return 0
                ;;
        esac
    fi

    return 1
}

save_control() {
    local slot="$1"
    local profile="$2"

    [[ "$slot" == "hyperx" ||
       "$slot" == "builtin" ]] ||
        return 1

    [[ "$profile" == "cloud3" ||
       "$profile" == "earpods" ]] ||
        return 1

    umask 077

    printf '%s %s\n' \
        "$slot" \
        "$profile" \
        >"$LAST_CONTROL_FILE"
}

load_control() {
    local slot="hyperx"
    local profile="cloud3"

    if [[ -r "$LAST_CONTROL_FILE" ]]; then
        read -r slot profile <"$LAST_CONTROL_FILE" || {
            slot="hyperx"
            profile="cloud3"
        }
    fi

    [[ "$slot" == "hyperx" ||
       "$slot" == "builtin" ]] ||
        slot="hyperx"

    [[ "$profile" == "cloud3" ||
       "$profile" == "earpods" ]] ||
        profile="cloud3"

    printf '%s %s\n' "$slot" "$profile"
}

###############################################################################
# STOP HELPERS
###############################################################################

stop_camilla() {
    local pid_file="$1"
    local state_file="$2"
    local pid=""
    local active_slot=""
    local active_profile=""

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

    rm -f \
        "$pid_file" \
        "$state_file"

    if [[ -r "$ACTIVE_CONTROL_FILE" ]]; then
        read -r active_slot active_profile \
            <"$ACTIVE_CONTROL_FILE" ||
            true

        if [[ "$pid_file" == "$HYPERX_PID_FILE" &&
              "$active_slot" == "hyperx" ]]; then

            rm -f "$ACTIVE_CONTROL_FILE"

        elif [[ "$pid_file" == "$BUILTIN_PID_FILE" &&
                "$active_slot" == "builtin" ]]; then

            rm -f "$ACTIVE_CONTROL_FILE"
        fi
    fi
}

stop_shared_airplay() {
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

    rm -f "$SHAIRPORT_PID_FILE"
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

###############################################################################
# DEVICE DETECTION
###############################################################################

find_playback_device() {
    local wanted="$1"
    local line
    local card
    local device

    while IFS= read -r line; do
        case "$wanted" in
            hyperx)
                if [[ "$line" != *HyperX* &&
                      "$line" != *Cloud* ]]; then
                    continue
                fi
                ;;

            builtin)
                if [[ "$line" != *sof* &&
                      "$line" != *SOF* &&
                      "$line" != *PCH* ]]; then
                    continue
                fi
                ;;

            *)
                return 1
                ;;
        esac

        card="$(
            sed -nE \
                's/^card ([0-9]+):.*/\1/p' \
                <<<"$line"
        )"

        device="$(
            sed -nE \
                's/.*device ([0-9]+):.*/\1/p' \
                <<<"$line"
        )"

        [[ "$card" =~ ^[0-9]+$ ]] || continue
        [[ "$device" =~ ^[0-9]+$ ]] || continue

        if [[ "$wanted" == "hyperx" ]]; then
            printf 'hw:%s,%s\n' \
                "$card" \
                "$device"
        else
            printf 'plughw:%s,%s\n' \
                "$card" \
                "$device"
        fi

        return 0
    done < <(aplay -l 2>/dev/null)

    return 1
}

###############################################################################
# CONFIG GENERATION
###############################################################################

prepare_config() {
    local slot="$1"
    local profile="$2"
    local playback
    local template
    local config
    local temporary

    playback="$(find_playback_device "$slot")" || {
        echo "ERROR: no playback device found for $slot" >&2
        return 1
    }

    template="$AIRPLAY_CONFIG_DIR/camilladsp-${slot}-${profile}.yml"
    config="$CONFIG_DIR/camilladsp-${slot}-${profile}.yml"
    temporary="${config}.tmp.$$"

    if [[ ! -f "$template" ]]; then
        echo "ERROR: template missing: $template" >&2
        return 1
    fi

    if ! sed \
        -e "s|__HOME__|$HOME_DIR|g" \
        -e "s|__USB_PLAYBACK_DEVICE__|$playback|g" \
        -e "s|__BUILTIN_PLAYBACK_DEVICE__|$playback|g" \
        "$template" \
        >"$temporary"; then

        echo "ERROR: failed to generate config" >&2
        rm -f "$temporary"
        return 1
    fi

    if grep -qE \
        '__HOME__|__USB_PLAYBACK_DEVICE__|__BUILTIN_PLAYBACK_DEVICE__' \
        "$temporary"; then

        echo "ERROR: unresolved placeholder in $temporary" >&2
        rm -f "$temporary"
        return 1
    fi

    mv -f "$temporary" "$config"

    echo "generated config=$config playback=$playback" >&2

    printf '%s\n' "$config"
}

###############################################################################
# START CAMILLADSP
###############################################################################

start_camilla() {
    local slot="$1"
    local profile="$2"
    local config
    local pid_file
    local state_file
    local camilla_log
    local pid
    local temporary_active_control

    config="$(prepare_config "$slot" "$profile")" ||
        return 1

    if [[ "$slot" == "hyperx" ]]; then
        pid_file="$HYPERX_PID_FILE"
        state_file="$HYPERX_STATE_FILE"
        camilla_log="$HYPERX_LOG"
    else
        pid_file="$BUILTIN_PID_FILE"
        state_file="$BUILTIN_STATE_FILE"
        camilla_log="$BUILTIN_LOG"
    fi

    rm -f \
        "$pid_file" \
        "$state_file"

    : >"$camilla_log"

    echo "starting CamillaDSP slot=$slot profile=$profile config=$config"

    nohup camilladsp \
        --loglevel=debug \
        --logfile="$camilla_log" \
        "$config" \
        >>"$camilla_log" \
        2>&1 &

    pid=$!

    printf '%s\n' "$pid" >"$pid_file"

    for _ in {1..40}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "ERROR: CamillaDSP exited during startup"

            tail -n 80 "$camilla_log" 2>/dev/null || true

            rm -f \
                "$pid_file" \
                "$state_file"

            return 1
        fi

        sleep 0.1
    done

    printf '%s\n' "$profile" >"$state_file"

    temporary_active_control="${ACTIVE_CONTROL_FILE}.tmp.$$"

    printf '%s %s\n' \
        "$slot" \
        "$profile" \
        >"$temporary_active_control"

    mv -f \
        "$temporary_active_control" \
        "$ACTIVE_CONTROL_FILE"

    save_control "$slot" "$profile"

    echo "CamillaDSP active: $slot/$profile PID $pid"

    return 0
}

###############################################################################
# START SHARED AIRPLAY
###############################################################################

start_shared_airplay() {
    local pid
    local nqptp_binary

    if ! nqptp_running; then
        nqptp_binary="$(command -v nqptp)" || {
            echo "ERROR: nqptp command was not found"
            return 1
        }

        : >"$NQPTP_LOG"

        if ! sudo -n sh -c \
            "nohup '$nqptp_binary' >>'$NQPTP_LOG' 2>&1 &"; then

            echo "ERROR: NQPTP requires passwordless sudo"
            return 1
        fi

        for _ in {1..50}; do
            nqptp_running && break
            sleep 0.1
        done

        nqptp_running || {
            echo "ERROR: NQPTP failed to remain running"
            tail -n 80 "$NQPTP_LOG" 2>/dev/null || true
            return 1
        }
    fi

    if ! shairport_running; then
        if [[ ! -f "$SHAIRPORT_CONFIG" ]]; then
            echo "ERROR: Shairport config is missing: $SHAIRPORT_CONFIG"
            return 1
        fi

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
                echo "ERROR: Shairport Sync exited during startup"
                tail -n 80 "$SHAIRPORT_LOG" 2>/dev/null || true
                rm -f "$SHAIRPORT_PID_FILE"
                return 1
            fi

            sleep 0.1
        done
    fi

    return 0
}

###############################################################################
# START EVERYTHING
###############################################################################

start_all() {
    local slot
    local profile

    read -r slot profile < <(load_control)

    echo "requested control=$slot/$profile"

    if ! find_playback_device "$slot" >/dev/null; then
        echo "saved output $slot is unavailable"

        if [[ "$slot" == "hyperx" ]] &&
           find_playback_device builtin >/dev/null; then

            slot="builtin"
            echo "falling back to builtin/$profile"
        else
            echo "ERROR: requested playback device is unavailable"
            return 1
        fi
    fi

    stop_camilla \
        "$HYPERX_PID_FILE" \
        "$HYPERX_STATE_FILE"

    stop_camilla \
        "$BUILTIN_PID_FILE" \
        "$BUILTIN_STATE_FILE"

    stop_shared_airplay

    echo "stopping PipeWire"

    stop_pipewire || {
        echo "WARNING: PipeWire did not fully stop"
    }

    if ! start_camilla "$slot" "$profile"; then
        echo "ERROR: CamillaDSP startup failed"
        restore_pipewire
        return 1
    fi

    if ! start_shared_airplay; then
        echo "ERROR: shared AirPlay startup failed"

        stop_camilla \
            "$HYPERX_PID_FILE" \
            "$HYPERX_STATE_FILE"

        stop_camilla \
            "$BUILTIN_PID_FILE" \
            "$BUILTIN_STATE_FILE"

        stop_shared_airplay
        restore_pipewire

        return 1
    fi

    if ! hotspot_active; then
        echo "starting hotspot"

        if ! nmcli \
            --wait 15 \
            connection up \
            "$HOTSPOT_CONNECTION"; then

            echo "ERROR: hotspot startup failed"

            stop_shared_airplay

            stop_camilla \
                "$HYPERX_PID_FILE" \
                "$HYPERX_STATE_FILE"

            stop_camilla \
                "$BUILTIN_PID_FILE" \
                "$BUILTIN_STATE_FILE"

            restore_pipewire

            return 1
        fi
    fi

    echo "toggle result=active"
    echo "control=$(current_control)"
    echo "hotspot=active"

    return 0
}

###############################################################################
# STOP EVERYTHING
###############################################################################

stop_all() {
    stop_camilla \
        "$HYPERX_PID_FILE" \
        "$HYPERX_STATE_FILE"

    stop_camilla \
        "$BUILTIN_PID_FILE" \
        "$BUILTIN_STATE_FILE"

    stop_shared_airplay

    nmcli \
        --wait 15 \
        connection down \
        "$HOTSPOT_CONNECTION" \
        >/dev/null 2>&1 ||
        true

    restore_pipewire
}

###############################################################################
# TOGGLE
###############################################################################

CONTROL_ACTIVE=0
HOTSPOT_ACTIVE=0
CONTROL_VALUE=""

if CONTROL_VALUE="$(current_control 2>/dev/null)"; then
    CONTROL_ACTIVE=1
fi

if hotspot_active; then
    HOTSPOT_ACTIVE=1
fi

echo "control_active=$CONTROL_ACTIVE"
echo "hotspot_active=$HOTSPOT_ACTIVE"
echo "control=${CONTROL_VALUE:-none}"

###############################################################################
# BOTH STOPPED: START AIRPLAY STACK AND TURN DISPLAY OFF
###############################################################################

if (( CONTROL_ACTIVE == 0 && HOTSPOT_ACTIVE == 0 )); then
    echo "action=start-and-display-off"

    if ! start_all; then
        echo "toggle result=start-failed"

        # Ensure a failed activation never leaves a partial stack behind.
        stop_all

        echo "turning displays on after failed start"

        swaymsg \
            "output * power on" \
            >/dev/null 2>&1 ||
            true

        exit 1
    fi

    # Re-check both parts after startup.
    CONTROL_VALUE=""

    if CONTROL_VALUE="$(current_control 2>/dev/null)"; then
        CONTROL_ACTIVE=1
    else
        CONTROL_ACTIVE=0
    fi

    if hotspot_active; then
        HOTSPOT_ACTIVE=1
    else
        HOTSPOT_ACTIVE=0
    fi

    if (( CONTROL_ACTIVE == 0 || HOTSPOT_ACTIVE == 0 )); then
        echo "ERROR: activation produced a partial state"
        echo "control_active=$CONTROL_ACTIVE"
        echo "hotspot_active=$HOTSPOT_ACTIVE"

        stop_all

        swaymsg \
            "output * power on" \
            >/dev/null 2>&1 ||
            true

        echo "toggle result=partial-start-cleaned-up"
        exit 1
    fi

    echo "toggle result=active"
    echo "control=$CONTROL_VALUE"
    echo "hotspot=$HOTSPOT_CONNECTION"
    echo "turning displays off"

    release_lock
    trap - EXIT INT TERM HUP

    if ! swaymsg \
        "output * power off" \
        >/dev/null; then

        echo "ERROR: failed to turn displays off"

        # Leave AirPlay running because startup itself succeeded.
        exit 1
    fi

    exit 0
fi

###############################################################################
# BOTH ACTIVE OR PARTIAL STATE: STOP EVERYTHING AND SUSPEND
###############################################################################

if (( CONTROL_ACTIVE == 1 && HOTSPOT_ACTIVE == 1 )); then
    echo "action=stop-complete-stack-and-suspend"
else
    echo "action=clean-partial-state-and-suspend"
    echo "partial control_active=$CONTROL_ACTIVE hotspot_active=$HOTSPOT_ACTIVE"
fi

# Remember the CamillaDSP output only if one is genuinely active.
if (( CONTROL_ACTIVE == 1 )); then
    read -r \
        CURRENT_SLOT \
        CURRENT_PROFILE \
        <<<"$CONTROL_VALUE"

    save_control \
        "$CURRENT_SLOT" \
        "$CURRENT_PROFILE"
fi

# This safely handles complete and partial states:
# - Stops either CamillaDSP instance if present.
# - Stops Shairport Sync if present.
# - Stops NQPTP if present.
# - Stops the AirPlay Direct connection if present.
# - Restores PipeWire.
stop_all

###############################################################################
# VERIFY COMPLETE SHUTDOWN
###############################################################################

CONTROL_VALUE=""

if CONTROL_VALUE="$(current_control 2>/dev/null)"; then
    CONTROL_ACTIVE=1
else
    CONTROL_ACTIVE=0
fi

if hotspot_active; then
    HOTSPOT_ACTIVE=1
else
    HOTSPOT_ACTIVE=0
fi

echo "post-stop control_active=$CONTROL_ACTIVE"
echo "post-stop hotspot_active=$HOTSPOT_ACTIVE"
echo "post-stop shairport=$(shairport_running && echo active || echo stopped)"
echo "post-stop nqptp=$(nqptp_running && echo active || echo stopped)"

###############################################################################
# TURN DISPLAY ON, THEN SUSPEND
###############################################################################

echo "turning displays on"

if ! swaymsg \
    "output * power on" \
    >/dev/null; then

    echo "WARNING: Sway rejected the display power-on command"
fi

echo "toggle result=inactive"
echo "suspending"

sync

release_lock
trap - EXIT INT TERM HUP

systemctl suspend
exit $?
