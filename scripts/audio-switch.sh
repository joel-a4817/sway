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

case "$MODE" in
    hdmi)
        # First try existing HDMI sink
        HDMI_SINK="$(pactl list sinks short | awk 'tolower($0) ~ /hdmi/ {print $2; exit}')"

        if [ -n "${HDMI_SINK:-}" ]; then
            move_streams "$HDMI_SINK"
            exit 0
        fi

        # No HDMI sink exists -> activate HDMI profile
        CARD="$(pactl list cards short | awk 'NR==1{print $2}')"

        PROFILE="$(
            pactl list cards |
            sed -n '/Profiles:/,/Active Profile:/p' |
            grep 'available: yes' |
            grep -i hdmi |
            head -n1 |
            awk '{print $1}' |
            sed 's/:$//'
        )"

        [ -n "${PROFILE:-}" ] || exit 1

        pactl set-card-profile "$CARD" "$PROFILE"

        sleep 1

        HDMI_SINK="$(pactl list sinks short | awk 'tolower($0) ~ /hdmi/ {print $2; exit}')"

        [ -n "${HDMI_SINK:-}" ] && move_streams "$HDMI_SINK"
        ;;

    analog)
        # Prefer speaker/headphone sink if it already exists
        ANALOG_SINK="$(
            pactl list sinks short |
            awk '
                tolower($0) !~ /hdmi/ {print $2; exit}
            '
        )"

        if [ -n "${ANALOG_SINK:-}" ]; then
            move_streams "$ANALOG_SINK"
            exit 0
        fi

        # No analog sink exists -> activate analog profile
        CARD="$(pactl list cards short | awk 'NR==1{print $2}')"

        PROFILE="$(
            pactl list cards |
            sed -n '/Profiles:/,/Active Profile:/p' |
            grep 'available: yes' |
            grep '^.*output:analog' |
            head -n1 |
            awk '{print $1}' |
            sed 's/:$//'
        )"

        [ -n "${PROFILE:-}" ] || exit 1

        pactl set-card-profile "$CARD" "$PROFILE"

        sleep 1

        ANALOG_SINK="$(
            pactl list sinks short |
            awk '
                tolower($0) !~ /hdmi/ {print $2; exit}
            '
        )"

        [ -n "${ANALOG_SINK:-}" ] && move_streams "$ANALOG_SINK"
        ;;
esac
