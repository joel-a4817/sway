#!/usr/bin/env bash
set -u

HOME_DIR="/home/joel"
STATE_DIR="$HOME_DIR/.local/state/audio"
ACTION_LOG="$STATE_DIR/audio-switch.log"
HOTSPOT_CONNECTION="AirPlay Direct"

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_STARTED_FILE="/tmp/audio-toggle-started.$$"
CARD_SELECTION_FILE="/tmp/audio-card-selected.$$"
PROFILE_SELECTION_FILE="/tmp/audio-profile-selected.$$"
SWAYNAG_LOG="/tmp/audio-switch-swaynag.$$"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
printf '\n[%s] audio-switch pid=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$$" >>"$ACTION_LOG"

FINAL_PAUSE_REACHED=0
EXIT_HANDLER_RUNNING=0

cleanup() {
    rm -f "$RESULT_FILE" "$ACTION_STARTED_FILE" "$CARD_SELECTION_FILE" \
        "$PROFILE_SELECTION_FILE" "$SWAYNAG_LOG"
}

pause_before_close() {
    FINAL_PAUSE_REACHED=1
    echo
    if [[ -r /dev/tty && -w /dev/tty ]]; then
        read -n 1 -r -s -p "Press any key to close..." </dev/tty || true
        echo >/dev/tty
    fi
}

handle_script_exit() {
    local status=$?
    (( EXIT_HANDLER_RUNNING )) && return
    EXIT_HANDLER_RUNNING=1
    trap - EXIT INT TERM HUP QUIT
    if (( status != 0 && ! FINAL_PAUSE_REACHED )); then
        echo
        echo "Audio script exited unexpectedly."
        echo "Exit status: $status"
        [[ -s "$ACTION_LOG" ]] && { echo; echo "Action log: $ACTION_LOG"; cat "$ACTION_LOG"; }
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

echo '{ "command": ["set_property", "pause", true] }' | socat - /tmp/mpvsocket >/dev/null 2>&1 || true

normalize_audio_volumes() {
    local restore_volume="${1:-}" restore_sink="${2:-}" sink
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
export -f normalize_audio_volumes

ORIGINAL_SINK="$(pactl get-default-sink 2>/dev/null || true)"
ORIGINAL_VOLUME=""
if [[ -n "$ORIGINAL_SINK" ]]; then
    ORIGINAL_VOLUME="$(pactl get-sink-volume "$ORIGINAL_SINK" 2>/dev/null | grep -Po '[0-9]+%' | head -n1)"
fi
normalize_audio_volumes "$ORIGINAL_VOLUME" "$ORIGINAL_SINK"
export RESULT_FILE ACTION_STARTED_FILE ACTION_LOG CARD_SELECTION_FILE ORIGINAL_VOLUME ORIGINAL_SINK

mapfile -t CARDS < <(pactl list cards 2>/dev/null | awk '
function output_card(){if(card!=""){if(description=="")description=card; print card "|" description}}
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

hotspot_active() {
    nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fqx "$HOTSPOT_CONNECTION"
}

ARGS=(-t warning -y overlay -m "Audio Device Select")
ARGS+=( -z "Audio Restart" "touch '$ACTION_STARTED_FILE'; printf '%s\n' restart-audio >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
if hotspot_active; then
    ARGS+=( -z "Stop AirPlay Hotspot [active]" "touch '$ACTION_STARTED_FILE'; printf '%s\n' toggle-hotspot >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
else
    ARGS+=( -z "Start AirPlay Hotspot [inactive]" "touch '$ACTION_STARTED_FILE'; printf '%s\n' toggle-hotspot >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
fi
for i in "${!CARDS[@]}"; do
    card="${CARDS[$i]%%|*}"; description="${CARDS[$i]#*|}"
    ARGS+=( -z "$(card_display_label "$card" "$description")" "touch '$ACTION_STARTED_FILE'; printf '%s\n' '$i' >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
done

swaynag "${ARGS[@]}" >"$SWAYNAG_LOG" 2>&1 &
SWAYNAG_PID=$!
while [[ ! -f "$RESULT_FILE" ]]; do
    kill -0 "$SWAYNAG_PID" 2>/dev/null || { wait "$SWAYNAG_PID" 2>/dev/null || true; echo "No audio action was selected."; pause_before_close; exit 0; }
    sleep 0.1
done
kill "$SWAYNAG_PID" 2>/dev/null || true
wait "$SWAYNAG_PID" 2>/dev/null || true
SELECTED_CARD_VALUE="$(cat "$CARD_SELECTION_FILE")"

if [[ "$SELECTED_CARD_VALUE" == restart-audio ]]; then
    systemctl --user restart pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service >>"$ACTION_LOG" 2>&1 || true
    for _ in {1..100}; do pactl info >/dev/null 2>&1 && break; sleep 0.1; done
    pactl info >/dev/null 2>&1 || { echo "PipeWire did not become ready."; exit 1; }
    normalize_audio_volumes "$ORIGINAL_VOLUME" "$(pactl get-default-sink 2>/dev/null || true)"
    # Shairport and NQPTP are system services. Do not kill or launch them here.
    sudo -n systemctl try-restart shairport-sync.service >/dev/null 2>&1 || true
    echo "Audio restart completed."
    pause_before_close
    exit 0
fi

if [[ "$SELECTED_CARD_VALUE" == toggle-hotspot ]]; then
    if hotspot_active; then nmcli connection down "$HOTSPOT_CONNECTION" >>"$ACTION_LOG" 2>&1; echo "AirPlay hotspot stopped."
    else nmcli connection up "$HOTSPOT_CONNECTION" >>"$ACTION_LOG" 2>&1; echo "AirPlay hotspot started."; fi
    pause_before_close
    exit 0
fi

[[ "$SELECTED_CARD_VALUE" =~ ^[0-9]+$ ]] && (( SELECTED_CARD_VALUE < ${#CARDS[@]} )) || { echo "Invalid audio device selection."; exit 1; }
SELECTED_CARD="${CARDS[$SELECTED_CARD_VALUE]%%|*}"
SELECTED_CARD_DESCRIPTION="${CARDS[$SELECTED_CARD_VALUE]#*|}"
SELECTED_CARD_LABEL="$(card_display_label "$SELECTED_CARD" "$SELECTED_CARD_DESCRIPTION")"

mapfile -t PROFILES < <(pactl list cards | awk -v target="$SELECTED_CARD" '
/^[[:space:]]*Name:/{current=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",current);selected=(current==target);in_profiles=0;next}
selected && /^[[:space:]]*Profiles:/{in_profiles=1;next}
selected && in_profiles && /^[[:space:]]*(Active Profile|Active Port|Ports):/{in_profiles=0;next}
selected && in_profiles && index($0,"(sinks:"){line=$0;sub(/^[[:space:]]*/,"",line);separator=index(line,": ");if(!separator)next;profile=substr(line,1,separator-1);description=substr(line,separator+2);if(profile=="off")next;availability="unknown";if(line~/available:[[:space:]]*yes/)availability="yes";else if(line~/available:[[:space:]]*no/)availability="no";sub(/[[:space:]]+\(sinks:.*/,"",description);print profile "|" description "|" availability}')

ACTIVE_PROFILE="$(pactl list cards | awk -v target="$SELECTED_CARD" '/^[[:space:]]*Name:/{x=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",x);s=(x==target);next}s&&/^[[:space:]]*Active Profile:/{x=$0;sub(/^[[:space:]]*Active Profile:[[:space:]]*/,"",x);print x;exit}')"
echo; echo "Selected Audio Device"; echo; echo "$SELECTED_CARD_LABEL"; echo; echo "Profiles"; echo
printf '[0] Keep current profile (%s)\n' "${ACTIVE_PROFILE:-none}"
for i in "${!PROFILES[@]}"; do echo "[$((i+1))] ${PROFILES[$i]#*|}" | sed 's/|[^|]*$//'; done
echo; read -rp "Select profile: " profile_choice
if [[ "$profile_choice" != 0 ]]; then
    [[ "$profile_choice" =~ ^[0-9]+$ ]] || { echo "Invalid profile selection."; exit 1; }
    index=$((profile_choice-1)); (( index >= 0 && index < ${#PROFILES[@]} )) || { echo "Invalid profile selection."; exit 1; }
    entry="${PROFILES[$index]}"; profile="${entry%%|*}"; availability="${entry##*|}"
    [[ "$availability" != no ]] || { echo "That profile is unavailable."; exit 1; }
    pactl set-card-profile "$SELECTED_CARD" "$profile" >>"$ACTION_LOG" 2>&1 || exit 1
fi

for _ in {1..50}; do mapfile -t SINKS < <(pactl list short sinks | awk '{print $2}'); ((${#SINKS[@]})) && break; sleep 0.1; done
CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
echo; echo "Available Audio Sinks"; echo; echo "[0] Keep current sink ($CURRENT_SINK)"
for i in "${!SINKS[@]}"; do echo "[$((i+1))] ${SINKS[$i]}"; done
echo; read -rp "Select sink: " choice
if [[ "$choice" != 0 ]]; then
    [[ "$choice" =~ ^[0-9]+$ ]] || { echo "Invalid sink selection."; exit 1; }
    index=$((choice-1)); (( index >= 0 && index < ${#SINKS[@]} )) || { echo "Invalid sink selection."; exit 1; }
    sink="${SINKS[$index]}"
    pactl set-default-sink "$sink" >>"$ACTION_LOG" 2>&1 || exit 1
    while read -r id; do [[ -n "$id" ]] && pactl move-sink-input "$id" "$sink" >>"$ACTION_LOG" 2>&1 || true; done < <(pactl list short sink-inputs | awk '{print $1}')
    normalize_audio_volumes "$ORIGINAL_VOLUME" "$sink"
fi

echo; echo "Current sink:"; pactl get-default-sink 2>/dev/null || true
pause_before_close
