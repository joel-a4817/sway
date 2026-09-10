#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/audio-toggle-complete.$$"
ACTION_LOG="/tmp/audio-toggle-action.$$"
HEADPHONE_STATE="/tmp/headphone-volume.saved"

rm -f "$RESULT_FILE" "$ACTION_LOG"
touch "$ACTION_LOG"

export RESULT_FILE ACTION_LOG

CARD="$(pactl list cards short | awk 'NR==1 {print $2}')"

ARGS=(
    -t warning
    -y overlay
    -m "Audio Profile Select"
)

###############################################################################
# PROFILE BUTTON
###############################################################################

add_profile_button() {
    local label="$1"
    local profile="$2"

    ARGS+=(
        -z "$label"
        "
pactl set-card-profile \"$CARD\" \"$profile\" >>\"\$ACTION_LOG\" 2>&1
touch \"\$RESULT_FILE\"
"
    )
}

###############################################################################
# DISCOVER PROFILES
###############################################################################

while IFS='|' read -r profile description available; do

    [[ "$profile" == "off" ]] && continue

      label="$description"

      if [[ "$description" == *"Headphone"* ]]; then
          label="Headphones"
      fi

      if [[ "$description" == *"Speaker"* ]]; then
          label="Speaker"
      fi

      if [[ "$description" == *"Pro Audio"* ]]; then
          label="Pro Audio"
      fi

      if [[ "$label" == "$description" ]] &&
         [[ "$description" == *"HDMI"* ]]; then

          hdmi="1"

          [[ "$description" == *"HDMI 2"* ]] && hdmi="2"
          [[ "$description" == *"HDMI 3"* ]] && hdmi="3"

          label="HDMI${hdmi}"

          [[ "$description" == *"7.1"* ]] && label+=" 7.1"
          [[ "$description" == *"5.1"* ]] && label+=" 5.1"

          if [[ "$description" == *"Input"* ]]; then
              label+=" + Mic"
          fi
      fi

      if [[ "$label" == "$description" ]] &&
         [[ "$profile" == "input:analog-stereo" ]]; then
          label="Mic Only"
      fi

      if [[ "$label" == "$description" ]] &&
         [[ "$description" == *"Analog"* ]]; then

          label="Analog"

          if [[ "$description" == *"Input"* ||
                "$description" == *"Duplex"* ]]; then
              label+=" + Mic"
          fi
      fi

    [[ "$available" == "no" ]] && continue

    add_profile_button "$label" "$profile"

done < <(
    pactl list cards | awk '
    /^[[:space:]]*[A-Za-z0-9].*[[:space:]]\(sinks:/ {
        line=$0
        sub(/^[[:space:]]*/, "", line)
        split(line, parts, ": ")
        profile = parts[1]
        desc=line
        sub(/^[^:]*:[[:space:]]*/, "", desc)
        sub(/[[:space:]]+\(sinks:.*/, "", desc)
        avail="yes"
        if (line ~ /available:[[:space:]]*no/)
            avail="no"
        print profile "|" desc "|" avail
    }
    '
)

###############################################################################
# SELECT PROFILE
###############################################################################

swaynag "${ARGS[@]}" &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

###############################################################################
# BUILD SINK MENU
###############################################################################

sleep 1

CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"

RANDOM_PICK=0

mapfile -t SINKS < <(
    pactl list short sinks |
    while read -r id sink rest; do

        label="$sink"

        case "$sink" in 
            *pro-output-[0-9]*)
                label="$(grep -o 'pro-output-[0-9]\+' <<< "$sink")"
                ;;
            *analog*)
                label="analog-stereo"
                ;;
            *hdmi*)
                label="HDMI"
                ;;
            *HyperX_Cloud_III*)
                label="Cloud III USB"
                ;;
            *Headphones*)
                label="Headphones"
                ;;
            *Speaker*)
                label="Speaker"
                ;;
            *HDMI1*)
                label="HDMI 1"
                ;;
            *HDMI2*)
                label="HDMI 2"
                ;;
            *HDMI3*)
                label="HDMI 3"
                ;;
        esac

        echo "$sink|$label"
    done
)

echo
echo "Available Audio Sinks"
echo

CURRENT_DESC="Current Sink"

for entry in "${SINKS[@]}"; do
    sink="${entry%%|*}"
    desc="${entry#*|}"

    if [[ "$sink" == "$CURRENT_SINK" ]]; then
        CURRENT_DESC="$desc"
        break
    fi
done

echo "[0] Keep current sink ($CURRENT_DESC)"

for i in "${!SINKS[@]}"; do
    desc="${SINKS[$i]#*|}"
    echo "[$((i + 1))] $desc"
done

EARPOD_RANDOM=$(( ${#SINKS[@]} + 1 ))
CLOUD3_RANDOM=$(( ${#SINKS[@]} + 2 ))

echo "[$EARPOD_RANDOM] Random EarPods"
echo "[$CLOUD3_RANDOM] Random Cloud III"
echo
read -rp "Select sink: " choice

if [[ "$choice" == "0" ]]; then
    echo "Keeping current sink."
else
    EARPOD_RANDOM=$(( ${#SINKS[@]} + 1 ))
    CLOUD3_RANDOM=$(( ${#SINKS[@]} + 2 ))

    if [[ "$choice" == "$EARPOD_RANDOM" ]]; then

        mapfile -t RANDOM_CANDIDATES < <(
            printf '%s\n' "${SINKS[@]}" |
            cut -d'|' -f1 |
            grep -E '^(earpods_fir.*|alsa_output.*Headphones.*)$'
        )

        sink="$(
            printf '%s\n' "${RANDOM_CANDIDATES[@]}" |
            shuf -n 1
        )"
 
        echo "Random EarPods sink selected."
        RANDOM_PICK=1

    elif [[ "$choice" == "$CLOUD3_RANDOM" ]]; then

        mapfile -t RANDOM_CANDIDATES < <(
            printf '%s\n' "${SINKS[@]}" |
            cut -d'|' -f1 |
            grep -E '^(cloud3_fir.*|alsa_output.*Headphones.*)$'
        )

        sink="$(
            printf '%s\n' "${RANDOM_CANDIDATES[@]}" |
            shuf -n 1
        )"

        echo "Random Cloud III sink selected."
        RANDOM_PICK=1

    else

        index=$((choice - 1))

        if (( index < 0 || index >= ${#SINKS[@]} )); then
            echo "Invalid selection."
            exit 1
        fi

        sink="${SINKS[$index]%%|*}"

    fi
    
    OLD_VOLUME="$(
        pactl get-sink-volume "$CURRENT_SINK" |
        grep -Po '[0-9]+%' |
        head -n1
    )"

    pactl set-default-sink "$sink" >>"$ACTION_LOG" 2>&1

    # If moving from a raw sink to a FIR sink, force Headphone to 100%
    if [[ "$sink" =~ ^(earpods_|cloud3_) ]]; then

        if [[ ! "$CURRENT_SINK" =~ ^(earpods_|cloud3_) ]]; then
            amixer -c 0 sget Headphone |
            grep -Po '[0-9]+(?=%)' |
            head -n1 > "$HEADPHONE_STATE"

            amixer -c 0 sset Headphone 100% >/dev/null 2>&1 || true
        fi

    # If moving from a FIR sink back to a raw sink, restore Headphone volume
    else

        if [[ "$CURRENT_SINK" =~ ^(earpods_|cloud3_) ]]; then

            if [[ -f "$HEADPHONE_STATE" ]]; then
                SAVED_VOL="$(cat "$HEADPHONE_STATE")"

                amixer -c 0 sset Headphone "${SAVED_VOL}%" >/dev/null 2>&1 || true

                rm -f "$HEADPHONE_STATE"
            fi
        fi

    fi

    # Apply previous volume AFTER all FIR/raw gain changes
    sleep 0.5

    pactl set-sink-volume "$sink" "$OLD_VOLUME" \
        >>"$ACTION_LOG" 2>&1 || true

    # 4. MIGRATE ALL RUNNING AUDIO STREAMS
    pactl list sink-inputs short | awk '{print $1}' | while read -r id; do
        pactl move-sink-input "$id" "$sink" >>"$ACTION_LOG" 2>&1
    done
fi

###############################################################################

if [[ -s "$ACTION_LOG" ]]; then
    cat "$ACTION_LOG"
    echo
fi

rm -f "$RESULT_FILE" "$ACTION_LOG"

pactl list cards | grep "Active Profile" || true

echo

if (( RANDOM_PICK )); then
    echo "The sink is a secret!"
else
    echo "Current sink:"
    pactl get-default-sink 2>/dev/null || true
fi
echo
read -n 1 -rsp "Press any key to close..."

