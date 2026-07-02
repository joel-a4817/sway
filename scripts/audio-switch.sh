#!/usr/bin/env bash

set -euo pipefail

CARD="$(pactl list cards short | awk 'NR==1{print $2}')"

case "${1:-}" in
    hdmi)
        PROFILE="$(pactl list cards | sed -n '/Profiles:/,/Active Profile:/p' | grep 'available: yes' | grep 'hdmi' | head -n1 | awk '{print $1}' | sed 's/:$//')"
        ;;
    analog)
        PROFILE="$(pactl list cards | sed -n '/Profiles:/,/Active Profile:/p' | grep 'available: yes' | grep '^.*output:analog' | head -n1 | awk '{print $1}' | sed 's/:$//')"
        ;;
    *)
        echo "Usage: $0 hdmi|analog"
        exit 1
        ;;
esac

echo "CARD=$CARD"
echo "PROFILE=$PROFILE"

pactl set-card-profile "$CARD" "$PROFILE"

