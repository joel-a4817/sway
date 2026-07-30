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

    if [ -z "${profile:-}" ]; then
        echo "ERROR: No available HDMI profile found"
        return 1
    fi

    echo "Enabling HDMI profile: $profile"
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

    if [ -z "${profile:-}" ]; then
        echo "ERROR: No available Analog profile found"
        return 1
    fi

    echo "Enabling Analog profile: $profile"
    pactl set-card-profile "$card" "$profile"
    sleep 1
}

CURRENT="$(pactl get-default-sink)"

echo "Current sink: $CURRENT"

if echo "$CURRENT" | grep -qi hdmi; then
    echo "HDMI currently active, switching to Analog..."

    ANALOG_SINK="$(find_analog_sink)"

    if [ -z "${ANALOG_SINK:-}" ]; then
        echo "Analog sink not found, attempting to enable Analog profile..."
        enable_analog
        ANALOG_SINK="$(find_analog_sink)"
    fi

    if [ -z "${ANALOG_SINK:-}" ]; then
        echo "ERROR: Failed to find an Analog sink"
        exit 1
    fi

    move_streams "$ANALOG_SINK"

    NEW="$(pactl get-default-sink)"

    if [ "$NEW" = "$ANALOG_SINK" ]; then
        echo "SUCCESS: Switched to Analog"
        echo "Sink: $NEW"
    else
        echo "ERROR: Switch to Analog failed"
        echo "Expected: $ANALOG_SINK"
        echo "Actual:   $NEW"
        exit 1
    fi

else
    echo "Analog currently active, switching to HDMI..."

    HDMI_SINK="$(find_hdmi_sink)"

    if [ -z "${HDMI_SINK:-}" ]; then
        echo "HDMI sink not found, attempting to enable HDMI profile..."
        enable_hdmi
        HDMI_SINK="$(find_hdmi_sink)"
    fi

    if [ -z "${HDMI_SINK:-}" ]; then
        echo "ERROR: Failed to find an HDMI sink"
        exit 1
    fi

    move_streams "$HDMI_SINK"

    NEW="$(pactl get-default-sink)"

    if [ "$NEW" = "$HDMI_SINK" ]; then
        echo "SUCCESS: Switched to HDMI"
        echo "Sink: $NEW"
    else
        echo "ERROR: Switch to HDMI failed"
        echo "Expected: $HDMI_SINK"
        echo "Actual:   $NEW"
        exit 1
    fi
fi
