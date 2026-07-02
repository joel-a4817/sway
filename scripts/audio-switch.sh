#!/usr/bin/env bash

set -euo pipefail

CARD="$(pactl list cards short | awk 'NR==1{print $2}')"

case "$1" in
    hdmi)
        PROFILE="$(
            pactl list cards |
            awk '
                /Profiles:/ {p=1; next}
                /Active Profile:/ {p=0}
                p && /output:.*hdmi/ && /available: yes/ {
                    print $1
                    exit
                }
            ' |
            sed 's/:$//'
        )"
        ;;

    analog)
        PROFILE="$(
            pactl list cards |
            awk '
                /Profiles:/ {p=1; next}
                /Active Profile:/ {p=0}

                p &&
                /output:/ &&
                !/hdmi/ &&
                /available: yes/ {

                    name=$1
                    gsub(/:$/, "", name)

                    if (match($0, /priority: [0-9]+/)) {
                        pr = substr($0, RSTART+10, RLENGTH-10)

                        if (pr > best) {
                            best = pr
                            profile = name
                        }
                    }
                }

                END { print profile }
            '
        )"
        ;;
esac

[ -n "$PROFILE" ] || {
    echo "No matching profile found"
    exit 1
}

echo "Switching to: $PROFILE"
pactl set-card-profile "$CARD" "$PROFILE"
