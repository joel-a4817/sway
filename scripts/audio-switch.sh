#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-}"

move_streams() {
    local sink="$1"

    pactl set-default-sink "$sink"

    pactl list sink-inputs short | awk '{print $1}' | while read -r id; do
        pactl move-sink-input "$id" "$sink"
    done
}

#!/usr/bin/env bash

set -euo pipefail

move_streams() {
    local sink="$1"

    pactl set-default-sink "$sink"

    pactl list sink-inputs short | awk '{print $1}' | while read -r id; do
        pactl move-sink-input "$id" "$sink"
    done
}

find_hdmi_sink() {
    pactl list sinks short | awk 'tolower($0) ~ /hdmi/ {print $2; exit}'
}

find_analog_sink() {
    pactl list sinks short | awk 'tolower($0) !~ /hdmi/ {print $2; exit}'
}

enable_hdmi() {
    local card profile

    card="$(pactl list cards short | awk 'NR==1{print $2}')"

    profile="$(
        pactl list cards |
        sed -n '/Profiles:/,/Active Profile:/p' |
        grep 'available: yes' |
        grep -i hdmi |
        head -n1 |
        awk '{print $1}' |
        sed 's/:$//'
    )"

    [ -n "${profile:-}" ] || exit 1

    pactl set-card-profile "$card" "$profile"
    sleep 1
}

enable_analog() {
    local card profile

    card="$(pactl list cards short | awk 'NR==1{print $2}')"

    profile="$(
        pactl list cards |
        sed -n '/Profiles:/,/Active Profile:/p' |
        grep 'available: yes' |
        grep '^.*output:analog' |
        head -n1 |
        awk '{print $1}' |
        sed 's/:$//'
    )"

    [ -n "${profile:-}" ] || exit 1

    pactl set-card-profile "$card" "$profile"
    sleep 1
}

CURRENT="$(pactl get-default-sink)"

if echo "$CURRENT" | grep -qi hdmi; then
    # Currently on HDMI -> switch to analog

    ANALOG_SINK="$(find_analog_sink)"

    if [ -z "${ANALOG_SINK:-}" ]; then
        enable_analog
        ANALOG_SINK="$(find_analog_sink)"
    fi

    [ -n "${ANALOG_SINK:-}" ] && move_streams "$ANALOG_SINK"
else
    # Currently on analog -> switch to HDMI

    HDMI_SINK="$(find_hdmi_sink)"

    if [ -z "${HDMI_SINK:-}" ]; then
        enable_hdmi
        HDMI_SINK="$(find_hdmi_sink)"
    fi

    [ -n "${HDMI_SINK:-}" ] && move_streams "$HDMI_SINK"
fi
