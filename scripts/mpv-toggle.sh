#!/usr/bin/env bash

printf 'cycle pause\n' | socat - /tmp/mpvsocket

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
    amixer -c "$HYPERX_CARD" sset 'Speaker Volume' 100% unmute >/dev/null 2>&1 || true
fi

pactl list short sinks |
awk '{print $2}' |
grep '^alsa_output\.' |
while read -r sink; do
    pactl set-sink-volume "$sink" 100% >/dev/null 2>&1 || true
done
