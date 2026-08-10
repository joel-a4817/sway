#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_LOG="/tmp/audio-toggle-action.$$"

rm -f "$RESULT_FILE" "$ACTION_LOG"
touch "$ACTION_LOG"

export RESULT_FILE ACTION_LOG

CARD="$(pactl list cards short | awk 'NR==1 {print $2}')"

ARGS=(
    -t warning
    -y overlay
    -m "Audio Profile Selector"
)

###############################################################################
# PROFILE BUTTON
###############################################################################

add_profile_button() {

    local label="$1"
    local profile="$2"

    ARGS+=(
        -z "$label"
        "
pactl set-card-profile \"$CARD\" \"$profile\" >>\"\$ACTION_LOG\" 2>&1
touch \"\$RESULT_FILE\"
"
    )
}

###############################################################################
# DISCOVER PROFILES
###############################################################################

while IFS='|' read -r profile description available; do

    [[ "$profile" == "off" ]] && continue

    if [[ "$description" == *"Pro Audio"* ]]; then

    label="Pro Audio"

elif [[ "$profile" == "input:analog-stereo" ]]; then

    label="Mic Only"

elif [[ "$description" == *"Analog Stereo Duplex"* ]]; then

    label="Analog + Mic"

elif [[ "$description" == *"Analog Stereo Output"* ]]; then

    label="Analog"

elif [[ "$description" == *"HDMI"* ]]; then

    hdmi="1"
    [[ "$description" == *"HDMI 2"* ]] && hdmi="2"
    [[ "$description" == *"HDMI 3"* ]] && hdmi="3"

    label="HDMI${hdmi}"

    if [[ "$description" == *"7.1"* ]]; then
        label+=" 7.1"
    elif [[ "$description" == *"5.1"* ]]; then
        label+=" 5.1"
    fi

    if [[ "$description" == *"Input"* ]]; then
        label+=" + Mic"
    fi

fi

    [[ "$available" == "no" ]] && continue

    add_profile_button "$label" "$profile"

done < <(
    pactl list cards | awk '

    /^[[:space:]]*[A-Za-z0-9].*[[:space:]]\(sinks:/ {

        line=$0

        sub(/^[[:space:]]*/, "", line)

        split(line, parts, ": ")
        profile = parts[1]

        desc=line
        sub(/^[^:]*:[[:space:]]*/, "", desc)
        sub(/[[:space:]]+\(sinks:.*/, "", desc)

        avail="yes"

        if (line ~ /available:[[:space:]]*no/)
            avail="no"

        print profile "|" desc "|" avail
    }
    '
)

###############################################################################
# SELECT PROFILE
###############################################################################

swaynag "${ARGS[@]}" &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

###############################################################################
# BUILD SINK MENU
###############################################################################

sleep 1

CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"

mapfile -t SINKS < <(
    pactl list short sinks |
    awk '{
        print $2 "|" $2
    }'
)

echo
echo "Available Audio Sinks"
echo

CURRENT_DESC="Current Sink"

for entry in "${SINKS[@]}"; do

    sink="${entry%%|*}"
    desc="${entry#*|}"

    if [[ "$sink" == "$CURRENT_SINK" ]]; then
        CURRENT_DESC="$desc"
        break
    fi
done

echo "[0] Keep current sink ($CURRENT_DESC)"

for i in "${!SINKS[@]}"; do
    desc="${SINKS[$i]#*|}"
    echo "[$((i + 1))] $desc"
done

echo
read -rp "Select sink: " choice

if [[ "$choice" == "0" ]]; then

    echo
    echo "Keeping current sink."

else

    index=$((choice - 1))

    if (( index < 0 || index >= ${#SINKS[@]} )); then

        echo
        echo "Invalid selection."
        exit 1
    fi

    sink="${SINKS[$index]%%|*}"

    pactl set-default-sink "$sink" >>"$ACTION_LOG" 2>&1

    pactl list sink-inputs short |
        awk '{print $1}' |
        while read -r id; do
            pactl move-sink-input "$id" "$sink" >>"$ACTION_LOG" 2>&1
        done
fi

###############################################################################

if [[ -s "$ACTION_LOG" ]]; then
    cat "$ACTION_LOG"
    echo
fi

rm -f "$RESULT_FILE" "$ACTION_LOG"

pactl list cards | grep "Active Profile"

echo
echo "Current sink:"
pactl get-default-sink 2>/dev/null || true

echo
read -n 1 -rsp "Press any key to close..."
