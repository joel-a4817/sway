#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-}"

find_profile() {
    local pattern="$1"

    pactl list cards | awk -v pat="$pattern" '
    /^Card #/ {
        card=""
    }

    /^[[:space:]]*Name:/ {
        card=$2
    }

    /Profiles:/ {
        profiles=1
        next
    }

    /Active Profile:/ {
        profiles=0
    }

    profiles && $0 ~ pat && $0 ~ /available: yes/ {
        profile=$1
        sub(/:$/, "", profile)
        print card "|" profile
        exit
    }
    '
}

case "$MODE" in
    hdmi)
        RESULT="$(find_profile "hdmi")"
        ;;
    analog)
        RESULT="$(find_profile "analog")"
        ;;
    *)
        echo "Usage: $0 {hdmi|analog}"
        exit 1
        ;;
esac

[ -n "$RESULT" ] || {
    echo "No matching profile found"
    exit 1
}

CARD="${RESULT%%|*}"
PROFILE="${RESULT##*|}"

echo "Card: $CARD"
echo "Profile: $PROFILE"

pactl set-card-profile "$CARD" "$PROFILE"
