#!/usr/bin/env bash
set -u

HOME_DIR="/home/joel"
STATE_DIR="$HOME_DIR/.local/state/sway/audio"
ACTION_LOG="$STATE_DIR/audio-switch.log"
HOTSPOT_CONNECTION="AirPlay Direct"
RESULT_FILE="$STATE_DIR/audio-toggle-complete.$$"
ACTION_STARTED_FILE="$STATE_DIR/audio-toggle-started.$$"
CARD_SELECTION_FILE="$STATE_DIR/audio-card-selected.$$"
PROFILE_SELECTION_FILE="$STATE_DIR/audio-profile-selected.$$"
SWAYNAG_LOG="$STATE_DIR/audio-switch-swaynag.$$"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
printf '\n[%s] audio-switch pid=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$$" >>"$ACTION_LOG"
FINAL_PAUSE_REACHED=0
EXIT_HANDLER_RUNNING=0

cleanup() { rm -f "$RESULT_FILE" "$ACTION_STARTED_FILE" "$CARD_SELECTION_FILE" "$PROFILE_SELECTION_FILE" "$SWAYNAG_LOG"; }
pause_before_close() {
    FINAL_PAUSE_REACHED=1
    echo
    if [[ -r /dev/tty && -w /dev/tty ]]; then
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
    if (( status != 0 && ! FINAL_PAUSE_REACHED )); then
        echo; echo "Audio script exited unexpectedly."; echo "Exit status: $status"
        [[ -s "$ACTION_LOG" ]] && { echo; echo "Action log:"; echo "$ACTION_LOG"; echo; cat "$ACTION_LOG"; }
        pause_before_close
    fi
    cleanup
    builtin exit "$status"
}
trap handle_script_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap 'exit 131' QUIT

pulse_ready() {
    pactl info >/dev/null 2>&1
}

wait_for_pulse() {
    local _
    for _ in {1..100}; do
        pulse_ready && return 0
        sleep 0.1
    done
    return 1
}

sink_exists() {
    local wanted="$1"
    pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fqx "$wanted"
}

wait_for_sink() {
    local wanted="$1" _
    for _ in {1..100}; do
        sink_exists "$wanted" && return 0
        sleep 0.1
    done
    return 1
}

set_card_profile_stable() {
    local card="$1" profile="$2" _
    for _ in {1..30}; do
        wait_for_pulse || continue
        if pactl set-card-profile "$card" "$profile" >>"$ACTION_LOG" 2>&1; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

is_custom_sink() {
    case "$1" in
        earpods_*|cloud3_*) return 0 ;;
        *) return 1 ;;
    esac
}

selected_filter_output_node() {
    local custom_sink="$1"

    pw-dump 2>/dev/null |
    jq -r --arg sink "$custom_sink" '
        [
            .[] |
            select(.type == "PipeWire:Interface:Node") |
            {
                name: .info.props."node.name",
                class: .info.props."media.class",
                group: .info.props."node.link-group"
            }
        ] as $nodes |
        ($nodes[] |
            select(.name == $sink and .class == "Audio/Sink") |
            .group
        ) as $group |
        $nodes[] |
        select(
            .class == "Stream/Output/Audio" and
            .group == $group and
            (.name | startswith("output.filter-chain-"))
        ) |
        .name
    ' |
    head -n1
}

filter_link_ids() {
    pw-dump 2>/dev/null |
    jq -r '
        [
            .[] |
            select(.type == "PipeWire:Interface:Node") |
            .info.props as $p |
            select(
                $p."media.class" == "Stream/Output/Audio" and
                (($p."node.name" // "") | startswith("output.filter-chain-"))
            ) |
            .id
        ] as $filter_ids |
        .[] |
        select(.type == "PipeWire:Interface:Link") |
        select(.info."output-node-id" as $id | $filter_ids | index($id)) |
        .id
    '
}

disconnect_all_filter_outputs() {
    local link_id
    local _
    local -a links=()

    for _ in {1..20}; do
        mapfile -t links < <(filter_link_ids)
        ((${#links[@]} == 0)) && return 0

        for link_id in "${links[@]}"; do
            [[ -n "$link_id" ]] || continue
            pw-link -d "$link_id" >>"$ACTION_LOG" 2>&1 || true
        done
        sleep 0.1
    done

    mapfile -t links < <(filter_link_ids)
    if ((${#links[@]} != 0)); then
        printf 'Warning: %s filter links remained after cleanup.\n' \
            "${#links[@]}" >>"$ACTION_LOG"
        return 1
    fi
    return 0
}

move_application_inputs_to() {
    local wanted="$1"
    local id
    local block

    while read -r id _; do
        [[ -n "$id" ]] || continue
        block="$(pactl list sink-inputs 2>/dev/null | awk -v wanted="$id" '
            /^Sink Input #[0-9]+/ {
                current=$3
                sub(/^#/, "", current)
                selected=(current==wanted)
            }
            selected { print }
        ')"
        grep -Eq 'node.name = "output\.filter-chain-|media.name = ".* output"' \
            <<<"$block" && continue
        pactl move-sink-input "$id" "$wanted" \
            >>"$ACTION_LOG" 2>&1 || true
    done < <(pactl list short sink-inputs 2>/dev/null || true)
}

selected_filter_link_count() {
    local output_node="$1"
    local physical_sink="$2"

    pw-dump 2>/dev/null |
    jq -r --arg out "$output_node" --arg input "$physical_sink" '
        [
            .[] |
            select(.type == "PipeWire:Interface:Node") |
            {id: .id, name: .info.props."node.name"}
        ] as $nodes |
        ($nodes[] | select(.name == $out) | .id) as $out_id |
        ($nodes[] | select(.name == $input) | .id) as $in_id |
        [
            .[] |
            select(.type == "PipeWire:Interface:Link") |
            select(
                .info."output-node-id" == $out_id and
                .info."input-node-id" == $in_id
            )
        ] |
        length
    '
}

custom_sink_description() {
    local wanted="$1"

    pactl list sinks 2>/dev/null |
    awk -v wanted="$wanted" '
        /^[[:space:]]*Name:/ {
            name = $0
            sub(/^[[:space:]]*Name:[[:space:]]*/, "", name)
            selected = (name == wanted)
            next
        }
        selected && /^[[:space:]]*Description:/ {
            value = $0
            sub(/^[[:space:]]*Description:[[:space:]]*/, "", value)
            print value
            exit
        }
    '
}

sink_is_headphones() {
    local sink="$1"
    local description

    description="$(custom_sink_description "$sink")"

    [[ "$sink" == *Headphones* || "$description" == *Headphones* ]]
}

sink_is_excluded_physical_target() {
    local sink="$1"
    local description

    description="$(custom_sink_description "$sink")"

    is_custom_sink "$sink" && return 0

    [[ "$sink" == *Speaker* || "$description" == *Speaker* ]] && return 0
    [[ "$sink" == *HDMI* || "$sink" == *hdmi* ||
       "$description" == *HDMI* || "$description" == *DisplayPort* ]] && return 0

    [[ "$sink" == *snd_aloop* || "$sink" == *Loopback* ||
       "$description" == *Loopback* ]] && return 0

    return 1
}

preferred_physical_sink() {
    local sink
    local -a candidates=()

    while read -r sink; do
        [[ -n "$sink" ]] || continue
        if sink_is_headphones "$sink"; then
            printf '%s\n' "$sink"
            return 0
        fi
    done < <(
        pactl list short sinks 2>/dev/null |
        awk '{print $2}'
    )

    while read -r sink; do
        [[ -n "$sink" ]] || continue

        [[ "$sink" == alsa_output.* ]] || continue
        sink_is_excluded_physical_target "$sink" && continue

        candidates+=("$sink")
    done < <(
        pactl list short sinks 2>/dev/null |
        awk '{print $2}'
    )

    if ((${#candidates[@]} == 1)); then
        printf '%s\n' "${candidates[0]}"
        return 0
    fi

    sink="$(pactl get-default-sink 2>/dev/null || true)"
    if [[ -n "$sink" ]]; then
        local candidate
        for candidate in "${candidates[@]}"; do
            if [[ "$candidate" == "$sink" ]]; then
                printf '%s\n' "$candidate"
                return 0
            fi
        done
    fi

    if ((${#candidates[@]} > 0)); then
        printf '%s\n' "${candidates[0]}"
        return 0
    fi

    return 1
}

route_selected_custom_output() {
    local custom_sink="$1"
    local physical_sink="$2"
    local output_node=""
    local link_count
    local total_filter_links
    local _

    for _ in {1..50}; do
        output_node="$(selected_filter_output_node "$custom_sink" || true)"
        [[ -n "$output_node" ]] && break
        sleep 0.1
    done

    if [[ -z "$output_node" ]]; then
        printf 'Warning: could not resolve filter output for %s.\n' \
            "$custom_sink" >>"$ACTION_LOG"
        return 1
    fi

    disconnect_all_filter_outputs || return 1

    if ! pw-link "$output_node:output_FL" "$physical_sink:playback_FL" \
        >>"$ACTION_LOG" 2>&1; then
        printf 'Warning: failed FL link: %s -> %s\n' \
            "$output_node" "$physical_sink" >>"$ACTION_LOG"
        return 1
    fi

    if ! pw-link "$output_node:output_FR" "$physical_sink:playback_FR" \
        >>"$ACTION_LOG" 2>&1; then
        pw-link -d "$output_node:output_FL" "$physical_sink:playback_FL" \
            >>"$ACTION_LOG" 2>&1 || true
        printf 'Warning: failed FR link: %s -> %s\n' \
            "$output_node" "$physical_sink" >>"$ACTION_LOG"
        return 1
    fi

    for _ in {1..30}; do
        link_count="$(selected_filter_link_count "$output_node" "$physical_sink" || true)"
        total_filter_links="$(filter_link_ids | wc -l)"

        if [[ "$link_count" == "2" && "$total_filter_links" -eq 2 ]]; then
            printf 'Custom route settled: %s (%s) -> %s\n' \
                "$custom_sink" "$output_node" "$physical_sink" \
                >>"$ACTION_LOG"
            return 0
        fi
        sleep 0.1
    done

    printf 'Warning: custom route did not settle: %s (%s) -> %s; selected_links=%s total_filter_links=%s\n' \
        "$custom_sink" "$output_node" "$physical_sink" \
        "${link_count:-unknown}" "${total_filter_links:-unknown}" \
        >>"$ACTION_LOG"
    return 1
}

set_default_sink_stable() {
    local wanted="$1"
    local current
    local physical_sink=""
    local id
    local _

    wait_for_pulse || return 1
    wait_for_sink "$wanted" || return 1

    if is_custom_sink "$wanted"; then
        physical_sink="$(preferred_physical_sink || true)"
        if [[ -z "$physical_sink" ]]; then
            echo "No eligible Headphones or extra physical sink is available." \
                >>"$ACTION_LOG"
            return 1
        fi
    fi

    for _ in {1..50}; do
        if pulse_ready && sink_exists "$wanted"; then
            if pactl set-default-sink "$wanted" \
                >>"$ACTION_LOG" 2>&1; then
                current="$(pactl get-default-sink 2>/dev/null || true)"
                [[ "$current" == "$wanted" ]] && break
            fi
        fi
        sleep 0.1
    done

    [[ "$(pactl get-default-sink 2>/dev/null || true)" == "$wanted" ]] || return 1

    if is_custom_sink "$wanted"; then
        move_application_inputs_to "$wanted"

        route_selected_custom_output "$wanted" "$physical_sink" || return 1
    else
        move_application_inputs_to "$wanted"
        disconnect_all_filter_outputs || return 1
    fi

    return 0
}

pause_active_media() {
    local service
    local paused_any=0

    if [[ -S /tmp/mpvsocket ]] && command -v socat >/dev/null 2>&1; then
        printf 'set pause yes\n' |
            socat - /tmp/mpvsocket >>"$ACTION_LOG" 2>&1 || true
    fi

    if command -v busctl >/dev/null 2>&1; then
        while read -r service; do
            [[ -n "$service" ]] || continue

            if busctl --user call \
                "$service" \
                /org/mpris/MediaPlayer2 \
                org.mpris.MediaPlayer2.Player \
                Pause \
                >>"$ACTION_LOG" 2>&1; then
                printf 'Paused MPRIS player: %s\n' "$service" >>"$ACTION_LOG"
                paused_any=1
            else
                printf 'Failed to pause MPRIS player: %s\n' "$service" >>"$ACTION_LOG"
            fi
        done < <(
            busctl --user --no-pager --no-legend list 2>/dev/null |
            awk '$1 ~ /^org\.mpris\.MediaPlayer2\./ {print $1}'
        )
    fi
}

normalize_audio_volumes() {
    local restore_volume="${1:-}" restore_sink="${2:-}" sof_card hyperx_card sink
    pause_active_media
    sof_card="$(aplay -l 2>/dev/null | awk -F': ' '/sof|SOF/ {print $1; exit}' | grep -o '[0-9]\+' || true)"
    [[ -n "$sof_card" ]] && amixer -c "$sof_card" sset Headphone 100% >/dev/null 2>&1 || true
    hyperx_card="$(aplay -l 2>/dev/null | awk -F': ' '/HyperX Cloud III/ {print $1; exit}' | grep -o '[0-9]\+' || true)"
    [[ -n "$hyperx_card" ]] && amixer -c "$hyperx_card" sset 'Speaker Volume' 100% unmute >/dev/null 2>&1 || true
    while read -r sink; do
        [[ -n "$sink" ]] || continue
        pactl set-sink-mute "$sink" 0 >/dev/null 2>&1 || true
        pactl set-sink-volume "$sink" 100% >/dev/null 2>&1 || true
    done < <(pactl list short sinks 2>/dev/null | awk '{print $2}')
    if [[ -n "$restore_volume" && -n "$restore_sink" ]] &&
       pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fqx "$restore_sink"; then
        pactl set-sink-volume "$restore_sink" "$restore_volume" >/dev/null 2>&1 || true
    fi
}
export -f pause_active_media normalize_audio_volumes

ORIGINAL_SINK="$(pactl get-default-sink 2>/dev/null || true)"
ORIGINAL_VOLUME=""
if [[ -n "$ORIGINAL_SINK" ]] && pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fqx "$ORIGINAL_SINK"; then
    ORIGINAL_VOLUME="$(pactl get-sink-volume "$ORIGINAL_SINK" 2>/dev/null | grep -Po '[0-9]+%' | head -n1)"
fi
normalize_audio_volumes "$ORIGINAL_VOLUME" "$ORIGINAL_SINK"
export RESULT_FILE ACTION_STARTED_FILE ACTION_LOG CARD_SELECTION_FILE PROFILE_SELECTION_FILE ORIGINAL_VOLUME ORIGINAL_SINK

mapfile -t CARDS < <(pactl list cards 2>/dev/null | awk '
function output_card(){if(card!=""){if(description=="")description=card;print card "|" description}}
/^Card #[0-9]+/{output_card();card="";description="";next}
/^[[:space:]]*Name:/{card=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",card);next}
/^[[:space:]]*device\.description[[:space:]]*=/{if(description==""){description=$0;sub(/^[[:space:]]*device\.description[[:space:]]*=[[:space:]]*/,"",description);gsub(/^"|"$/,"",description)}}
END{output_card()}')

card_display_label() {
    local card="$1" description="$2" label="$2"
    case "$card $description" in
        *HyperX_Cloud_III*|*"HyperX Cloud III"*) label="HyperX Cloud III" ;;
        *sof*|*SOF*|*"sof-hda-dsp"*|*"Built-in Audio"*) label="Built-in Audio" ;;
        *HDMI*|*hdmi*) [[ "$description" == *HDMI* ]] || label="$description HDMI" ;;
    esac
    printf '%s\n' "$label"
}
profile_display_label() {
    local card="$1" profile="$2" description="$3" label="$3" hdmi
    if [[ "$description" == *Headphone* ]]; then label="Headphones"
    elif [[ "$description" == *Speaker* ]]; then label="Speaker"
    elif [[ "$description" == *"Pro Audio"* ]]; then label="Pro Audio"
    elif [[ "$description" == *HDMI* ]]; then
        hdmi=1; [[ "$description" == *"HDMI 2"* ]] && hdmi=2; [[ "$description" == *"HDMI 3"* ]] && hdmi=3
        label="HDMI $hdmi"; [[ "$description" == *"7.1"* ]] && label+=" 7.1"; [[ "$description" == *"5.1"* ]] && label+=" 5.1"; [[ "$description" == *Input* ]] && label+=" + Mic"
    elif [[ "$profile" == "input:analog-stereo" ]]; then label="Mic Only"
    elif [[ "$description" == *Analog* ]]; then label="Analog"; [[ "$description" == *Input* || "$description" == *Duplex* ]] && label+=" + Mic"
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
sink_display_label() {
    local sink="$1" label="$1"

    case "$sink" in
        *usb-HP__Inc_HyperX_Cloud_III*.analog-stereo) label="Cloud III Analog" ;;
        *usb-HP__Inc_HyperX_Cloud_III*.iec958-stereo) label="Cloud III Digital" ;;
        *platform-snd_aloop.0.analog-stereo) label="ALSA Loopback" ;;
        *HiFi__Headphones__sink*) label="Headphones" ;;
        *HiFi__Speaker__sink*) label="Speaker" ;;
        *HiFi__HDMI1__sink*) label="HDMI 1" ;;
        *HiFi__HDMI2__sink*) label="HDMI 2" ;;
        *HiFi__HDMI3__sink*) label="HDMI 3" ;;
        *pro-output-[0-9]*) label="${sink##*.}" ;;
        *hdmi*) label="HDMI" ;;
    esac

    printf '%s\n' "$label"
}

close_mpv_windows() {
    local _

    pkill -TERM -x mpv >>"$ACTION_LOG" 2>&1 || true

    for _ in {1..30}; do
        if ! pgrep -x mpv >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.1
    done

    pkill -KILL -x mpv >>"$ACTION_LOG" 2>&1 || true

    for _ in {1..20}; do
        if ! pgrep -x mpv >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.1
    done

    echo "Warning: one or more mpv processes are still running." \
        >>"$ACTION_LOG"

    return 1
}
hotspot_active() { nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fqx "$HOTSPOT_CONNECTION"; }

ARGS=(-t warning -y overlay -m "Audio Device Select")

ARGS+=(
    -z "Audio Start"
    "touch '$ACTION_STARTED_FILE'; printf '%s\n' start-audio >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"

    -z "Audio Stop"
    "touch '$ACTION_STARTED_FILE'; printf '%s\n' stop-audio >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
)

if hotspot_active; then
    ARGS+=(
        -z "Stop AirPlay Hotspot [active]"
        "touch '$ACTION_STARTED_FILE'; printf '%s\n' toggle-hotspot >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
    )
else
    ARGS+=(
        -z "Start AirPlay Hotspot [inactive]"
        "touch '$ACTION_STARTED_FILE'; printf '%s\n' toggle-hotspot >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
    )
fi

for i in "${!CARDS[@]}"; do
    card="${CARDS[$i]%%|*}"; description="${CARDS[$i]#*|}"
    ARGS+=( -z "$(card_display_label "$card" "$description")" "touch '$ACTION_STARTED_FILE'; printf '%s\n' '$i' >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
done

swaynag "${ARGS[@]}" >"$SWAYNAG_LOG" 2>&1 &
SWAYNAG_PID=$!
while [[ ! -f "$RESULT_FILE" ]]; do
    if kill -0 "$SWAYNAG_PID" 2>/dev/null; then sleep 0.1; continue; fi
    wait "$SWAYNAG_PID" 2>/dev/null; SWAYNAG_STATUS=$?
    if [[ -f "$ACTION_STARTED_FILE" ]]; then while [[ ! -f "$RESULT_FILE" ]]; do sleep 0.1; done; break; fi
    if [[ -s "$SWAYNAG_LOG" ]]; then echo; echo "Swaynag exited before completing the action."; echo; cat "$SWAYNAG_LOG"; exit "${SWAYNAG_STATUS:-1}"; fi
    echo; echo "No audio action was selected."; pause_before_close; exit 0
done
kill "$SWAYNAG_PID" 2>/dev/null || true
wait "$SWAYNAG_PID" 2>/dev/null || true
[[ -f "$CARD_SELECTION_FILE" ]] || { echo "No audio action was selected."; exit 1; }
SELECTED_CARD_VALUE="$(cat "$CARD_SELECTION_FILE")"

if [[ "$SELECTED_CARD_VALUE" == start-audio ]]; then
    echo "Starting PipeWire and WirePlumber..." | tee -a "$ACTION_LOG"

    systemctl --user reset-failed pipewire.socket pipewire.service pipewire-pulse.socket pipewire-pulse.service wireplumber.service >>"$ACTION_LOG" 2>&1 || true
    if ! systemctl --user start \
        pipewire.socket \
        pipewire-pulse.socket \
        pipewire.service \
        pipewire-pulse.service \
        wireplumber.service \
        >>"$ACTION_LOG" 2>&1; then
        echo "Failed to start the user audio services."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    PIPEWIRE_READY=0

    for _ in {1..100}; do
        if pactl info >/dev/null 2>&1; then
            PIPEWIRE_READY=1
            break
        fi

        sleep 0.1
    done

    if (( ! PIPEWIRE_READY )); then
        echo "PipeWire did not become ready."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    for _ in {1..100}; do
        if pactl list short sinks 2>/dev/null | grep -q .; then
            break
        fi

        sleep 0.1
    done

    STARTED_SINK="$(pactl get-default-sink 2>/dev/null || true)"

    normalize_audio_volumes "$ORIGINAL_VOLUME" "$STARTED_SINK"

    if ! systemctl start nqptp.service >>"$ACTION_LOG" 2>&1; then
        echo "Failed to start nqptp.service."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    if ! systemctl start shairport-sync.service >>"$ACTION_LOG" 2>&1; then
        echo "Failed to start shairport-sync.service."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    echo "Audio and AirPlay services started."
    echo
    echo "All hardware controls and PipeWire sinks were normalized to 100%."

    if [[ -n "$ORIGINAL_VOLUME" ]]; then
        echo "Master volume restored to: $ORIGINAL_VOLUME"
    fi

    pause_before_close
    exit 0
fi

if [[ "$SELECTED_CARD_VALUE" == stop-audio ]]; then
    echo "Stopping AirPlay and audio services..." | tee -a "$ACTION_LOG"

    close_mpv_windows || true

    systemctl stop shairport-sync.service \
        >>"$ACTION_LOG" 2>&1 || true

    systemctl stop nqptp.service \
        >>"$ACTION_LOG" 2>&1 || true

    if ! systemctl --user stop \
        wireplumber.service \
        pipewire-pulse.service \
        pipewire.service \
        pipewire-pulse.socket \
        pipewire.socket \
        >>"$ACTION_LOG" 2>&1; then
        echo "Failed to stop the user audio services."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    echo "Audio and AirPlay services stopped."
    pause_before_close
    exit 0
fi

if [[ "$SELECTED_CARD_VALUE" == toggle-hotspot ]]; then
    if hotspot_active; then nmcli connection down "$HOTSPOT_CONNECTION" >>"$ACTION_LOG" 2>&1; echo "AirPlay hotspot stopped."
    else nmcli connection up "$HOTSPOT_CONNECTION" >>"$ACTION_LOG" 2>&1; echo "AirPlay hotspot started."; fi
    pause_before_close; exit 0
fi

[[ "$SELECTED_CARD_VALUE" =~ ^[0-9]+$ ]] && (( SELECTED_CARD_VALUE < ${#CARDS[@]} )) || { echo "Invalid audio device selection."; exit 1; }
SELECTED_CARD="${CARDS[$SELECTED_CARD_VALUE]%%|*}"
SELECTED_CARD_DESCRIPTION="${CARDS[$SELECTED_CARD_VALUE]#*|}"
SELECTED_CARD_LABEL="$(card_display_label "$SELECTED_CARD" "$SELECTED_CARD_DESCRIPTION")"

CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
normalize_audio_volumes "$ORIGINAL_VOLUME" "$CURRENT_SINK"

mapfile -t PROFILES < <(pactl list cards | awk -v target="$SELECTED_CARD" '
/^[[:space:]]*Name:/{current=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",current);selected=(current==target);in_profiles=0;next}
selected&&/^[[:space:]]*Profiles:/{in_profiles=1;next}
selected&&in_profiles&&/^[[:space:]]*(Active Profile|Active Port|Ports):/{in_profiles=0;next}
selected&&in_profiles&&index($0,"(sinks:"){line=$0;sub(/^[[:space:]]*/,"",line);separator=index(line,": ");if(!separator)next;profile=substr(line,1,separator-1);description=substr(line,separator+2);if(profile=="off")next;availability="unknown";if(line~/available:[[:space:]]*yes/)availability="yes";else if(line~/available:[[:space:]]*no/)availability="no";sub(/[[:space:]]+\(sinks:.*/,"",description);print profile "|" description "|" availability}')
((${#PROFILES[@]})) || { echo "No profiles were found for: $SELECTED_CARD"; exit 1; }
get_active_profile() { pactl list cards | awk -v target="$SELECTED_CARD" '/^[[:space:]]*Name:/{x=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",x);s=(x==target);next}s&&/^[[:space:]]*Active Profile:/{x=$0;sub(/^[[:space:]]*Active Profile:[[:space:]]*/,"",x);print x;exit}'; }
ACTIVE_PROFILE="$(get_active_profile)"; ACTIVE_PROFILE_LABEL="$ACTIVE_PROFILE"
for entry in "${PROFILES[@]}"; do profile="${entry%%|*}"; remainder="${entry#*|}"; description="${remainder%%|*}"; [[ "$profile" == "$ACTIVE_PROFILE" ]] && { ACTIVE_PROFILE_LABEL="$(profile_display_label "$SELECTED_CARD" "$profile" "$description")"; break; }; done

echo; echo "Selected Audio Device"; echo; echo "$SELECTED_CARD_LABEL"; echo; echo "Profiles"; echo
[[ -n "$ACTIVE_PROFILE" ]] && echo "[0] Keep current profile ($ACTIVE_PROFILE_LABEL)" || echo "[0] Keep current profile"
for i in "${!PROFILES[@]}"; do
    entry="${PROFILES[$i]}"; profile="${entry%%|*}"; remainder="${entry#*|}"; description="${remainder%%|*}"; availability="${remainder##*|}"
    label="$(profile_display_label "$SELECTED_CARD" "$profile" "$description")"
    [[ "$profile" == "$ACTIVE_PROFILE" ]] && label+=" [active]"; [[ "$availability" == no ]] && label+=" [unavailable]"; [[ "$availability" == unknown ]] && label+=" [availability unknown]"
    echo "[$((i+1))] $label"
done
read -rp "Select profile: " profile_choice
SELECTED_PROFILE="$ACTIVE_PROFILE"; SELECTED_PROFILE_LABEL="$ACTIVE_PROFILE_LABEL"
if [[ "$profile_choice" != 0 ]]; then
    [[ "$profile_choice" =~ ^[0-9]+$ ]] || { echo "Invalid profile selection."; exit 1; }
    profile_index=$((profile_choice-1)); (( profile_index >= 0 && profile_index < ${#PROFILES[@]} )) || { echo "Invalid profile selection."; exit 1; }
    profile_entry="${PROFILES[$profile_index]}"; SELECTED_PROFILE="${profile_entry%%|*}"; remainder="${profile_entry#*|}"; description="${remainder%%|*}"; availability="${remainder##*|}"
    [[ "$availability" != no ]] || { echo "That profile is currently marked unavailable by PipeWire."; exit 1; }
    SELECTED_PROFILE_LABEL="$(profile_display_label "$SELECTED_CARD" "$SELECTED_PROFILE" "$description")"
    set_card_profile_stable "$SELECTED_CARD" "$SELECTED_PROFILE" || { echo "Failed to set profile '$SELECTED_PROFILE'. See: $ACTION_LOG"; exit 1; }
    printf '%s\n' "$SELECTED_PROFILE_LABEL" >"$PROFILE_SELECTION_FILE"
    PROFILE_OK=0; for _ in {1..50}; do [[ "$(get_active_profile)" == "$SELECTED_PROFILE" ]] && { PROFILE_OK=1; break; }; sleep 0.1; done
    (( PROFILE_OK )) || { echo "Warning: profile did not settle as '$SELECTED_PROFILE'." >>"$ACTION_LOG"; echo "Warning: profile switch did not settle. See: $ACTION_LOG"; }
fi

CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
for _ in {1..50}; do [[ -n "$CURRENT_SINK" ]] && pactl list short sinks | awk '{print $2}' | grep -Fqx "$CURRENT_SINK" && break; sleep 0.1; CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"; done
normalize_audio_volumes "$ORIGINAL_VOLUME" "$CURRENT_SINK"
RANDOM_PICK=0
mapfile -t SINKS < <(pactl list short sinks | while read -r id sink rest; do printf '%s|%s\n' "$sink" "$(sink_display_label "$sink")"; done)
CURRENT_DESC="Current Sink"; for entry in "${SINKS[@]}"; do [[ "${entry%%|*}" == "$CURRENT_SINK" ]] && { CURRENT_DESC="${entry#*|}"; break; }; done

echo; echo "Available Audio Sinks"; echo; echo "[0] Keep current sink ($CURRENT_DESC)"
for i in "${!SINKS[@]}"; do echo "[$((i+1))] ${SINKS[$i]#*|}"; done
ACOUSTIC_RANDOM=$((${#SINKS[@]} + 1))
echo "[$ACOUSTIC_RANDOM] Random Acoustic Environment"
echo
read -rp "Select sink: " choice
if [[ "$choice" != 0 ]]; then
    [[ "$choice" =~ ^[0-9]+$ ]] || { echo "Invalid selection."; exit 1; }
    sink=""
    if [[ "$choice" == "$ACOUSTIC_RANDOM" ]]; then
        mapfile -t RANDOM_CANDIDATES < <(
            printf '%s\n' "${SINKS[@]}" |
                cut -d'|' -f1 |
                grep -E '^earpods_' |
                grep -Ev '(^|[-_])anechoic([-_]|$)' ||
                true
        )

        if ((${#RANDOM_CANDIDATES[@]} == 0)); then
            echo "No non-anechoic acoustic-environment sinks are currently available."
            exit 1
        fi

        sink="$(printf '%s\n' "${RANDOM_CANDIDATES[@]}" | shuf -n 1)"
        RANDOM_PICK=1
        echo "Random acoustic environment selected."
    else
        index=$((choice-1)); (( index >= 0 && index < ${#SINKS[@]} )) || { echo "Invalid selection."; exit 1; }; sink="${SINKS[$index]%%|*}"
    fi
    wait_for_sink "$sink" || { echo "Selected sink is no longer available: $sink"; exit 1; }
    if set_default_sink_stable "$sink"; then
        SWITCH_OK=1
        normalize_audio_volumes "$ORIGINAL_VOLUME" "$sink"
    else
        SWITCH_OK=0
        echo "Warning: failed to switch to sink '$sink'." >>"$ACTION_LOG"
        echo "Warning: sink switch did not settle. See: $ACTION_LOG"
    fi
else
    echo "Keeping current sink."
fi

FINAL_SINK="$(pactl get-default-sink 2>/dev/null || true)"
normalize_audio_volumes "$ORIGINAL_VOLUME" "$FINAL_SINK"

echo
if (( RANDOM_PICK )); then echo "The sink is a secret!"; else
    echo "Current sink:"; current_sink="$(pactl get-default-sink 2>/dev/null || true)"; current_label="$current_sink"
    for entry in "${SINKS[@]}"; do [[ "${entry%%|*}" == "$current_sink" ]] && { current_label="${entry#*|}"; break; }; done
    echo "$current_label"
fi
echo; echo "Current device:"; echo "$SELECTED_CARD_LABEL"
echo; echo "Current profile:"; [[ -f "$PROFILE_SELECTION_FILE" ]] && cat "$PROFILE_SELECTION_FILE" || echo "${SELECTED_PROFILE_LABEL:-$SELECTED_PROFILE}"
pause_before_close
