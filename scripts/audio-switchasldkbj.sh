#!/usr/bin/env bash
set -euo pipefail
SERVER="$HOME/.config/sway/scripts/network/camilladsp-server-sonobus.py"
STATE="$HOME/.local/state/sway/audio-switch"
mkdir -p "$STATE"
[[ -f "$SERVER" ]] || { echo "Missing paired server: $SERVER" >&2; exit 1; }
command -v swaynag >/dev/null
command -v jq >/dev/null
exec 9>"$STATE/audio-switch.lock"
flock -w 20 9 || { echo 'Audio switch is busy.' >&2; exit 1; }
SELECTION="$(mktemp "$STATE/selection.XXXXXXXX")"
rm -f "$SELECTION"
NAG_PID=''
cleanup() {
    if [[ -n "$NAG_PID" ]]; then kill "$NAG_PID" 2>/dev/null || true; wait "$NAG_PID" 2>/dev/null || true; fi
    rm -f -- "$SELECTION"
}
trap cleanup EXIT INT TERM
pause_close() {
    echo
    if [[ -t 0 ]]; then read -r -n 1 -s -p 'Press any key to close...' _ || true; echo; fi
}
menu() {
    local heading="$1"; shift
    local i n choice
    printf '\n%s\n' "$heading"
    for i in "${!MENU[@]}"; do printf '[%d] %s\n' "$((i+1))" "${MENU[i]}"; done
    while :; do
        read -rp "Select: " n || return 1
        [[ -n "$n" ]] || return 1
        if [[ "$n" =~ ^[0-9]+$ ]]; then
            choice=$((10#$n))
            if (( choice >= 1 && choice <= ${#MENU[@]} )); then PICK=$((choice-1)); return 0; fi
        fi
        echo 'Enter a listed number.'
    done
}
# Keep the original Swaynag first step: service and filter buttons followed by live cards.
TOPOLOGY="$(python3 "$SERVER" --output-topology)"
mapfile -t CARDS < <(jq -r '.cards[].name' <<<"$TOPOLOGY")
mapfile -t LABELS < <(jq -r '.cards[].label' <<<"$TOPOLOGY")
ARGS=(-t warning -y overlay -m 'Choose card, then playback profile')
ARGS+=(-z 'Audio services' "printf '%s' services > $(printf '%q' "$SELECTION")")
ARGS+=(-z 'CamillaDSP filters' "printf '%s' filters > $(printf '%q' "$SELECTION")")
for i in "${!CARDS[@]}"; do
    ARGS+=(-z "${LABELS[i]}" "printf '%s' $i > $(printf '%q' "$SELECTION")")
done
swaynag "${ARGS[@]}" & NAG_PID=$!
while [[ ! -e "$SELECTION" ]] && kill -0 "$NAG_PID" 2>/dev/null; do sleep .1; done
if [[ ! -e "$SELECTION" ]]; then echo 'No audio action selected.'; pause_close; exit 0; fi
CHOICE="$(cat "$SELECTION")"
kill "$NAG_PID" 2>/dev/null || true
wait "$NAG_PID" 2>/dev/null || true
NAG_PID=''
case "$CHOICE" in
services)
    echo; echo 'Audio services'
    MENU=('Audio stop / start (toggle)' 'Restart CamillaDSP' 'Restart SonoBus' 'Restart AirPlay' 'Restart VNC')
    menu 'Select service action' || { pause_close; exit 0; }
    if ((PICK==0)); then python3 "$SERVER" --audio-toggle
    else python3 "$SERVER" --restart-service "$PICK"; fi
    ;;
filters)
    PROFILES="$(python3 "$SERVER" --dsp-profiles)"
    INFO="$(python3 "$SERVER" --dsp-filter-info)"
    mapfile -t NAMES < <(jq -r '.[]' <<<"$PROFILES")
    ((${#NAMES[@]})) || { echo 'No listening filters generated.' >&2; exit 1; }
    MENU=()
    previous=''
    echo; echo 'CamillaDSP listening filter'
    for name in "${NAMES[@]}"; do
        group="$(jq -r --arg name "$name" '.[$name].group // "Other"' <<<"$INFO")"
        label="$(jq -r --arg name "$name" '.[$name].label // $name' <<<"$INFO")"
        if [[ "$group" != "$previous" ]]; then echo; echo "$group"; previous="$group"; fi
        MENU+=("$group · $label")
        printf '[%d] %s\n' "${#MENU[@]}" "$label"
    done
    while :; do
        read -rp 'Select CamillaDSP filter: ' n || { pause_close; exit 0; }
        [[ -n "$n" ]] || { pause_close; exit 0; }
        if [[ "$n" =~ ^[0-9]+$ ]]; then
            idx=$((10#$n-1))
            if ((idx>=0 && idx<${#NAMES[@]})); then break; fi
        fi
        echo 'Enter a listed number.'
    done
    python3 "$SERVER" --select-filter "${NAMES[idx]}"
    ;;
*)
    [[ "$CHOICE" =~ ^[0-9]+$ ]] && (( CHOICE < ${#CARDS[@]} )) || { echo 'Invalid card selection.' >&2; exit 1; }
    CARD="${CARDS[CHOICE]}"
    echo; echo "Card: ${LABELS[CHOICE]}"
    # ALSA has one hardware profile per card, unlike PipeWire's selectable profiles.
    MENU=('ALSA hardware PCM')
    menu 'Select card playback profile' || { pause_close; exit 0; }
    mapfile -t SINKS < <(jq -r --arg card "$CARD" '.cards[] | select(.name==$card) | .sinks[] | select(.cardName==$card) | .name' <<<"$TOPOLOGY")
    mapfile -t SINK_LABELS < <(jq -r --arg card "$CARD" '.cards[] | select(.name==$card) | .sinks[] | select(.cardName==$card) | .label' <<<"$TOPOLOGY")
    ((${#SINKS[@]})) || { echo 'No playback PCM on selected card.' >&2; exit 1; }
    MENU=("${SINK_LABELS[@]}")
    menu 'Select playback sink' || { pause_close; exit 0; }
    SINK="${SINKS[PICK]}"
    echo 'Output port: ALSA PCM has no separate PipeWire port.'
    REQUEST="$(jq -cn --arg card "$CARD" --arg sink "$SINK" '{card:$card,profile:"ALSA",sink:$sink,port:""}')"
    python3 "$SERVER" --apply-laptop-output "$REQUEST"
    ;;
esac
pause_close
