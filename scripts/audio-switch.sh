#!/usr/bin/env bash

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_LOG="/tmp/audio-toggle-action.$$"
CARD_SELECTION_FILE="/tmp/audio-card-selected.$$"
PROFILE_SELECTION_FILE="/tmp/audio-profile-selected.$$"

rm -f "$RESULT_FILE" "$ACTION_LOG" "$CARD_SELECTION_FILE" "$PROFILE_SELECTION_FILE"
touch "$ACTION_LOG"

cleanup() {
    rm -f "$RESULT_FILE" "$CARD_SELECTION_FILE" "$PROFILE_SELECTION_FILE"
}
trap cleanup EXIT

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
    done < <(pactl list short sinks | awk '{print $2}')

    if [[ -n "$restore_volume" && -n "$restore_sink" ]] &&
       pactl list short sinks | awk '{print $2}' | grep -Fqx "$restore_sink"; then
        pactl set-sink-volume "$restore_sink" "$restore_volume" >/dev/null 2>&1 || true
    fi
}
export -f normalize_audio_volumes

ORIGINAL_SINK="$(pactl get-default-sink 2>/dev/null || true)"
ORIGINAL_VOLUME=""
if pactl list short sinks | awk '{print $2}' | grep -Fqx "$ORIGINAL_SINK"; then
    ORIGINAL_VOLUME="$(pactl get-sink-volume "$ORIGINAL_SINK" | grep -Po '[0-9]+%' | head -n1)"
fi

export RESULT_FILE ACTION_LOG CARD_SELECTION_FILE PROFILE_SELECTION_FILE ORIGINAL_VOLUME ORIGINAL_SINK
normalize_audio_volumes "$ORIGINAL_VOLUME" "$ORIGINAL_SINK"

###############################################################################
# DISCOVER CARDS
###############################################################################

mapfile -t CARDS < <(
    pactl list cards | awk '
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
    echo "No PipeWire/PulseAudio cards were found."
    echo
    read -n 1 -rsp "Press any key to close..."
    echo
    exit 1
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

RESET_ACTION="$(cat <<EOF_RESET
systemctl --user stop wireplumber.service pipewire.service pipewire-pulse.service pipewire.socket pipewire-pulse.socket
rm -rf "\$HOME/.local/state/wireplumber"
systemctl --user start pipewire.socket pipewire-pulse.socket wireplumber.service
systemctl --user start wireplumber pipewire pipewire-pulse
for _ in {1..100}; do
    pactl info >/dev/null 2>&1 && break
    sleep 0.1
done
CURRENT_SINK="\$(pactl get-default-sink 2>/dev/null || true)"
normalize_audio_volumes "$ORIGINAL_VOLUME" "\$CURRENT_SINK"
printf '%s\n' "Audio reset" > "$CARD_SELECTION_FILE"
touch "$RESULT_FILE"
EOF_RESET
)"

ARGS+=(-z "Audio Restart" "$RESET_ACTION")

for i in "${!CARDS[@]}"; do
    card="${CARDS[$i]%%|*}"
    description="${CARDS[$i]#*|}"
    label="$(card_display_label "$card" "$description")"
    ARGS+=(-z "$label" "printf '%s\n' '$i' > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'")
done

swaynag "${ARGS[@]}" &
SWAYNAG_PID=$!

while [[ ! -f "$RESULT_FILE" ]]; do
    if ! kill -0 "$SWAYNAG_PID" 2>/dev/null; then
        echo "Audio device selection was cancelled."
        exit 1
    fi
    sleep 0.1
done
wait "$SWAYNAG_PID" 2>/dev/null || true

if [[ ! -f "$CARD_SELECTION_FILE" ]]; then
    echo "No audio device was selected."
    exit 1
fi

SELECTED_CARD_VALUE="$(cat "$CARD_SELECTION_FILE")"
if [[ "$SELECTED_CARD_VALUE" == "Audio reset" ]]; then
    echo
    echo "Audio reset completed."
    echo
    read -n 1 -rsp "Press any key to close..."
    echo
    exit 0
fi

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
    echo
    read -n 1 -rsp "Press any key to close..."
    echo
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

echo
read -n 1 -rsp "Press any key to close..."
echo
