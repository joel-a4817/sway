#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_LOG="/tmp/audio-toggle-action.$$"

rm -f "$RESULT_FILE" "$ACTION_LOG"
touch "$ACTION_LOG"

export RESULT_FILE ACTION_LOG

CARD="$(pactl list cards short | awk 'NR==1 {print $2}')"

declare -A AVAIL

while IFS='|' read -r port avail; do
    AVAIL["$port"]="$avail"
done < <(
    pactl list cards | awk '
    /\[Out\]/ {

        name=$0
        sub(/^[[:space:]]*\[Out\][[:space:]]*/, "", name)
        sub(/:.*/, "", name)

        avail="unknown"

        if ($0 ~ /not available/)
            avail="no"
        else if ($0 ~ /available/)
            avail="yes"

        print name "|" avail
    }

    /^[[:space:]]*hdmi-output-/ ||
    /^[[:space:]]*analog-output-/ {

        port=$1
        sub(/:$/, "", port)

        avail="unknown"

        if ($0 ~ /not available/)
            avail="no"
        else if ($0 ~ /available/)
            avail="yes"

        print port "|" avail
    }
    '
)

ARGS=(
    -t warning
    -y overlay
    -m "Audio Output Selector"
)

###############################################################################
# DETECT MACHINE TYPE
###############################################################################

if pactl list cards | grep -q 'output:hdmi-stereo:'; then

    ###########################################################################
    # OLD MACHINE (PROFILE BASED)
    ###########################################################################

    ARGS+=(
        -z "Speaker"
        "
pactl set-card-profile \"$CARD\" output:analog-stereo >>\"\$ACTION_LOG\" 2>&1

sleep 1

sink=\$(pactl list short sinks | awk 'NR==1 {print \$2}')

if [[ -n \$sink ]]; then
    pactl set-default-sink \"\$sink\" >>\"\$ACTION_LOG\" 2>&1

    pactl list sink-inputs short |
        awk '{print \$1}' |
        while read -r id; do
            pactl move-sink-input \"\$id\" \"\$sink\" >>\"\$ACTION_LOG\" 2>&1
        done
fi

touch \"\$RESULT_FILE\"
"
    )

    ARGS+=(
        -z "HDMI1"
        "
pactl set-card-profile \"$CARD\" output:hdmi-stereo >>\"\$ACTION_LOG\" 2>&1

sleep 1

sink=\$(pactl list short sinks | awk 'NR==1 {print \$2}')

if [[ -n \$sink ]]; then
    pactl set-default-sink \"\$sink\" >>\"\$ACTION_LOG\" 2>&1

    pactl list sink-inputs short |
        awk '{print \$1}' |
        while read -r id; do
            pactl move-sink-input \"\$id\" \"\$sink\" >>\"\$ACTION_LOG\" 2>&1
        done
fi

touch \"\$RESULT_FILE\"
"
    )

    ARGS+=(
        -z "HDMI2"
        "
pactl set-card-profile \"$CARD\" output:hdmi-stereo-extra1 >>\"\$ACTION_LOG\" 2>&1

sleep 1

sink=\$(pactl list short sinks | awk 'NR==1 {print \$2}')

if [[ -n \$sink ]]; then
    pactl set-default-sink \"\$sink\" >>\"\$ACTION_LOG\" 2>&1

    pactl list sink-inputs short |
        awk '{print \$1}' |
        while read -r id; do
            pactl move-sink-input \"\$id\" \"\$sink\" >>\"\$ACTION_LOG\" 2>&1
        done
fi

touch \"\$RESULT_FILE\"
"
    )

    ARGS+=(
        -z "HDMI3"
        "
pactl set-card-profile \"$CARD\" output:hdmi-stereo-extra2 >>\"\$ACTION_LOG\" 2>&1

sleep 1

sink=\$(pactl list short sinks | awk 'NR==1 {print \$2}')

if [[ -n \$sink ]]; then
    pactl set-default-sink \"\$sink\" >>\"\$ACTION_LOG\" 2>&1

    pactl list sink-inputs short |
        awk '{print \$1}' |
        while read -r id; do
            pactl move-sink-input \"\$id\" \"\$sink\" >>\"\$ACTION_LOG\" 2>&1
        done
fi

touch \"\$RESULT_FILE\"
"
    )

else

    ###########################################################################
    # HP MACHINE (SINK BASED)
    ###########################################################################

    mapfile -t SINKS < <(
        pactl list sinks |
        awk '
            /^Sink #/ {
                if (name != "" && desc != "")
                    print name "|" desc

                name=""
                desc=""
            }

            /^[[:space:]]*Name:/ {
                name=$2
            }

            /^[[:space:]]*Description:/ {
                sub(/^[[:space:]]*Description:[[:space:]]*/, "")
                desc=$0
            }

            END {
                if (name != "" && desc != "")
                    print name "|" desc
            }
        '
    )

    for entry in "${SINKS[@]}"; do

        sink="${entry%%|*}"
        desc="${entry#*|}"
        case "$desc" in
            *HDMI*3*)
                label="HDMI3"
                key="HDMI3"
                ;;
            *HDMI*2*)
                label="HDMI2"
                key="HDMI2"
                ;;
            *HDMI*1*)
                label="HDMI1"
                key="HDMI1"
                ;;
            *Speaker*)
                label="Speaker"
                key="Speaker"
                ;;
            *Headphone*)
                label="Headphones"
                key="Headphones"
                ;;
            *)
                label="$desc"
                key=""
                ;;
        esac

        if [[ -n "$key" && "${AVAIL[$key]:-yes}" == "no" ]]; then
            label="$label (unavailable)"
        fi
        ARGS+=(
            -z "$label"
            "
pactl set-default-sink \"$sink\" >>\"\$ACTION_LOG\" 2>&1

pactl list sink-inputs short |
    awk '{print \$1}' |
    while read -r id; do
        pactl move-sink-input \"\$id\" \"$sink\" >>\"\$ACTION_LOG\" 2>&1
    done

touch \"\$RESULT_FILE\"
"
        )
    done
fi

###############################################################################

swaynag "${ARGS[@]}" &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

echo

if [[ -s "$ACTION_LOG" ]]; then
    cat "$ACTION_LOG"
    echo
fi

rm -f "$RESULT_FILE" "$ACTION_LOG"

echo "Updated sink:"
pactl get-default-sink 2>/dev/null || true

echo
read -n 1 -rsp "Press any key to close..."
