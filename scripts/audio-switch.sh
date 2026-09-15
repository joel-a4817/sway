#!/usr/bin/env bash

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_STARTED_FILE="/tmp/audio-toggle-started.$$"
ACTION_LOG="/tmp/audio-toggle-action.$$"
CARD_SELECTION_FILE="/tmp/audio-card-selected.$$"
PROFILE_SELECTION_FILE="/tmp/audio-profile-selected.$$"

rm -f \
    "$RESULT_FILE" \
    "$ACTION_STARTED_FILE" \
    "$ACTION_LOG" \
    "$CARD_SELECTION_FILE" \
    "$PROFILE_SELECTION_FILE"
touch "$ACTION_LOG"

FINAL_PAUSE_REACHED=0
EXIT_HANDLER_RUNNING=0

cleanup() {
    rm -f \
        "$RESULT_FILE" \
        "$ACTION_STARTED_FILE" \
        "$ACTION_LOG" \
        "$CARD_SELECTION_FILE" \
        "$PROFILE_SELECTION_FILE" \
        "${SWAYNAG_LOG:-}"
}

pause_before_close() {
    FINAL_PAUSE_REACHED=1

    echo

    if [[ -r /dev/tty && -w /dev/tty ]]; then
        read \
            -n 1 \
            -r \
            -s \
            -p "Press any key to close..." \
            </dev/tty || true

        echo >/dev/tty
    else
        echo "No controlling terminal is available."
    fi
}

handle_script_exit() {
    local exit_status=$?

    if (( EXIT_HANDLER_RUNNING )); then
        return
    fi

    EXIT_HANDLER_RUNNING=1

    trap - EXIT INT TERM HUP QUIT

    if (( exit_status != 0 && ! FINAL_PAUSE_REACHED )); then
        echo
        echo "Audio script exited unexpectedly."
        echo "Exit status: $exit_status"

        if [[ -s "$ACTION_LOG" ]]; then
            echo
            echo "Action log:"
            echo "$ACTION_LOG"
            echo
            cat "$ACTION_LOG"
        fi

        pause_before_close
    fi

    cleanup

    builtin exit "$exit_status"
}

trap handle_script_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap 'exit 131' QUIT

echo '{ "command": ["set_property", "pause", true] }' | socat - /tmp/mpvsocket >/dev/null 2>&1

normalize_audio_volumes() {
    local restore_volume="${1:-}"
    local restore_sink="${2:-}"
    local sof_card hyperx_card sink

    sof_card="$(aplay -l | awk -F': ' '/sof|SOF/ {print $1; exit}' | grep -o '[0-9]\+' || true)"
    if [[ -n "$sof_card" ]]; then
        amixer -c "$sof_card" sset Headphone 100% >/dev/null 2>&1 || true
    fi

    hyperx_card="$(aplay -l | awk -F': ' '/HyperX Cloud III/ {print $1; exit}' | grep -o '[0-9]\+' || true)"
    if [[ -n "$hyperx_card" ]]; then
        amixer -c "$hyperx_card" sset 'Speaker Volume' 100% unmute >/dev/null 2>&1 || true
    fi

    while read -r sink; do
        [[ -z "$sink" ]] && continue
        pactl set-sink-mute "$sink" 0 >/dev/null 2>&1 || true
        pactl set-sink-volume "$sink" 100% >/dev/null 2>&1 || true
    done < <(
        pactl list short sinks 2>/dev/null |
        awk '{print $2}'
    )

    if [[ -n "$restore_volume" && -n "$restore_sink" ]]; then
        if pactl list short sinks 2>/dev/null |
           awk '{print $2}' |
           grep -Fqx "$restore_sink"; then

            pactl set-sink-volume \
                "$restore_sink" \
                "$restore_volume" \
                >/dev/null 2>&1 || true
        fi
    fi
}

export -f normalize_audio_volumes

ORIGINAL_SINK=""
ORIGINAL_VOLUME=""

if pactl info >/dev/null 2>&1; then
    ORIGINAL_SINK="$(pactl get-default-sink 2>/dev/null || true)"

    if pactl list short sinks 2>/dev/null |
       awk '{print $2}' |
       grep -Fqx "$ORIGINAL_SINK"; then

        ORIGINAL_VOLUME="$(
            pactl get-sink-volume "$ORIGINAL_SINK" 2>/dev/null |
            grep -Po '[0-9]+%' |
            head -n1
        )"
    fi

    normalize_audio_volumes \
        "$ORIGINAL_VOLUME" \
        "$ORIGINAL_SINK"
fi

export \
    RESULT_FILE \
    ACTION_STARTED_FILE \
    ACTION_LOG \
    CARD_SELECTION_FILE \
    PROFILE_SELECTION_FILE \
    ORIGINAL_VOLUME \
    ORIGINAL_SINK

###############################################################################
# DISCOVER CARDS
###############################################################################

mapfile -t CARDS < <(
    pactl list cards 2>/dev/null | awk '
    function output_card() {
        if (card != "") {
            if (description == "") description = card
            print card "|" description
        }
    }
    /^Card #[0-9]+/ {
        output_card()
        card = ""
        description = ""
        next
    }
    /^[[:space:]]*Name:/ {
        card = $0
        sub(/^[[:space:]]*Name:[[:space:]]*/, "", card)
        next
    }
    /^[[:space:]]*device\.description[[:space:]]*=/ {
        if (description == "") {
            description = $0
            sub(/^[[:space:]]*device\.description[[:space:]]*=[[:space:]]*/, "", description)
            gsub(/^"/, "", description)
            gsub(/"$/, "", description)
        }
        next
    }
    END { output_card() }
    '
)

if (( ${#CARDS[@]} == 0 )); then
    PIPEWIRE_CARDS_AVAILABLE=0
    echo "PipeWire is unavailable or currently stopped." \
        >>"$ACTION_LOG"
else
    PIPEWIRE_CARDS_AVAILABLE=1
fi

card_display_label() {
    local card="$1" description="$2" label="$2"
    case "$card $description" in
        *HyperX_Cloud_III*|*"HyperX Cloud III"*) label="HyperX Cloud III" ;;
        *sof*|*SOF*|*"sof-hda-dsp"*|*"Built-in Audio"*) label="Built-in Audio" ;;
        *HDMI*|*hdmi*)
            [[ "$description" != *HDMI* ]] && label="$description HDMI"
            ;;
    esac
    printf '%s\n' "$label"
}

###############################################################################
# SWAYNAG DEVICE MENU
###############################################################################

ARGS=(-t warning -y overlay -m "Audio Device Select")
AIRPLAY_CONFIG_DIR="$HOME/Documents/prefs/audio/airplay"
AUDIO_STACK_DIR="${XDG_RUNTIME_DIR:-/tmp}/${USER:-user}-airplay-stack"
RUNTIME_CONFIG_DIR="$AUDIO_STACK_DIR/configs"

mkdir -p "$AUDIO_STACK_DIR" "$RUNTIME_CONFIG_DIR"

HYPERX_CLOUD3_TEMPLATE="$AIRPLAY_CONFIG_DIR/camilladsp-hyperx-cloud3.yml"
HYPERX_EARPODS_TEMPLATE="$AIRPLAY_CONFIG_DIR/camilladsp-hyperx-earpods.yml"
BUILTIN_CLOUD3_TEMPLATE="$AIRPLAY_CONFIG_DIR/camilladsp-builtin-cloud3.yml"
BUILTIN_EARPODS_TEMPLATE="$AIRPLAY_CONFIG_DIR/camilladsp-builtin-earpods.yml"

HYPERX_CLOUD3_CONFIG="$RUNTIME_CONFIG_DIR/camilladsp-hyperx-cloud3.yml"
HYPERX_EARPODS_CONFIG="$RUNTIME_CONFIG_DIR/camilladsp-hyperx-earpods.yml"
BUILTIN_CLOUD3_CONFIG="$RUNTIME_CONFIG_DIR/camilladsp-builtin-cloud3.yml"
BUILTIN_EARPODS_CONFIG="$RUNTIME_CONFIG_DIR/camilladsp-builtin-earpods.yml"

HYPERX_STATE_FILE="$AUDIO_STACK_DIR/hyperx-profile"
BUILTIN_STATE_FILE="$AUDIO_STACK_DIR/builtin-profile"

HYPERX_PID_FILE="$AUDIO_STACK_DIR/camilladsp-hyperx.pid"
BUILTIN_PID_FILE="$AUDIO_STACK_DIR/camilladsp-builtin.pid"

HYPERX_LOG="$AUDIO_STACK_DIR/camilladsp-hyperx.log"
BUILTIN_LOG="$AUDIO_STACK_DIR/camilladsp-builtin.log"

SHAIRPORT_LOG="$AUDIO_STACK_DIR/shairport-sync.log"
NQPTP_LOG="$AUDIO_STACK_DIR/nqptp.log"
mkdir -p "$AUDIO_STACK_DIR"

###############################################################################
# GENERATE MACHINE-LOCAL CAMILLADSP CONFIGS
###############################################################################

find_playback_device() {
    local wanted_type="$1"
    local card_dir
    local card_number
    local card_id
    local card_name
    local device_dir
    local device_number
    local sys_path

    for card_dir in /sys/class/sound/card[0-9]*; do
        [[ -e "$card_dir" ]] || continue

        card_number="${card_dir##*card}"
        sys_path="$(readlink -f "$card_dir/device" 2>/dev/null || true)"
        card_id="$(cat "$card_dir/id" 2>/dev/null || true)"
        card_name="$(cat "/proc/asound/card${card_number}/id" 2>/dev/null || true)"

        [[ -n "$sys_path" ]] || continue

        case "$wanted_type" in
            hyperx)
                [[ "$sys_path" == *"/usb"* ]] || continue

                if [[ "$card_id $card_name" != *HyperX* &&
                      "$card_id $card_name" != *Cloud* ]]; then
                    continue
                fi
                ;;

            builtin)
                [[ "$sys_path" != *"/usb"* ]] || continue
                [[ "$sys_path" != *"/virtual/"* ]] || continue

                if [[ "$card_id $card_name" != *sof* &&
                      "$card_id $card_name" != *SOF* &&
                      "$card_id $card_name" != *PCH* ]]; then
                    continue
                fi
                ;;

            *)
                return 1
                ;;
        esac

        for device_dir in \
            "/sys/class/sound/card${card_number}"/pcm*p; do

            [[ -e "$device_dir" ]] || continue

            device_number="$(
                basename "$device_dir" |
                sed -nE 's/^pcmC[0-9]+D([0-9]+)p$/\1/p'
            )"

            [[ "$device_number" =~ ^[0-9]+$ ]] || continue

            if [[ "$wanted_type" == "builtin" ]]; then
                printf 'plughw:%s,%s\n' \
                    "$card_number" \
                    "$device_number"
            else
                printf 'hw:%s,%s\n' \
                    "$card_number" \
                    "$device_number"
            fi

            return 0
        done
    done

    return 1
}

USB_PLAYBACK_DEVICE="$(
    find_playback_device hyperx || true
)"

BUILTIN_PLAYBACK_DEVICE="$(
    find_playback_device builtin || true
)"

generate_camilla_config() {
    local template="$1"
    local destination="$2"
    local playback_device="$3"
    local temporary

    [[ -f "$template" ]] || {
        echo "Missing CamillaDSP template: $template" \
            >>"$ACTION_LOG"

        return 1
    }

    [[ -n "$playback_device" ]] || {
        echo "No suitable playback device was detected for: $template" \
            >>"$ACTION_LOG"

        return 1
    }

    temporary="${destination}.tmp"

    sed \
        -e "s|__HOME__|$HOME|g" \
        -e "s|__USB_PLAYBACK_DEVICE__|$playback_device|g" \
        -e "s|__BUILTIN_PLAYBACK_DEVICE__|$playback_device|g" \
        "$template" \
        >"$temporary" ||
        return 1

    camilladsp -c "$temporary" \
        >>"$ACTION_LOG" 2>&1 ||
        return 1

    mv -f "$temporary" "$destination"
}

generate_camilla_config \
    "$HYPERX_CLOUD3_TEMPLATE" \
    "$HYPERX_CLOUD3_CONFIG" \
    "$USB_PLAYBACK_DEVICE" ||
    true

generate_camilla_config \
    "$HYPERX_EARPODS_TEMPLATE" \
    "$HYPERX_EARPODS_CONFIG" \
    "$USB_PLAYBACK_DEVICE" ||
    true

generate_camilla_config \
    "$BUILTIN_CLOUD3_TEMPLATE" \
    "$BUILTIN_CLOUD3_CONFIG" \
    "$BUILTIN_PLAYBACK_DEVICE" ||
    true

generate_camilla_config \
    "$BUILTIN_EARPODS_TEMPLATE" \
    "$BUILTIN_EARPODS_CONFIG" \
    "$BUILTIN_PLAYBACK_DEVICE" ||
    true

HOTSPOT_CONNECTION="AirPlay Direct"

###############################################################################
# AUDIO RESTART BUTTON
###############################################################################

RESET_ACTION="$(cat <<EOF_RESET
touch "$ACTION_STARTED_FILE"
echo "Audio Restart action started." >>"$ACTION_LOG"

for proc in /proc/[[:digit:]]*; do
    [ -d "\$proc" ] || continue

    pid="\${proc##*/}"
    exe="\$(readlink -f "\$proc/exe" 2>/dev/null || true)"

    [ -n "\$exe" ] || continue

    if [ "\${exe##*/}" = "shairport-sync" ]; then
        kill -TERM "\$pid" 2>/dev/null || true
    fi
done

pkill -TERM -x camilladsp >/dev/null 2>&1 || true
sudo -n pkill -TERM -x nqptp >/dev/null 2>&1 || true

rm -f \
    "$AUDIO_STACK_DIR/shairport-sync.pid" \
    "$AUDIO_STACK_DIR/shairport-sync.port" \
    "$AUDIO_STACK_DIR/shairport-sync-runtime.conf" \
    "$AUDIO_STACK_DIR/hyperx-profile" \
    "$AUDIO_STACK_DIR/builtin-profile" \
    "$AUDIO_STACK_DIR/camilladsp-hyperx.pid" \
    "$AUDIO_STACK_DIR/camilladsp-builtin.pid" \
    "$AUDIO_STACK_DIR/shairport-hyperx.pid" \
    "$AUDIO_STACK_DIR/shairport-builtin.pid"

for _ in \$(seq 1 50); do
    SHARED_AUDIO_BUSY=0

    pgrep -x camilladsp >/dev/null 2>&1 &&
        SHARED_AUDIO_BUSY=1

    pgrep -x nqptp >/dev/null 2>&1 &&
        SHARED_AUDIO_BUSY=1

    for proc in /proc/[[:digit:]]*; do
        [ -d "\$proc" ] || continue

        exe="\$(readlink -f "\$proc/exe" 2>/dev/null || true)"

        if [ "\${exe##*/}" = "shairport-sync" ]; then
            SHARED_AUDIO_BUSY=1
            break
        fi
    done

    [ "\$SHARED_AUDIO_BUSY" -eq 0 ] &&
        break

    sleep 0.1
done

systemctl --user stop \
    wireplumber.service \
    pipewire-pulse.service \
    pipewire-pulse.socket \
    pipewire.service \
    pipewire.socket \
    >/dev/null 2>&1 || true

rm -rf "\$HOME/.local/state/wireplumber"

systemctl --user start \
    pipewire.socket \
    pipewire-pulse.socket \
    pipewire.service \
    pipewire-pulse.service \
    wireplumber.service \
    >/dev/null 2>&1 || true

PIPEWIRE_READY=0

for _ in \$(seq 1 100); do
    if pactl info >/dev/null 2>&1; then
        PIPEWIRE_READY=1
        break
    fi

    sleep 0.1
done

if [ "\$PIPEWIRE_READY" -eq 1 ]; then
    CURRENT_SINK="\$(pactl get-default-sink 2>/dev/null || true)"

    normalize_audio_volumes \
        "$ORIGINAL_VOLUME" \
        "\$CURRENT_SINK"

    printf '%s\n' "Audio reset" >"$CARD_SELECTION_FILE"
else
    printf '%s\n' "Audio reset failed" >"$CARD_SELECTION_FILE"
fi

touch "$RESULT_FILE"
EOF_RESET
)"

ARGS+=(
    -z "Audio Restart"
    "$RESET_ACTION"
)

###############################################################################
# AIRPLAY + CAMILLADSP PROFILE BUTTONS
###############################################################################
hotspot_active() {
    nmcli \
        -t \
        -f NAME \
        connection show --active \
        2>/dev/null |
    grep -Fqx "$HOTSPOT_CONNECTION"
}

toggle_hotspot() {
    if hotspot_active; then
        echo "Stopping hotspot: $HOTSPOT_CONNECTION" \
            >>"$ACTION_LOG"

        if nmcli connection down "$HOTSPOT_CONNECTION" \
            >>"$ACTION_LOG" 2>&1; then

            printf '%s\n' "Hotspot stopped" \
                >"$CARD_SELECTION_FILE"

            return 0
        fi

        printf '%s\n' "Hotspot action failed" \
            >"$CARD_SELECTION_FILE"

        return 1
    fi

    echo "Starting hotspot: $HOTSPOT_CONNECTION" \
        >>"$ACTION_LOG"

    if nmcli connection up "$HOTSPOT_CONNECTION" \
        >>"$ACTION_LOG" 2>&1; then

        printf '%s\n' "Hotspot started" \
            >"$CARD_SELECTION_FILE"

        return 0
    fi

    printf '%s\n' "Hotspot action failed" \
        >"$CARD_SELECTION_FILE"

    return 1
}

camilladsp_running() {
    pgrep -x camilladsp >/dev/null 2>&1
}

shairport_pids() {
    local proc
    local pid
    local exe

    for proc in /proc/[[:digit:]]*; do
        [[ -d "$proc" ]] || continue

        pid="${proc##*/}"

        exe="$(
            readlink -f "$proc/exe" \
                2>/dev/null || true
        )"

        [[ -n "$exe" ]] || continue

        if [[ "${exe##*/}" == "shairport-sync" ]]; then
            printf '%s\n' "$pid"
        fi
    done
}

shairport_running() {
    local shairport_pid

    while read -r shairport_pid; do
        [[ -n "$shairport_pid" ]] && return 0
    done < <(shairport_pids)

    return 1
}

nqptp_running() {
    pgrep -x nqptp >/dev/null 2>&1
}

read_state_file() {
    local state_file="$1"

    if [[ -f "$state_file" ]]; then
        cat "$state_file"
    fi
}

pid_file_running() {
    local pid_file="$1"
    local pid
    local exe

    [[ -s "$pid_file" ]] || return 1

    read -r pid <"$pid_file"

    if [[ ! "$pid" =~ ^[[:digit:]]+$ ]]; then
        rm -f "$pid_file"
        return 1
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        return 1
    fi

    exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"

    if [[ "${exe##*/}" != "camilladsp" ]]; then
        rm -f "$pid_file"
        return 1
    fi

    return 0
}

if ! pid_file_running "$HYPERX_PID_FILE"; then
    rm -f "$HYPERX_STATE_FILE"
fi

if ! pid_file_running "$BUILTIN_PID_FILE"; then
    rm -f "$BUILTIN_STATE_FILE"
fi

HYPERX_STATE="$(read_state_file "$HYPERX_STATE_FILE")"
BUILTIN_STATE="$(read_state_file "$BUILTIN_STATE_FILE")"

if pid_file_running "$HYPERX_PID_FILE" ||
   pid_file_running "$BUILTIN_PID_FILE"; then

    AIRPLAY_CONTROLS_LABEL="AirPlay Controls"

    if pid_file_running "$HYPERX_PID_FILE"; then
        AIRPLAY_CONTROLS_LABEL+=" [HyperX: ${HYPERX_STATE:-active}]"
    fi

    if pid_file_running "$BUILTIN_PID_FILE"; then
        AIRPLAY_CONTROLS_LABEL+=" [Built-in: ${BUILTIN_STATE:-active}]"
    fi
elif shairport_running || nqptp_running; then
    AIRPLAY_CONTROLS_LABEL="AirPlay Controls [partial stack active]"
else
    AIRPLAY_CONTROLS_LABEL="AirPlay Controls [stopped]"
fi

ARGS+=(
    -z "$AIRPLAY_CONTROLS_LABEL"
    "touch '$ACTION_STARTED_FILE'; printf '%s\n' 'airplay-controls' >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
)
if hotspot_active; then
    HOTSPOT_BUTTON_LABEL="Stop AirPlay Hotspot [active]"
else
    HOTSPOT_BUTTON_LABEL="Start AirPlay Hotspot [inactive]"
fi

ARGS+=(
    -z "$HOTSPOT_BUTTON_LABEL"
    "touch '$ACTION_STARTED_FILE'; printf '%s\n' 'toggle-airplay-hotspot' >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
)

###############################################################################
# AUDIO CARD BUTTONS
###############################################################################

if (( PIPEWIRE_CARDS_AVAILABLE )); then
    for i in "${!CARDS[@]}"; do
        card="${CARDS[$i]%%|*}"
        description="${CARDS[$i]#*|}"
        label="$(card_display_label "$card" "$description")"

        ARGS+=(
            -z "$label"
            "touch '$ACTION_STARTED_FILE'; printf '%s\n' '$i' >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
        )
    done
fi

###############################################################################
# SHOW SWAYNAG
###############################################################################

SWAYNAG_LOG="/tmp/audio-switch-swaynag.$$"

swaynag "${ARGS[@]}" \
    >"$SWAYNAG_LOG" 2>&1 &

SWAYNAG_PID=$!


while [[ ! -f "$RESULT_FILE" ]]; do
    if kill -0 "$SWAYNAG_PID" 2>/dev/null; then
        sleep 0.1
        continue
    fi

    wait "$SWAYNAG_PID" 2>/dev/null
    SWAYNAG_STATUS=$?

    if [[ -f "$ACTION_STARTED_FILE" ]]; then
        echo "Waiting for the selected audio action to finish." \
            >>"$ACTION_LOG"

        while [[ ! -f "$RESULT_FILE" ]]; do
            sleep 0.1
        done

        break
    fi

    if [[ -s "$SWAYNAG_LOG" ]]; then
        echo
        echo "Swaynag exited before completing the action."
        echo
        cat "$SWAYNAG_LOG"

        exit "${SWAYNAG_STATUS:-1}"
    fi

    echo
    echo "No audio action was selected."

    pause_before_close
    exit 0
done

if kill -0 "$SWAYNAG_PID" 2>/dev/null; then
    kill "$SWAYNAG_PID" 2>/dev/null || true
fi

wait "$SWAYNAG_PID" 2>/dev/null || true

if [[ ! -f "$CARD_SELECTION_FILE" ]]; then
    echo "No audio action was selected."
    exit 1
fi

SELECTED_CARD_VALUE="$(cat "$CARD_SELECTION_FILE")"

###############################################################################
# HANDLE HOTSPOT TOGGLE
###############################################################################

if [[ "$SELECTED_CARD_VALUE" == "toggle-airplay-hotspot" ]]; then
    if toggle_hotspot; then
        HOTSPOT_RESULT="$(
            cat "$CARD_SELECTION_FILE" \
                2>/dev/null || true
        )"

        echo

        case "$HOTSPOT_RESULT" in
            "Hotspot started")
                echo "AirPlay hotspot started."
                echo
                echo "NetworkManager connection:"
                echo "$HOTSPOT_CONNECTION"
                ;;

            "Hotspot stopped")
                echo "AirPlay hotspot stopped."
                ;;

            *)
                echo "AirPlay hotspot action completed."
                ;;
        esac

    pause_before_close
        exit 0
    fi

    echo
    echo "The AirPlay hotspot could not be toggled."

    if [[ -s "$ACTION_LOG" ]]; then
        echo
        echo "Details:"
        cat "$ACTION_LOG"
    fi

    exit 1
fi




###############################################################################
# HANDLE COMBINED AIRPLAY CONTROLS MENU
###############################################################################
if [[ "$SELECTED_CARD_VALUE" == "airplay-controls" ]]; then
    HYPERX_STATE="$(read_state_file "$HYPERX_STATE_FILE")"
    BUILTIN_STATE="$(read_state_file "$BUILTIN_STATE_FILE")"

    HYPERX_CLOUD_LABEL="HyperX: Cloud III"
    HYPERX_EARPODS_LABEL="HyperX: EarPods"
    BUILTIN_CLOUD_LABEL="Built-in: Cloud III"
    BUILTIN_EARPODS_LABEL="Built-in: EarPods"

    if pid_file_running "$HYPERX_PID_FILE"; then
        case "$HYPERX_STATE" in
            cloud3)
                HYPERX_CLOUD_LABEL+=" [active, select to stop]"
                ;;
            earpods)
                HYPERX_EARPODS_LABEL+=" [active, select to stop]"
                ;;
        esac
    fi

    if pid_file_running "$BUILTIN_PID_FILE"; then
        case "$BUILTIN_STATE" in
            cloud3)
                BUILTIN_CLOUD_LABEL+=" [active, select to stop]"
                ;;
            earpods)
                BUILTIN_EARPODS_LABEL+=" [active, select to stop]"
                ;;
        esac
    fi

    echo
    echo "AirPlay Controls"
    echo
    echo "Shared services"
    printf 'NQPTP:      '
    nqptp_running && echo running || echo stopped
    printf 'Shairport:  '
    shairport_running && echo running || echo stopped
    echo
    echo "Output slots"
    printf 'HyperX:     '
    if pid_file_running "$HYPERX_PID_FILE"; then
        echo "${HYPERX_STATE:-active}"
    else
        echo stopped
    fi

    printf 'Built-in:   '
    if pid_file_running "$BUILTIN_PID_FILE"; then
        echo "${BUILTIN_STATE:-active}"
    else
        echo stopped
    fi

    echo
    echo "[0] Exit without changes"
    echo "[1] $HYPERX_CLOUD_LABEL"
    echo "[2] $HYPERX_EARPODS_LABEL"
    echo "[3] $BUILTIN_CLOUD_LABEL"
    echo "[4] $BUILTIN_EARPODS_LABEL"
    echo "[5] Restart AirPlay receiver"
    echo

    read -rp "Select AirPlay action: " AIRPLAY_CHOICE

    case "$AIRPLAY_CHOICE" in
        0)
            echo
            echo "No changes made."
    pause_before_close
            exit 0
            ;;
        1)
            SELECTED_CARD_VALUE="dual-hyperx-cloud3"
            ;;
        2)
            SELECTED_CARD_VALUE="dual-hyperx-earpods"
            ;;
        3)
            SELECTED_CARD_VALUE="dual-builtin-cloud3"
            ;;
        4)
            SELECTED_CARD_VALUE="dual-builtin-earpods"
            ;;
        5)
            SELECTED_CARD_VALUE="restart-airplay-stream"
            ;;
        *)
            echo "Invalid AirPlay selection."
            exit 1
            ;;
    esac
fi

if [[ "$SELECTED_CARD_VALUE" == "restart-airplay-stream" ]]; then
    SHAIRPORT_CONFIG="/home/joel/Documents/prefs/audio/airplay/shairport-sync.conf"

    mapfile -t EXISTING_SHAIRPORT_PIDS < <(
        shairport_pids
    )

    for shairport_pid in "${EXISTING_SHAIRPORT_PIDS[@]}"; do
        [[ "$shairport_pid" =~ ^[[:digit:]]+$ ]] || continue
        kill -TERM "$shairport_pid" 2>/dev/null || true
    done

    for _ in {1..50}; do
        shairport_running || break
        sleep 0.1
    done

    if shairport_running; then
        mapfile -t EXISTING_SHAIRPORT_PIDS < <(
            shairport_pids
        )

        for shairport_pid in "${EXISTING_SHAIRPORT_PIDS[@]}"; do
            [[ "$shairport_pid" =~ ^[[:digit:]]+$ ]] || continue
            kill -KILL "$shairport_pid" 2>/dev/null || true
        done
    fi

    rm -f "$AUDIO_STACK_DIR/shairport-sync.pid"
    : >"$SHAIRPORT_LOG"

    if [[ ! -f "$SHAIRPORT_CONFIG" ]]; then
        echo
        echo "Shairport configuration was not found:"
        echo "$SHAIRPORT_CONFIG"
        echo

    pause_before_close
        exit 1
    fi

    nohup shairport-sync \
        -c "$SHAIRPORT_CONFIG" \
        -vv \
        >>"$SHAIRPORT_LOG" 2>&1 &

    shairport_pid=$!

    printf '%s\n' "$shairport_pid" \
        >"$AUDIO_STACK_DIR/shairport-sync.pid"

    SHAIRPORT_RESTART_OK=1

    for _ in {1..30}; do
        if ! kill -0 "$shairport_pid" 2>/dev/null; then
            SHAIRPORT_RESTART_OK=0
            break
        fi

        sleep 0.1
    done

    if (( ! SHAIRPORT_RESTART_OK )); then
        echo
        echo "Shairport Sync exited during restart."
        echo
        tail -n 100 "$SHAIRPORT_LOG"
        echo

        rm -f "$AUDIO_STACK_DIR/shairport-sync.pid"

    pause_before_close
        exit 1
    fi

    echo
    echo "AirPlay receiver restarted."
    echo "Reconnect Joel Laptop AirPlay on the sender."
    echo

    pause_before_close
    exit 0
fi
###############################################################################
# HANDLE INDEPENDENT HYPERX AND BUILT-IN CAMILLADSP SLOTS
###############################################################################

if [[ "$SELECTED_CARD_VALUE" == dual-* ]]; then
    case "$SELECTED_CARD_VALUE" in
        dual-hyperx-cloud3)
            SLOT="hyperx"
            PROFILE="cloud3"
            CONFIG="$HYPERX_CLOUD3_CONFIG"
            PID_FILE="$HYPERX_PID_FILE"
            STATE_FILE="$HYPERX_STATE_FILE"
            LOG_FILE="$HYPERX_LOG"
            OUTPUT_LABEL="HyperX"
            ;;
        dual-hyperx-earpods)
            SLOT="hyperx"
            PROFILE="earpods"
            CONFIG="$HYPERX_EARPODS_CONFIG"
            PID_FILE="$HYPERX_PID_FILE"
            STATE_FILE="$HYPERX_STATE_FILE"
            LOG_FILE="$HYPERX_LOG"
            OUTPUT_LABEL="HyperX"
            ;;
        dual-builtin-cloud3)
            SLOT="builtin"
            PROFILE="cloud3"
            CONFIG="$BUILTIN_CLOUD3_CONFIG"
            PID_FILE="$BUILTIN_PID_FILE"
            STATE_FILE="$BUILTIN_STATE_FILE"
            LOG_FILE="$BUILTIN_LOG"
            OUTPUT_LABEL="Built-in"
            ;;
        dual-builtin-earpods)
            SLOT="builtin"
            PROFILE="earpods"
            CONFIG="$BUILTIN_EARPODS_CONFIG"
            PID_FILE="$BUILTIN_PID_FILE"
            STATE_FILE="$BUILTIN_STATE_FILE"
            LOG_FILE="$BUILTIN_LOG"
            OUTPUT_LABEL="Built-in"
            ;;
        *)
            echo "Unknown dual-output action."
            exit 1
            ;;
    esac

    stop_camilla_slot() {
        local pid_file="$1"
        local state_file="$2"
        local pid

        if [[ -s "$pid_file" ]]; then
            read -r pid <"$pid_file"

            if [[ "$pid" =~ ^[[:digit:]]+$ ]]; then
                kill -TERM "$pid" 2>/dev/null || true

                for _ in {1..50}; do
                    kill -0 "$pid" 2>/dev/null || break
                    sleep 0.1
                done

                if kill -0 "$pid" 2>/dev/null; then
                    kill -KILL "$pid" 2>/dev/null || true
                fi
            fi
        fi

        rm -f "$pid_file" "$state_file"
    }

    stop_all_shairport() {
        local pid

        while read -r pid; do
            [[ "$pid" =~ ^[[:digit:]]+$ ]] || continue
            kill -TERM "$pid" 2>/dev/null || true
        done < <(shairport_pids)

        for _ in {1..50}; do
            shairport_running || return 0
            sleep 0.1
        done

        while read -r pid; do
            [[ "$pid" =~ ^[[:digit:]]+$ ]] || continue
            kill -KILL "$pid" 2>/dev/null || true
        done < <(shairport_pids)
    }

    stop_shared_airplay() {
        stop_all_shairport

        sudo -n pkill -TERM -x nqptp 2>/dev/null ||
            pkill -TERM -x nqptp 2>/dev/null ||
            true

        for _ in {1..50}; do
            nqptp_running || break
            sleep 0.1
        done

        if nqptp_running; then
            sudo -n pkill -KILL -x nqptp 2>/dev/null ||
                pkill -KILL -x nqptp 2>/dev/null ||
                true
        fi

        rm -f "$AUDIO_STACK_DIR/shairport-sync.pid"
    }

    start_shared_airplay() {
        local shairport_pid

        if ! nqptp_running; then
            : >"$NQPTP_LOG"

            sudo -n sh -c \
                "nohup '$(command -v nqptp)' >'$NQPTP_LOG' 2>&1 &"

            for _ in {1..50}; do
                nqptp_running && break
                sleep 0.1
            done

            nqptp_running || return 1
        fi

        if ! shairport_running; then
            : >"$SHAIRPORT_LOG"

            nohup shairport-sync \
                -c "$AIRPLAY_CONFIG_DIR/shairport-sync.conf" \
                -vv \
                >>"$SHAIRPORT_LOG" 2>&1 &

            shairport_pid=$!

            printf '%s\n' "$shairport_pid" \
                >"$AUDIO_STACK_DIR/shairport-sync.pid"

            for _ in {1..30}; do
                kill -0 "$shairport_pid" 2>/dev/null ||
                    return 1

                sleep 0.1
            done
        fi
    }

    stop_pipewire_for_dual_mode() {
        systemctl --user stop \
            wireplumber.service \
            pipewire-pulse.service \
            pipewire-pulse.socket \
            pipewire.service \
            pipewire.socket \
            >>"$ACTION_LOG" 2>&1 ||
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

    restore_pipewire_after_dual_mode() {
        systemctl --user start \
            pipewire.socket \
            pipewire-pulse.socket \
            pipewire.service \
            pipewire-pulse.service \
            wireplumber.service \
            >>"$ACTION_LOG" 2>&1 ||
            true

        for _ in {1..100}; do
            pactl info >/dev/null 2>&1 && return 0
            sleep 0.1
        done

        return 1
    }

    start_camilla_slot() {
        local config="$1"
        local pid_file="$2"
        local state_file="$3"
        local log_file="$4"
        local profile="$5"
        local pid

        [[ -f "$config" ]] || {
            echo "Missing generated config: $config" \
                >>"$ACTION_LOG"
            return 1
        }

        camilladsp -c "$config" \
            >>"$ACTION_LOG" 2>&1 ||
            return 1

        : >"$log_file"

        nohup camilladsp \
            --loglevel=debug \
            --logfile="$log_file" \
            "$config" \
            >>"$log_file" 2>&1 &

        pid=$!

        printf '%s\n' "$pid" >"$pid_file"

        for _ in {1..30}; do
            if ! kill -0 "$pid" 2>/dev/null; then
                tail -n 100 "$log_file" \
                    >>"$ACTION_LOG" 2>&1 ||
                    true

                rm -f "$pid_file" "$state_file"
                return 1
            fi

            sleep 0.1
        done

        printf '%s\n' "$profile" >"$state_file"
    }

    CURRENT_SLOT_PROFILE="$(read_state_file "$STATE_FILE")"

    if pid_file_running "$PID_FILE" &&
       [[ "$CURRENT_SLOT_PROFILE" == "$PROFILE" ]]; then

        stop_camilla_slot "$HYPERX_PID_FILE" "$HYPERX_STATE_FILE"

        stop_camilla_slot "$BUILTIN_PID_FILE" "$BUILTIN_STATE_FILE"

        stop_shared_airplay
        restore_pipewire_after_dual_mode

        echo
        echo "$OUTPUT_LABEL $PROFILE convolution stopped."
    pause_before_close
        exit 0
    fi

    # Exactly one AirPlay output may run at a time.
    stop_camilla_slot "$HYPERX_PID_FILE" "$HYPERX_STATE_FILE"
    stop_camilla_slot "$BUILTIN_PID_FILE" "$BUILTIN_STATE_FILE"

    if ! stop_pipewire_for_dual_mode; then
        echo
        echo "PipeWire did not release the audio devices."

        stop_shared_airplay
        restore_pipewire_after_dual_mode

        exit 1
    fi

    if ! start_camilla_slot \
        "$CONFIG" \
        "$PID_FILE" \
        "$STATE_FILE" \
        "$LOG_FILE" \
        "$PROFILE"; then

        rm -f "$PID_FILE" "$STATE_FILE"

        if ! pid_file_running "$HYPERX_PID_FILE" &&
           ! pid_file_running "$BUILTIN_PID_FILE"; then

            stop_shared_airplay
            restore_pipewire_after_dual_mode
        fi

        echo
        echo "$OUTPUT_LABEL CamillaDSP instance failed."

        if [[ -s "$LOG_FILE" ]]; then
            echo
            echo "$OUTPUT_LABEL CamillaDSP log:"
            tail -n 100 "$LOG_FILE"
        fi

        if [[ -s "$ACTION_LOG" ]]; then
            echo
            echo "Action log:"
            cat "$ACTION_LOG"
        fi

        exit 1
    fi

    if ! start_shared_airplay; then
        stop_camilla_slot "$PID_FILE" "$STATE_FILE"

        if ! pid_file_running "$HYPERX_PID_FILE" &&
           ! pid_file_running "$BUILTIN_PID_FILE"; then

            restore_pipewire_after_dual_mode
        fi

        echo "Shared AirPlay services failed to start."
        cat "$ACTION_LOG"
        exit 1
    fi

    echo
    echo "$OUTPUT_LABEL $PROFILE convolution started."
    echo
    echo "HyperX:"
    if pid_file_running "$HYPERX_PID_FILE"; then
        read_state_file "$HYPERX_STATE_FILE"
    else
        echo stopped
    fi

    echo
    echo "Built-in:"
    if pid_file_running "$BUILTIN_PID_FILE"; then
        read_state_file "$BUILTIN_STATE_FILE"
    else
        echo stopped
    fi

    pause_before_close
    exit 0
fi
###############################################################################
# HANDLE NON-CARD ACTIONS
###############################################################################

case "$SELECTED_CARD_VALUE" in
    "Audio reset")
        echo
        echo "Audio reset completed."
        echo

    pause_before_close

        exit 0
        ;;
    "Audio reset failed")
        echo
        echo "The DSP stack was stopped, but PipeWire did not become ready."
        echo
        echo "Check:"
        echo "  systemctl --user status pipewire.service"
        echo "  systemctl --user status wireplumber.service"
        echo

    pause_before_close

        exit 1
        ;;
            "Hotspot started")
        echo
        echo "AirPlay hotspot started."
        echo
        echo "Connect the sender to the hotspot network."
        echo

    pause_before_close
        exit 0
        ;;

    "Hotspot stopped")
        echo
        echo "AirPlay hotspot stopped."
        echo

    pause_before_close
        exit 0
        ;;

    "Hotspot action failed")
        echo
        echo "The AirPlay hotspot action failed."
        echo
        cat "$ACTION_LOG"
        echo

    pause_before_close
        exit 1
        ;;
esac

if [[ ! "$SELECTED_CARD_VALUE" =~ ^[0-9]+$ ]] || (( SELECTED_CARD_VALUE >= ${#CARDS[@]} )); then
    echo "Invalid audio device selection."
    exit 1
fi

SELECTED_CARD="${CARDS[$SELECTED_CARD_VALUE]%%|*}"
SELECTED_CARD_DESCRIPTION="${CARDS[$SELECTED_CARD_VALUE]#*|}"
SELECTED_CARD_LABEL="$(card_display_label "$SELECTED_CARD" "$SELECTED_CARD_DESCRIPTION")"

# Requested extra normalization stage after choosing the device/card.
CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
normalize_audio_volumes "$ORIGINAL_VOLUME" "$CURRENT_SINK"

profile_display_label() {
    local card="$1" profile="$2" description="$3" label="$3" hdmi

    if [[ "$description" == *Headphone* ]]; then
        label="Headphones"
    elif [[ "$description" == *Speaker* ]]; then
        label="Speaker"
    elif [[ "$description" == *"Pro Audio"* ]]; then
        label="Pro Audio"
    elif [[ "$description" == *HDMI* ]]; then
        hdmi="1"
        [[ "$description" == *"HDMI 2"* ]] && hdmi="2"
        [[ "$description" == *"HDMI 3"* ]] && hdmi="3"
        label="HDMI $hdmi"
        [[ "$description" == *"7.1"* ]] && label+=" 7.1"
        [[ "$description" == *"5.1"* ]] && label+=" 5.1"
        [[ "$description" == *Input* ]] && label+=" + Mic"
    elif [[ "$profile" == "input:analog-stereo" ]]; then
        label="Mic Only"
    elif [[ "$description" == *Analog* ]]; then
        label="Analog"
        if [[ "$description" == *Input* || "$description" == *Duplex* ]]; then
            label+=" + Mic"
        fi
    fi

    if [[ "$card" == *HyperX_Cloud_III* ]]; then
        case "$profile" in
            output:analog-stereo) label="Cloud III Analog" ;;
            output:analog-stereo+input:mono-fallback) label="Cloud III Analog + Mic" ;;
            output:iec958-stereo) label="Cloud III Digital" ;;
            output:iec958-stereo+input:mono-fallback) label="Cloud III Digital + Mic" ;;
            input:mono-fallback) label="Cloud III Mic" ;;
            pro-audio) label="Cloud III Pro Audio" ;;
        esac
    fi
    printf '%s\n' "$label"
}

###############################################################################
# DISCOVER ALL PROFILES FOR SELECTED CARD
# Stored as: profile|description|availability
###############################################################################

mapfile -t PROFILES < <(
    pactl list cards | awk -v target="$SELECTED_CARD" '
    /^[[:space:]]*Name:/ {
        current = $0
        sub(/^[[:space:]]*Name:[[:space:]]*/, "", current)
        selected = (current == target)
        in_profiles = 0
        next
    }
    selected && /^[[:space:]]*Profiles:/ {
        in_profiles = 1
        next
    }
    selected && in_profiles && /^[[:space:]]*(Active Profile|Active Port|Ports):/ {
        in_profiles = 0
        next
    }
    selected && in_profiles && index($0, "(sinks:") {
        line = $0
        sub(/^[[:space:]]*/, "", line)
        separator = index(line, ": ")
        if (separator == 0) next

        profile = substr(line, 1, separator - 1)
        description = substr(line, separator + 2)
        if (profile == "off") next

        availability = "unknown"
        if (line ~ /available:[[:space:]]*yes/) availability = "yes"
        else if (line ~ /available:[[:space:]]*no/) availability = "no"

        sub(/[[:space:]]+\(sinks:.*/, "", description)
        print profile "|" description "|" availability
    }
    '
)

if (( ${#PROFILES[@]} == 0 )); then
    echo
    echo "No profiles were found for:"
    echo "$SELECTED_CARD"
    echo
    echo "Raw selected-card profile section:"
    pactl list cards | awk -v target="$SELECTED_CARD" '
    /^[[:space:]]*Name:/ {
        current = $0
        sub(/^[[:space:]]*Name:[[:space:]]*/, "", current)
        selected = (current == target)
        in_profiles = 0
        next
    }
    selected && /^[[:space:]]*Profiles:/ { in_profiles = 1; next }
    selected && in_profiles && /^[[:space:]]*Active Profile:/ { exit }
    selected && in_profiles { print }
    '
    pause_before_close
    exit 1
fi

get_active_profile() {
    pactl list cards | awk -v target="$SELECTED_CARD" '
    /^[[:space:]]*Name:/ {
        current = $0
        sub(/^[[:space:]]*Name:[[:space:]]*/, "", current)
        selected = (current == target)
        next
    }
    selected && /^[[:space:]]*Active Profile:/ {
        profile = $0
        sub(/^[[:space:]]*Active Profile:[[:space:]]*/, "", profile)
        print profile
        exit
    }
    '
}

ACTIVE_PROFILE="$(get_active_profile)"
ACTIVE_PROFILE_LABEL="$ACTIVE_PROFILE"

for entry in "${PROFILES[@]}"; do
    profile="${entry%%|*}"
    remainder="${entry#*|}"
    description="${remainder%%|*}"
    if [[ "$profile" == "$ACTIVE_PROFILE" ]]; then
        ACTIVE_PROFILE_LABEL="$(profile_display_label "$SELECTED_CARD" "$profile" "$description")"
        break
    fi
done

###############################################################################
# TERMINAL PROFILE MENU
###############################################################################

echo
echo "Selected Audio Device"
echo
echo "$SELECTED_CARD_LABEL"
echo
echo "Profiles"
echo

if [[ -n "$ACTIVE_PROFILE" ]]; then
    echo "[0] Keep current profile ($ACTIVE_PROFILE_LABEL)"
else
    echo "[0] Keep current profile"
fi

for i in "${!PROFILES[@]}"; do
    entry="${PROFILES[$i]}"
    profile="${entry%%|*}"
    remainder="${entry#*|}"
    description="${remainder%%|*}"
    availability="${remainder##*|}"

    label="$(profile_display_label "$SELECTED_CARD" "$profile" "$description")"
    [[ "$profile" == "$ACTIVE_PROFILE" ]] && label+=" [active]"
    [[ "$availability" == "no" ]] && label+=" [unavailable]"
    [[ "$availability" == "unknown" ]] && label+=" [availability unknown]"

    echo "[$((i + 1))] $label"
done

echo
read -rp "Select profile: " profile_choice

SELECTED_PROFILE="$ACTIVE_PROFILE"
SELECTED_PROFILE_LABEL="$ACTIVE_PROFILE_LABEL"

if [[ "$profile_choice" == "0" ]]; then
    echo "Keeping current profile."
else
    if [[ ! "$profile_choice" =~ ^[0-9]+$ ]]; then
        echo "Invalid profile selection."
        exit 1
    fi

    profile_index=$((profile_choice - 1))
    if (( profile_index < 0 || profile_index >= ${#PROFILES[@]} )); then
        echo "Invalid profile selection."
        exit 1
    fi

    profile_entry="${PROFILES[$profile_index]}"
    SELECTED_PROFILE="${profile_entry%%|*}"
    profile_remainder="${profile_entry#*|}"
    profile_description="${profile_remainder%%|*}"
    profile_availability="${profile_remainder##*|}"

    if [[ "$profile_availability" == "no" ]]; then
        echo "That profile is currently marked unavailable by PipeWire."
        exit 1
    fi

    SELECTED_PROFILE_LABEL="$(profile_display_label "$SELECTED_CARD" "$SELECTED_PROFILE" "$profile_description")"

    if ! pactl set-card-profile "$SELECTED_CARD" "$SELECTED_PROFILE" >>"$ACTION_LOG" 2>&1; then
        echo "Failed to set profile '$SELECTED_PROFILE'."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    printf '%s\n' "$SELECTED_PROFILE_LABEL" > "$PROFILE_SELECTION_FILE"

    PROFILE_OK=0
    for _ in {1..50}; do
        CURRENT_PROFILE="$(get_active_profile)"
        if [[ "$CURRENT_PROFILE" == "$SELECTED_PROFILE" ]]; then
            PROFILE_OK=1
            break
        fi
        sleep 0.1
    done

    if (( ! PROFILE_OK )); then
        echo "Warning: profile did not settle as '$SELECTED_PROFILE'." >>"$ACTION_LOG"
        echo "Warning: profile switch did not settle. See: $ACTION_LOG"
    fi
fi

###############################################################################
# NORMALIZE AFTER PROFILE SELECTION, THEN BUILD FRESH SINK LIST
###############################################################################

CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
for _ in {1..50}; do
    if [[ -n "$CURRENT_SINK" ]] &&
       pactl list short sinks | awk '{print $2}' | grep -Fqx "$CURRENT_SINK"; then
        break
    fi
    sleep 0.1
    CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
done
normalize_audio_volumes "$ORIGINAL_VOLUME" "$CURRENT_SINK"

RANDOM_PICK=0
mapfile -t SINKS < <(
    pactl list short sinks | while read -r id sink rest; do
        label="$sink"
        case "$sink" in
            *HDMI1*) label="HDMI 1" ;;
            *HDMI2*) label="HDMI 2" ;;
            *HDMI3*) label="HDMI 3" ;;
            *pro-output-[0-9]*) label="${sink##*.}" ;;
            *HyperX_Cloud_III*) label="Cloud III USB" ;;
            *Headphones*) label="Headphones" ;;
            *Speaker*) label="Speaker" ;;
            *analog*) label="analog-stereo" ;;
            *hdmi*) label="HDMI" ;;
        esac
        printf '%s|%s\n' "$sink" "$label"
    done
)

CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
CURRENT_DESC="Current Sink"
for entry in "${SINKS[@]}"; do
    candidate="${entry%%|*}"
    desc="${entry#*|}"
    if [[ "$candidate" == "$CURRENT_SINK" ]]; then
        CURRENT_DESC="$desc"
        break
    fi
done

echo
echo "Available Audio Sinks"
echo
echo "[0] Keep current sink ($CURRENT_DESC)"
for i in "${!SINKS[@]}"; do
    echo "[$((i + 1))] ${SINKS[$i]#*|}"
done

EARPOD_RANDOM=$(( ${#SINKS[@]} + 1 ))
CLOUD3_RANDOM=$(( ${#SINKS[@]} + 2 ))
echo "[$EARPOD_RANDOM] Random EarPods"
echo "[$CLOUD3_RANDOM] Random Cloud III"
echo
read -rp "Select sink: " choice

if [[ "$choice" == "0" ]]; then
    echo "Keeping current sink."
else
    if [[ ! "$choice" =~ ^[0-9]+$ ]]; then
        echo "Invalid selection."
        exit 1
    fi

    sink=""
    if [[ "$choice" == "$EARPOD_RANDOM" ]]; then
        mapfile -t RANDOM_CANDIDATES < <(
            printf '%s\n' "${SINKS[@]}" | cut -d'|' -f1 | grep -E '^(earpods.*|alsa_output.*Headphones.*)$' || true
        )
        if (( ${#RANDOM_CANDIDATES[@]} == 0 )); then
            echo "No EarPods sinks are currently available."
            exit 1
        fi
        sink="$(printf '%s\n' "${RANDOM_CANDIDATES[@]}" | shuf -n 1)"
        echo "Random EarPods sink selected."
        RANDOM_PICK=1
    elif [[ "$choice" == "$CLOUD3_RANDOM" ]]; then
        mapfile -t RANDOM_CANDIDATES < <(
            printf '%s\n' "${SINKS[@]}" | cut -d'|' -f1 | grep -E '^(cloud3.*|alsa_output.*HyperX_Cloud_III.*)$' || true
        )
        if (( ${#RANDOM_CANDIDATES[@]} == 0 )); then
            echo "No Cloud III sinks are currently available."
            exit 1
        fi
        sink="$(printf '%s\n' "${RANDOM_CANDIDATES[@]}" | shuf -n 1)"
        echo "Random Cloud III sink selected."
        RANDOM_PICK=1
    else
        index=$((choice - 1))
        if (( index < 0 || index >= ${#SINKS[@]} )); then
            echo "Invalid selection."
            exit 1
        fi
        sink="${SINKS[$index]%%|*}"
    fi

    if ! pactl list short sinks | awk '{print $2}' | grep -Fqx "$sink"; then
        echo "Selected sink is no longer available: $sink"
        exit 1
    fi

    if ! pactl set-default-sink "$sink" >>"$ACTION_LOG" 2>&1; then
        echo "Failed to set default sink '$sink'."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    while read -r id; do
        [[ -z "$id" ]] && continue
        pactl move-sink-input "$id" "$sink" >>"$ACTION_LOG" 2>&1 || true
    done < <(pactl list sink-inputs short | awk '{print $1}')

    SWITCH_OK=0
    for _ in {1..50}; do
        if [[ "$(pactl get-default-sink 2>/dev/null || true)" == "$sink" ]]; then
            SWITCH_OK=1
            break
        fi
        sleep 0.1
    done

    if (( SWITCH_OK )); then
        normalize_audio_volumes "$ORIGINAL_VOLUME" "$sink"
    else
        echo "Warning: failed to switch to sink '$sink'." >>"$ACTION_LOG"
        echo "Warning: sink switch did not settle. See: $ACTION_LOG"
    fi
fi

###############################################################################
# FINAL STATUS
###############################################################################

echo
if (( RANDOM_PICK )); then
    echo "The sink is a secret!"
else
    echo "Current sink:"
    pactl get-default-sink 2>/dev/null || true
fi

echo
echo "Current device:"
echo "$SELECTED_CARD_LABEL"

echo
echo "Current profile:"
if [[ -f "$PROFILE_SELECTION_FILE" ]]; then
    cat "$PROFILE_SELECTION_FILE"
elif [[ -n "$SELECTED_PROFILE_LABEL" ]]; then
    echo "$SELECTED_PROFILE_LABEL"
else
    echo "$SELECTED_PROFILE"
fi

    pause_before_close
exit 0
