#!/usr/bin/env bash
set -u

HOME_DIR="/home/joel"
AIRPLAY_CONFIG_DIR="/home/joel/Documents/prefs/audio/airplay"
AUDIO_STACK_DIR="/run/user/1000/joel-airplay-stack"
RUNTIME_CONFIG_DIR="/run/user/1000/joel-airplay-stack/configs"
PERSIST_DIR="/home/joel/.local/state/audio-suspend-toggle"

LAST_CONTROL_FILE="$PERSIST_DIR/last-airplay-control"
ACTIVE_CONTROL_FILE="$AUDIO_STACK_DIR/active-control"
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

SHAIRPORT_CONFIG="$AIRPLAY_CONFIG_DIR/shairport-sync.conf"
SHAIRPORT_PID_FILE="$AUDIO_STACK_DIR/shairport-sync.pid"
SHAIRPORT_LOG="$AUDIO_STACK_DIR/shairport-sync.log"
NQPTP_LOG="$AUDIO_STACK_DIR/nqptp.log"

HOTSPOT_CONNECTION="AirPlay Direct"

mkdir -p \
    "$AUDIO_STACK_DIR" \
    "$RUNTIME_CONFIG_DIR" \
    "$PERSIST_DIR"

chmod 700 "$PERSIST_DIR" 2>/dev/null || true


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

###############################################################################
# GENERATE MACHINE-LOCAL CAMILLADSP CONFIGS
###############################################################################

playback_device_metadata() {
    local card_number="$1"
    local info_file="$2"
    local card_id
    local card_longname
    local pcm_name
    local pcm_id
    local sys_path
    local parent
    local usb_product=""
    local usb_manufacturer=""
    local usb_vendor=""
    local usb_product_id=""
    local udev_properties=""

    card_id="$(
        cat "/proc/asound/card${card_number}/id" \
            2>/dev/null || true
    )"

    card_longname="$(
        cat "/proc/asound/card${card_number}/longname" \
            2>/dev/null || true
    )"

    pcm_name="$(
        sed -n 's/^name:[[:space:]]*//p' "$info_file" |
        head -n 1
    )"

    pcm_id="$(
        sed -n 's/^id:[[:space:]]*//p' "$info_file" |
        head -n 1
    )"

    sys_path="$(
        readlink -f \
            "/sys/class/sound/card${card_number}/device" \
            2>/dev/null || true
    )"

    parent="$sys_path"

    while [[ -n "$parent" && "$parent" != "/" ]]; do
        if [[ -r "$parent/idVendor" &&
              -r "$parent/idProduct" ]]; then

            usb_vendor="$(
                cat "$parent/idVendor" \
                    2>/dev/null || true
            )"

            usb_product_id="$(
                cat "$parent/idProduct" \
                    2>/dev/null || true
            )"

            usb_product="$(
                cat "$parent/product" \
                    2>/dev/null || true
            )"

            usb_manufacturer="$(
                cat "$parent/manufacturer" \
                    2>/dev/null || true
            )"

            break
        fi

        parent="${parent%/*}"
        [[ -n "$parent" ]] || parent="/"
    done

    if command -v udevadm >/dev/null 2>&1; then
        udev_properties="$(
            udevadm info \
                --query=property \
                --path="/sys/class/sound/card${card_number}" \
                2>/dev/null || true
        )"
    fi

    printf '%s\n' \
        "$card_id $card_longname $pcm_id $pcm_name $sys_path $usb_product $usb_manufacturer $usb_vendor $usb_product_id $udev_properties"
}

find_playback_device() {
    local wanted_type="$1"
    local info_file
    local pcm_dir
    local card_dir
    local card_number
    local device_number
    local metadata
    local metadata_lower
    local sys_path
    local parent
    local usb_device=0

    for info_file in /proc/asound/card[0-9]*/pcm[0-9]*p/info; do
        [[ -r "$info_file" ]] || continue

        pcm_dir="${info_file%/info}"
        card_dir="${pcm_dir%/*}"

        card_number="${card_dir##*/card}"
        device_number="${pcm_dir##*/pcm}"
        device_number="${device_number%p}"

        [[ "$card_number" =~ ^[0-9]+$ ]] || continue
        [[ "$device_number" =~ ^[0-9]+$ ]] || continue

        metadata="$(
            playback_device_metadata \
                "$card_number" \
                "$info_file"
        )"

        metadata_lower="${metadata,,}"

        sys_path="$(
            readlink -f \
                "/sys/class/sound/card${card_number}/device" \
                2>/dev/null || true
        )"

        usb_device=0
        parent="$sys_path"

        while [[ -n "$parent" && "$parent" != "/" ]]; do
            if [[ -r "$parent/idVendor" &&
                  -r "$parent/idProduct" ]]; then
                usb_device=1
                break
            fi

            parent="${parent%/*}"
            [[ -n "$parent" ]] || parent="/"
        done

        printf 'candidate hw:%s,%s usb=%s metadata=%q\n' \
            "$card_number" \
            "$device_number" \
            "$usb_device" \
            "$metadata" \
            >>"$ACTION_LOG"

        # ALSA Loopback is an intentional capture transport, but it must
        # never be selected as a physical playback destination.
        if [[ "$metadata_lower" == *loopback* ||
              "$sys_path" == *"/virtual/"* ]]; then
            continue
        fi

        case "$wanted_type" in
            hyperx)
                # Strong explicit match.
                if [[ "$metadata_lower" == *hyperx* ||
                      "$metadata_lower" == *"cloud iii"* ||
                      "$metadata_lower" == *cloud_iii* ]]; then

                    printf 'hw:%s,%s\n' \
                        "$card_number" \
                        "$device_number"

                    return 0
                fi

                # Appliance fallback: the intended non-SOF USB playback
                # endpoint is the HyperX USB DAC.
                if (( usb_device )) &&
                   [[ "$metadata_lower" != *sof* &&
                      "$metadata_lower" != *pch* &&
                      "$metadata_lower" != *hdmi* &&
                      "$metadata_lower" != *displayport* ]]; then

                    printf 'hw:%s,%s\n' \
                        "$card_number" \
                        "$device_number"

                    return 0
                fi
                ;;

            builtin)
                (( usb_device == 0 )) || continue

                if [[ "$metadata_lower" == *hdmi* ||
                      "$metadata_lower" == *displayport* ]]; then
                    continue
                fi

                if [[ "$metadata_lower" == *sof* ||
                      "$metadata_lower" == *pch* ||
                      "$metadata_lower" == *hda* ||
                      "$metadata_lower" == *analog* ||
                      "$metadata_lower" == *headphone* ||
                      "$metadata_lower" == *speaker* ]]; then

                    printf 'hw:%s,%s\n' \
                        "$card_number" \
                        "$device_number"

                    return 0
                fi
                ;;

            *)
                return 1
                ;;
        esac
    done

    return 1
}

dump_playback_devices() {
    local info_file
    local pcm_dir
    local card_dir
    local card_number
    local device_number
    local metadata

    echo "ALSA playback-device inventory:" \
        >>"$ACTION_LOG"

    for info_file in /proc/asound/card[0-9]*/pcm[0-9]*p/info; do
        [[ -r "$info_file" ]] || continue

        pcm_dir="${info_file%/info}"
        card_dir="${pcm_dir%/*}"

        card_number="${card_dir##*/card}"
        device_number="${pcm_dir##*/pcm}"
        device_number="${device_number%p}"

        metadata="$(
            playback_device_metadata \
                "$card_number" \
                "$info_file"
        )"

        printf '  hw:%s,%s metadata=%q\n' \
            "$card_number" \
            "$device_number" \
            "$metadata" \
            >>"$ACTION_LOG"
    done

    echo "aplay -l output:" \
        >>"$ACTION_LOG"

    aplay -l \
        >>"$ACTION_LOG" 2>&1 ||
        true
}

generate_camilla_config() {
    local template="$1"
    local destination="$2"
    local playback_device="$3"
    local temporary

    rm -f \
        "$destination" \
        "${destination}.tmp"

    if [[ ! -f "$template" ]]; then
        echo "Missing CamillaDSP template: $template" \
            >>"$ACTION_LOG"
        return 1
    fi

    if [[ -z "$playback_device" ]]; then
        echo "No suitable playback device was detected for: $template" \
            >>"$ACTION_LOG"
        return 1
    fi

    temporary="${destination}.tmp"

    if ! sed \
        -e "s|__HOME__|$HOME_DIR|g" \
        -e "s|__USB_PLAYBACK_DEVICE__|$playback_device|g" \
        -e "s|__BUILTIN_PLAYBACK_DEVICE__|$playback_device|g" \
        "$template" \
        >"$temporary"; then

        echo "Failed to render CamillaDSP template: $template" \
            >>"$ACTION_LOG"

        rm -f "$temporary"
        return 1
    fi

    if grep -qE \
        '__HOME__|__USB_PLAYBACK_DEVICE__|__BUILTIN_PLAYBACK_DEVICE__' \
        "$temporary"; then

        echo "Unresolved placeholder in generated config: $temporary" \
            >>"$ACTION_LOG"

        rm -f "$temporary"
        return 1
    fi

    if ! camilladsp -c "$temporary" \
        >>"$ACTION_LOG" 2>&1; then

        echo "CamillaDSP rejected generated config: $temporary" \
            >>"$ACTION_LOG"

        rm -f "$temporary"
        return 1
    fi

    mv -f \
        "$temporary" \
        "$destination"
}

rm -f \
    "$HYPERX_CLOUD3_CONFIG" \
    "$HYPERX_EARPODS_CONFIG" \
    "$BUILTIN_CLOUD3_CONFIG" \
    "$BUILTIN_EARPODS_CONFIG"

USB_PLAYBACK_DEVICE="$(
    find_playback_device hyperx || true
)"

BUILTIN_PLAYBACK_DEVICE="$(
    find_playback_device builtin || true
)"

printf 'Detected HyperX playback device: %s\n' \
    "${USB_PLAYBACK_DEVICE:-NONE}" \
    >>"$ACTION_LOG"

printf 'Detected built-in playback device: %s\n' \
    "${BUILTIN_PLAYBACK_DEVICE:-NONE}" \
    >>"$ACTION_LOG"

if [[ -z "$USB_PLAYBACK_DEVICE" ||
      -z "$BUILTIN_PLAYBACK_DEVICE" ]]; then
    dump_playback_devices
fi

HYPERX_CONFIGS_READY=0
BUILTIN_CONFIGS_READY=0

if [[ -n "$USB_PLAYBACK_DEVICE" ]]; then
    if generate_camilla_config \
           "$HYPERX_CLOUD3_TEMPLATE" \
           "$HYPERX_CLOUD3_CONFIG" \
           "$USB_PLAYBACK_DEVICE" &&
       generate_camilla_config \
           "$HYPERX_EARPODS_TEMPLATE" \
           "$HYPERX_EARPODS_CONFIG" \
           "$USB_PLAYBACK_DEVICE"; then

        HYPERX_CONFIGS_READY=1
    fi
fi

if [[ -n "$BUILTIN_PLAYBACK_DEVICE" ]]; then
    if generate_camilla_config \
           "$BUILTIN_CLOUD3_TEMPLATE" \
           "$BUILTIN_CLOUD3_CONFIG" \
           "$BUILTIN_PLAYBACK_DEVICE" &&
       generate_camilla_config \
           "$BUILTIN_EARPODS_TEMPLATE" \
           "$BUILTIN_EARPODS_CONFIG" \
           "$BUILTIN_PLAYBACK_DEVICE"; then

        BUILTIN_CONFIGS_READY=1
    fi
fi

printf 'HyperX configs ready: %s\n' \
    "$HYPERX_CONFIGS_READY" \
    >>"$ACTION_LOG"

printf 'Built-in configs ready: %s\n' \
    "$BUILTIN_CONFIGS_READY" \
    >>"$ACTION_LOG"

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
    "$ACTIVE_CONTROL_FILE" \
    "$SHAIRPORT_PID_FILE" \
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

airplay_name_for() {
    local slot="$1"
    local profile="$2"

    case "$slot:$profile" in
        hyperx:cloud3)
            printf '%s\n' "Joel Laptop - HyperX Cloud III"
            ;;
        hyperx:earpods)
            printf '%s\n' "Joel Laptop - HyperX EarPods"
            ;;
        builtin:cloud3)
            printf '%s\n' "Joel Laptop - Built-in Cloud III"
            ;;
        builtin:earpods)
            printf '%s\n' "Joel Laptop - Built-in EarPods"
            ;;
        *)
            printf '%s\n' "Joel Laptop"
            ;;
    esac
}

active_airplay_name() {
    local active=""
    local slot=""
    local profile=""

    if active="$(get_active_airplay_control 2>/dev/null)"; then
        read -r slot profile <<<"$active"
        airplay_name_for "$slot" "$profile"
    else
        printf '%s\n' "Joel Laptop"
    fi
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

get_active_airplay_control() {
    local slot="" profile="" temporary=""

    if [[ -r "$ACTIVE_CONTROL_FILE" ]]; then
        read -r slot profile <"$ACTIVE_CONTROL_FILE" || true
        case "$slot:$profile" in
            hyperx:cloud3|hyperx:earpods)
                pid_file_running "$HYPERX_PID_FILE" && { printf '%s %s\n' "$slot" "$profile"; return 0; }
                ;;
            builtin:cloud3|builtin:earpods)
                pid_file_running "$BUILTIN_PID_FILE" && { printf '%s %s\n' "$slot" "$profile"; return 0; }
                ;;
        esac
        rm -f "$ACTIVE_CONTROL_FILE"
    fi

    if pid_file_running "$HYPERX_PID_FILE"; then
        profile="$(read_state_file "$HYPERX_STATE_FILE")"
        case "$profile" in
            cloud3|earpods)
                temporary="${ACTIVE_CONTROL_FILE}.tmp.$$"
                printf 'hyperx %s\n' "$profile" >"$temporary"
                mv -f "$temporary" "$ACTIVE_CONTROL_FILE"
                printf 'hyperx %s\n' "$profile"
                return 0
                ;;
        esac
    fi

    if pid_file_running "$BUILTIN_PID_FILE"; then
        profile="$(read_state_file "$BUILTIN_STATE_FILE")"
        case "$profile" in
            cloud3|earpods)
                temporary="${ACTIVE_CONTROL_FILE}.tmp.$$"
                printf 'builtin %s\n' "$profile" >"$temporary"
                mv -f "$temporary" "$ACTIVE_CONTROL_FILE"
                printf 'builtin %s\n' "$profile"
                return 0
                ;;
        esac
    fi
    return 1
}

if ! pid_file_running "$HYPERX_PID_FILE"; then
    rm -f "$HYPERX_STATE_FILE"
fi

if ! pid_file_running "$BUILTIN_PID_FILE"; then
    rm -f "$BUILTIN_STATE_FILE"
fi

ACTIVE_AIRPLAY_CONTROL=""
ACTIVE_AIRPLAY_SLOT=""
ACTIVE_AIRPLAY_PROFILE=""

if ACTIVE_AIRPLAY_CONTROL="$(get_active_airplay_control)"; then
    read -r ACTIVE_AIRPLAY_SLOT ACTIVE_AIRPLAY_PROFILE <<<"$ACTIVE_AIRPLAY_CONTROL"
    case "$ACTIVE_AIRPLAY_SLOT:$ACTIVE_AIRPLAY_PROFILE" in
        hyperx:cloud3) AIRPLAY_CONTROLS_LABEL="AirPlay Controls [active: HyperX Cloud III]" ;;
        hyperx:earpods) AIRPLAY_CONTROLS_LABEL="AirPlay Controls [active: HyperX EarPods]" ;;
        builtin:cloud3) AIRPLAY_CONTROLS_LABEL="AirPlay Controls [active: Built-in Cloud III]" ;;
        builtin:earpods) AIRPLAY_CONTROLS_LABEL="AirPlay Controls [active: Built-in EarPods]" ;;
    esac
elif shairport_running || nqptp_running; then
    AIRPLAY_CONTROLS_LABEL="AirPlay Controls [partial active]"
else
    AIRPLAY_CONTROLS_LABEL="AirPlay Controls [inactive]"
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
    HYPERX_CLOUD_LABEL="HyperX: Cloud III [inactive]"
    HYPERX_EARPODS_LABEL="HyperX: EarPods [inactive]"
    BUILTIN_CLOUD_LABEL="Built-in: Cloud III [inactive]"
    BUILTIN_EARPODS_LABEL="Built-in: EarPods [inactive]"
    ACTIVE_AIRPLAY_CONTROL=""
    ACTIVE_AIRPLAY_SLOT=""
    ACTIVE_AIRPLAY_PROFILE=""

    if ACTIVE_AIRPLAY_CONTROL="$(get_active_airplay_control)"; then
        read -r ACTIVE_AIRPLAY_SLOT ACTIVE_AIRPLAY_PROFILE <<<"$ACTIVE_AIRPLAY_CONTROL"
        case "$ACTIVE_AIRPLAY_SLOT:$ACTIVE_AIRPLAY_PROFILE" in
            hyperx:cloud3) HYPERX_CLOUD_LABEL="HyperX: Cloud III [active, select to stop]" ;;
            hyperx:earpods) HYPERX_EARPODS_LABEL="HyperX: EarPods [active, select to stop]" ;;
            builtin:cloud3) BUILTIN_CLOUD_LABEL="Built-in: Cloud III [active, select to stop]" ;;
            builtin:earpods) BUILTIN_EARPODS_LABEL="Built-in: EarPods [active, select to stop]" ;;
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
    case "$ACTIVE_AIRPLAY_SLOT:$ACTIVE_AIRPLAY_PROFILE" in
        hyperx:cloud3) echo "HyperX:     Cloud III"; echo "Built-in:   stopped" ;;
        hyperx:earpods) echo "HyperX:     EarPods"; echo "Built-in:   stopped" ;;
        builtin:cloud3) echo "HyperX:     stopped"; echo "Built-in:   Cloud III" ;;
        builtin:earpods) echo "HyperX:     stopped"; echo "Built-in:   EarPods" ;;
        *) echo "HyperX:     stopped"; echo "Built-in:   stopped" ;;
    esac

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

case "$SELECTED_CARD_VALUE" in
    dual-hyperx-*)
        if (( ! HYPERX_CONFIGS_READY )); then
            echo
            echo "HyperX AirPlay output is unavailable."
            echo
            echo "No usable HyperX runtime configurations were generated."
            echo
            cat "$ACTION_LOG"
            exit 1
        fi
        ;;

    dual-builtin-*)
        if (( ! BUILTIN_CONFIGS_READY )); then
            echo
            echo "Built-in AirPlay output is unavailable."
            echo
            echo "No usable built-in runtime configurations were generated."
            echo
            cat "$ACTION_LOG"
            exit 1
        fi
        ;;
esac

if [[ "$SELECTED_CARD_VALUE" == "restart-airplay-stream" ]]; then
    SHAIRPORT_CONFIG="/home/joel/Documents/prefs/audio/airplay/shairport-sync.conf"
    AIRPLAY_NAME="$(active_airplay_name)"

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

    rm -f "$SHAIRPORT_PID_FILE"
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
        -a "$AIRPLAY_NAME" \
        -c "$SHAIRPORT_CONFIG" \
        -vv \
        >>"$SHAIRPORT_LOG" 2>&1 &

    shairport_pid=$!

    printf '%s\n' "$shairport_pid" \
        >"$SHAIRPORT_PID_FILE"

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

        rm -f "$SHAIRPORT_PID_FILE"

    pause_before_close
        exit 1
    fi

    echo
    echo "AirPlay receiver restarted."
    echo "Reconnect $AIRPLAY_NAME on the sender."
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

    AIRPLAY_NAME="$(airplay_name_for "$SLOT" "$PROFILE")"

    : >"$LOG_FILE"

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

        rm -f "$ACTIVE_CONTROL_FILE"
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

        rm -f "$SHAIRPORT_PID_FILE"
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
                -a "$AIRPLAY_NAME" \
                -c "$SHAIRPORT_CONFIG" \
                -vv \
                >>"$SHAIRPORT_LOG" 2>&1 &

            shairport_pid=$!

            printf '%s\n' "$shairport_pid" \
                >"$SHAIRPORT_PID_FILE"

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
        local temporary_active_control

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

        temporary_active_control="${ACTIVE_CONTROL_FILE}.tmp.$$"
        printf '%s %s\n' "$SLOT" "$profile" >"$temporary_active_control"
        mv -f "$temporary_active_control" "$ACTIVE_CONTROL_FILE"

        umask 077
        printf '%s %s\n' "$SLOT" "$profile" >"$LAST_CONTROL_FILE"
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
        else
            echo
            echo "$OUTPUT_LABEL CamillaDSP did not start."
            echo "No new CamillaDSP runtime log was produced."
        fi

        if [[ -s "$ACTION_LOG" ]]; then
            echo
            echo "Action log:"
            cat "$ACTION_LOG"
        fi

        exit 1
    fi

    # The selected slot/profile determines the advertised receiver name.
    # Restart the single Shairport instance so mDNS publishes the new name.
    # NQPTP is deliberately left running across profile/output switches.
    stop_all_shairport
    rm -f "$SHAIRPORT_PID_FILE"

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
    echo "AirPlay name: $AIRPLAY_NAME"
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
