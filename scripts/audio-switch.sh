#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_LOG="/tmp/audio-toggle-action.$$"

rm -f "$RESULT_FILE" "$ACTION_LOG"
touch "$ACTION_LOG"

export RESULT_FILE ACTION_LOG

# Build a list of sinks:
# sink_name|description
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

if ((${#SINKS[@]} == 0)); then
    echo "No audio sinks found."
    exit 1
fi

ARGS=(
    -t warning
    -y overlay
    -m "Audio Output Selector"
)

for entry in "${SINKS[@]}"; do
    sink="${entry%%|*}"
    desc="${entry#*|}"

    case "$desc" in
        *HDMI*)
            label="HDMI"
            ;;
        *Speaker*)
            label="Speaker"
            ;;
        *Headphone*)
            label="Headphones"
            ;;
        *Bluetooth*)
            label="Bluetooth"
            ;;
        *USB*)
            label="USB Audio"
            ;;
        *)
            label="$desc"
            [[ ${#label} -gt 12 ]] && label="${label:0:12}..."
            ;;
    esac

    ARGS+=(
        -z "$label"
        "
move_streams() {
    local sink=\"\$1\"

    pactl set-default-sink \"\$sink\" >>\"\$ACTION_LOG\" 2>&1

    pactl list sink-inputs short |
        awk '{print \$1}' |
        while read -r id; do
            pactl move-sink-input \"\$id\" \"\$sink\" >>\"\$ACTION_LOG\" 2>&1
        done
}

SINK=\"$sink\"

echo \"Switching to: \$SINK\" >>\"\$ACTION_LOG\"

move_streams \"\$SINK\"

echo \"SUCCESS\" >>\"\$ACTION_LOG\"
echo \"Sink: \$SINK\" >>\"\$ACTION_LOG\"

touch \"\$RESULT_FILE\"
"
    )
done

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

echo "Updated default sink:"
pactl get-default-sink

echo
read -n 1 -rsp "Press any key to close..."
