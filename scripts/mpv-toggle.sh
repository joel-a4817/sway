#!/usr/bin/env bash

printf 'cycle pause\n' | socat - /tmp/mpvsocket

normalize_audio_volumes() {
    local restore_volume="${1:-}"
    local restore_sink="${2:-}"

    SOF_CARD="$(
        aplay -l |
        awk -F': ' '/sof|SOF/ {print $1; exit}' |
        grep -o '[0-9]\+' || true
    )"

    if [[ -n "${SOF_CARD:-}" ]]; then
        amixer -c "$SOF_CARD" sset Headphone 100% >/dev/null 2>&1 || true
    fi

    HYPERX_CARD="$(
        aplay -l |
        awk -F': ' '/HyperX Cloud III/ {print $1; exit}' |
        grep -o '[0-9]\+' || true
    )"

    if [[ -n "${HYPERX_CARD:-}" ]]; then
        amixer -c "$HYPERX_CARD" \
            sset 'Speaker Volume' 100% unmute \
            >/dev/null 2>&1 || true
    fi

    pactl list short sinks |
    awk '{print $2}' |
    while read -r sink; do
        pactl set-sink-mute "$sink" 0 >/dev/null 2>&1 || true
        pactl set-sink-volume "$sink" 100% >/dev/null 2>&1 || true
    done

    if [[ -n "$restore_volume" && -n "$restore_sink" ]]; then
        pactl set-sink-volume "$restore_sink" "$restore_volume" \
            >/dev/null 2>&1 || true
    fi
}

normalize_audio_volumes
