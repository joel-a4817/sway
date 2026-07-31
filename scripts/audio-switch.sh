#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_LOG="/tmp/audio-toggle-action.$$"

rm -f "$RESULT_FILE" "$ACTION_LOG"
touch "$ACTION_LOG"

find_hdmi_sink() {
    pactl list sinks short | awk 'tolower($0) ~ /hdmi/ {print $2; exit}'
}

find_analog_sink() {
    pactl list sinks short | awk 'tolower($0) !~ /hdmi/ {print $2; exit}'
}

hdmi_available() {
    [[ -n "$(find_hdmi_sink)" ]]
}

analog_available() {
    [[ -n "$(find_analog_sink)" ]]
}

HDMI_LABEL="HDMI Audio"
ANALOG_LABEL="Analog Audio"

hdmi_available || HDMI_LABEL="HDMI Audio (Unavailable)"
analog_available || ANALOG_LABEL="Analog Audio (Unavailable)"

echo "Current default sink:"
pactl get-default-sink

export RESULT_FILE ACTION_LOG

swaynag \
    -t warning \
    -y overlay \
    -m "Audio Output Selector" \
    -z "$HDMI_LABEL" \
'
move_streams() {
    local sink="$1"

    pactl set-default-sink "$sink" >>"$ACTION_LOG" 2>&1

    pactl list sink-inputs short |
        awk "{print \$1}" |
        while read -r id; do
            pactl move-sink-input "$id" "$sink" >>"$ACTION_LOG" 2>&1
        done
}

find_hdmi_sink() {
    pactl list sinks short |
        awk '"'"'tolower($0) ~ /hdmi/ {print $2; exit}'"'"'
}

HDMI_SINK="$(find_hdmi_sink)"

if [[ -z "${HDMI_SINK:-}" ]]; then
    echo "ERROR: HDMI output is not available." >>"$ACTION_LOG"
    touch "$RESULT_FILE"
    exit 1
fi

echo "Switching to HDMI..." >>"$ACTION_LOG"

move_streams "$HDMI_SINK"

echo "SUCCESS: Switched to HDMI" >>"$ACTION_LOG"
echo "Sink: $HDMI_SINK" >>"$ACTION_LOG"

touch "$RESULT_FILE"
' \
    -z "$ANALOG_LABEL" \
'
move_streams() {
    local sink="$1"

    pactl set-default-sink "$sink" >>"$ACTION_LOG" 2>&1

    pactl list sink-inputs short |
        awk "{print \$1}" |
        while read -r id; do
            pactl move-sink-input "$id" "$sink" >>"$ACTION_LOG" 2>&1
        done
}

find_analog_sink() {
    pactl list sinks short |
        awk '"'"'tolower($0) !~ /hdmi/ {print $2; exit}'"'"'
}

ANALOG_SINK="$(find_analog_sink)"

if [[ -z "${ANALOG_SINK:-}" ]]; then
    echo "ERROR: Analog output is not available." >>"$ACTION_LOG"
    touch "$RESULT_FILE"
    exit 1
fi

echo "Switching to Analog..." >>"$ACTION_LOG"

move_streams "$ANALOG_SINK"

echo "SUCCESS: Switched to Analog" >>"$ACTION_LOG"
echo "Sink: $ANALOG_SINK" >>"$ACTION_LOG"

touch "$RESULT_FILE"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

echo

if [[ -s "$ACTION_LOG" ]]; then
    cat "$ACTION_LOG"
    echo
fi

rm -f "$RESULT_FILE" "$ACTION_LOG"

echo "Updated default sink:"
pactl get-default-sink

echo
read -n 1 -rsp "Press any key to close..."
echo
