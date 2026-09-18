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

echo '{ "command": ["set_property", "pause", true] }' | socat - /tmp/mpvsocket >/dev/null 2>&1 || true

normalize_audio_volumes() {
    local restore_volume="${1:-}" restore_sink="${2:-}" sof_card hyperx_card sink
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
export -f normalize_audio_volumes

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
((${#CARDS[@]})) || { echo "PipeWire is unavailable or no audio cards were found."; exit 1; }

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
        *HDMI1*) label="HDMI 1" ;; *HDMI2*) label="HDMI 2" ;; *HDMI3*) label="HDMI 3" ;;
        *pro-output-[0-9]*) label="${sink##*.}" ;; *HyperX_Cloud_III*) label="Cloud III USB" ;;
        *Headphones*) label="Headphones" ;; *Speaker*) label="Speaker" ;; *analog*) label="analog-stereo" ;; *hdmi*) label="HDMI" ;;
    esac
    printf '%s\n' "$label"
}
hotspot_active() { nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fqx "$HOTSPOT_CONNECTION"; }

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

if [[ "$SELECTED_CARD_VALUE" == restart-audio ]]; then
    systemctl --user restart pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service >>"$ACTION_LOG" 2>&1 || true
    for _ in {1..100}; do pactl info >/dev/null 2>&1 && break; sleep 0.1; done
    pactl info >/dev/null 2>&1 || { echo "PipeWire did not become ready."; exit 1; }
    normalize_audio_volumes "$ORIGINAL_VOLUME" "$(pactl get-default-sink 2>/dev/null || true)"
    # Restart the persistent AirPlay services after PipeWire is ready.
    # NQPTP is restarted first because AirPlay 2 timing depends on it.
    if ! sudo -n systemctl restart nqptp.service >>"$ACTION_LOG" 2>&1; then
        echo "Failed to restart nqptp.service."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    if ! sudo -n systemctl restart shairport-sync.service >>"$ACTION_LOG" 2>&1; then
        echo "Failed to restart shairport-sync.service."
        echo "See: $ACTION_LOG"
        exit 1
    fi

    echo "Audio and AirPlay services restarted."; pause_before_close; exit 0
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

# Normalize after card selection, before profile discovery/selection.
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
    pactl set-card-profile "$SELECTED_CARD" "$SELECTED_PROFILE" >>"$ACTION_LOG" 2>&1 || { echo "Failed to set profile '$SELECTED_PROFILE'. See: $ACTION_LOG"; exit 1; }
    printf '%s\n' "$SELECTED_PROFILE_LABEL" >"$PROFILE_SELECTION_FILE"
    PROFILE_OK=0; for _ in {1..50}; do [[ "$(get_active_profile)" == "$SELECTED_PROFILE" ]] && { PROFILE_OK=1; break; }; sleep 0.1; done
    (( PROFILE_OK )) || { echo "Warning: profile did not settle as '$SELECTED_PROFILE'." >>"$ACTION_LOG"; echo "Warning: profile switch did not settle. See: $ACTION_LOG"; }
fi

# Normalize after profile selection, before sink discovery/selection.
CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
for _ in {1..50}; do [[ -n "$CURRENT_SINK" ]] && pactl list short sinks | awk '{print $2}' | grep -Fqx "$CURRENT_SINK" && break; sleep 0.1; CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"; done
normalize_audio_volumes "$ORIGINAL_VOLUME" "$CURRENT_SINK"
RANDOM_PICK=0
mapfile -t SINKS < <(pactl list short sinks | while read -r id sink rest; do printf '%s|%s\n' "$sink" "$(sink_display_label "$sink")"; done)
CURRENT_DESC="Current Sink"; for entry in "${SINKS[@]}"; do [[ "${entry%%|*}" == "$CURRENT_SINK" ]] && { CURRENT_DESC="${entry#*|}"; break; }; done

echo; echo "Available Audio Sinks"; echo; echo "[0] Keep current sink ($CURRENT_DESC)"
for i in "${!SINKS[@]}"; do echo "[$((i+1))] ${SINKS[$i]#*|}"; done
EARPOD_RANDOM=$((${#SINKS[@]}+1)); CLOUD3_RANDOM=$((${#SINKS[@]}+2)); echo "[$EARPOD_RANDOM] Random EarPods"; echo "[$CLOUD3_RANDOM] Random Cloud III"; echo
read -rp "Select sink: " choice
if [[ "$choice" != 0 ]]; then
    [[ "$choice" =~ ^[0-9]+$ ]] || { echo "Invalid selection."; exit 1; }
    sink=""
    if [[ "$choice" == "$EARPOD_RANDOM" ]]; then
        mapfile -t RANDOM_CANDIDATES < <(printf '%s\n' "${SINKS[@]}" | cut -d'|' -f1 | grep -E '^(earpods.*|alsa_output.*Headphones.*)$' || true)
        ((${#RANDOM_CANDIDATES[@]})) || { echo "No EarPods sinks are currently available."; exit 1; }; sink="$(printf '%s\n' "${RANDOM_CANDIDATES[@]}" | shuf -n1)"; RANDOM_PICK=1; echo "Random EarPods sink selected."
    elif [[ "$choice" == "$CLOUD3_RANDOM" ]]; then
        mapfile -t RANDOM_CANDIDATES < <(printf '%s\n' "${SINKS[@]}" | cut -d'|' -f1 | grep -E '^(cloud3.*|alsa_output.*HyperX_Cloud_III.*)$' || true)
        ((${#RANDOM_CANDIDATES[@]})) || { echo "No Cloud III sinks are currently available."; exit 1; }; sink="$(printf '%s\n' "${RANDOM_CANDIDATES[@]}" | shuf -n1)"; RANDOM_PICK=1; echo "Random Cloud III sink selected."
    else
        index=$((choice-1)); (( index >= 0 && index < ${#SINKS[@]} )) || { echo "Invalid selection."; exit 1; }; sink="${SINKS[$index]%%|*}"
    fi
    pactl list short sinks | awk '{print $2}' | grep -Fqx "$sink" || { echo "Selected sink is no longer available: $sink"; exit 1; }
    pactl set-default-sink "$sink" >>"$ACTION_LOG" 2>&1 || { echo "Failed to set default sink '$sink'. See: $ACTION_LOG"; exit 1; }
    while read -r id; do [[ -n "$id" ]] && pactl move-sink-input "$id" "$sink" >>"$ACTION_LOG" 2>&1 || true; done < <(pactl list short sink-inputs | awk '{print $1}')
    SWITCH_OK=0; for _ in {1..50}; do [[ "$(pactl get-default-sink 2>/dev/null || true)" == "$sink" ]] && { SWITCH_OK=1; break; }; sleep 0.1; done
    (( SWITCH_OK )) && normalize_audio_volumes "$ORIGINAL_VOLUME" "$sink" || { echo "Warning: failed to switch to sink '$sink'." >>"$ACTION_LOG"; echo "Warning: sink switch did not settle. See: $ACTION_LOG"; }
else
    echo "Keeping current sink."
fi

# Final normalization after sink selection, including keep-current.
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
