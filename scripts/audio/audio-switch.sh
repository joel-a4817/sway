#!/usr/bin/env bash
set -u

HOME_DIR="/home/joel"
STATE_DIR="$HOME_DIR/.local/state/sway/audio"

# Prevent overlapping selectors from changing the graph concurrently.
mkdir -p "$STATE_DIR"
# Lock lives under ~/.local/state/sway/audio; children close fd 9.
LOCK_FILE="$STATE_DIR/audio-switch-v3.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Audio switch is already running."
    exit 0
fi

ACTION_LOG="$STATE_DIR/audio-switch.log"
HOTSPOT_CONNECTION="AirPlay Direct"
RESULT_FILE="$STATE_DIR/audio-toggle-complete.$$"
ACTION_STARTED_FILE="$STATE_DIR/audio-toggle-started.$$"
CARD_SELECTION_FILE="$STATE_DIR/audio-card-selected.$$"
PROFILE_SELECTION_FILE="$STATE_DIR/audio-profile-selected.$$"
SWAYNAG_LOG="$STATE_DIR/audio-switch-swaynag.$$"
MEDIA_RESUME_FILE="$STATE_DIR/audio-media-resume.$$"
MEDIA_CAPTURED_FILE="$STATE_DIR/audio-media-captured.$$"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
printf '\n[%s] audio-switch pid=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$$" >>"$ACTION_LOG"
FINAL_PAUSE_REACHED=0
EXIT_HANDLER_RUNNING=0

cleanup() { rm -f "$RESULT_FILE" "$ACTION_STARTED_FILE" "$CARD_SELECTION_FILE" "$PROFILE_SELECTION_FILE" "$SWAYNAG_LOG" "$MEDIA_RESUME_FILE" "$MEDIA_CAPTURED_FILE"; }
pause_before_close() {
    FINAL_PAUSE_REACHED=1
    flock -u 9 2>/dev/null || true
    exec 9>&-
    echo
    if [[ -t 0 && -r /dev/tty && -w /dev/tty ]]; then
        read -n 1 -r -s -p "Press any key to close..." </dev/tty || true
        echo >/dev/tty
    else
        echo "No controlling terminal is available."
    fi
}
handle_script_exit() {
    local status=$?
    (( EXIT_HANDLER_RUNNING )) && return
    EXIT_HANDLER_RUNNING=1
    trap - EXIT INT TERM HUP QUIT
    if (( status != 0 && ! FINAL_PAUSE_REACHED )); then
        echo; echo "Audio script exited unexpectedly."; echo "Exit status: $status"
        [[ -s "$ACTION_LOG" ]] && { echo; echo "Action log:"; echo "$ACTION_LOG"; echo; tail -n 45 "$ACTION_LOG"; }
        pause_before_close
    fi
    cleanup
    flock -u 9 2>/dev/null || true
    exec 9>&-
    builtin exit "$status"
}
trap handle_script_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap 'exit 131' QUIT

pulse_ready() {
    pactl info >/dev/null 2>&1
}

wait_for_pulse() {
    local _
    for _ in {1..100}; do
        pulse_ready && return 0
        sleep 0.1
    done
    return 1
}

sink_exists() {
    local wanted="$1"
    pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fqx "$wanted"
}

wait_for_sink() {
    local wanted="$1" _
    for _ in {1..100}; do
        sink_exists "$wanted" && return 0
        sleep 0.1
    done
    return 1
}

set_card_profile_stable() {
    local card="$1" profile="$2" _
    for _ in {1..30}; do
        wait_for_pulse || continue
        if pactl set-card-profile "$card" "$profile" >>"$ACTION_LOG" 2>&1; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

move_application_inputs_to() {
    local wanted="$1"
    local id
    local block

    while read -r id _; do
        [[ -n "$id" ]] || continue
        block="$(pactl list sink-inputs 2>/dev/null | awk -v wanted="$id" '
            /^Sink Input #[0-9]+/ {
                current=$3
                sub(/^#/, "", current)
                selected=(current==wanted)
            }
            selected { print }
        ')"
        pactl move-sink-input "$id" "$wanted" \
            >>"$ACTION_LOG" 2>&1 || true
    done < <(pactl list short sink-inputs 2>/dev/null || true)
}


# Local laptop-only CamillaDSP state. The phone server remains independent.
fail() { printf 'Error: %s\n' "$*" | tee -a "$ACTION_LOG" >&2; exit 1; }
CAMILLA="$(command -v camilladsp || true)"
PROFILES_DIR="$HOME_DIR/Documents/prefs/audio/camilladsp"
REMOTE_STATE="$STATE_DIR/camilladsp-webremote"
mkdir -p "$REMOTE_STATE"
PIDFILE="$REMOTE_STATE/camilladsp.pid"
ACTIVE="$REMOTE_STATE/active-profile"
MONPID="$REMOTE_STATE/local-monitor.pid"
SERVERPID="$REMOTE_STATE/web-server.pid"
ROUTE_FILE="$REMOTE_STATE/local-output-route.json"
LOCAL_CAMILLA_LOG="$STATE_DIR/camilladsp-local.log"
read_pid() { [[ -f "$1" ]] && cat "$1" || true; }
valid_pid() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] && kill -0 "$1" 2>/dev/null || return 1
    [[ "$(ps -o stat= -p "$1" 2>/dev/null)" != Z* ]]
}
# The webremote can own CamillaDSP independently. Do not kill its process.
ensure_local_owner() {
    local server_pid
    server_pid="$(read_pid "$SERVERPID")"
    if valid_pid "$server_pid" && [[ -r "/proc/$server_pid/cmdline" ]] &&
        tr '\0' ' ' <"/proc/$server_pid/cmdline" | grep -Fq 'camilladsp-server-sonobus.py'; then
        fail 'The phone webremote currently owns CamillaDSP. Stop it before local switching; this script never connects to port 8766.'
    fi
}
stop_monitor() {
    local pid pgid cmd
    pid="$(read_pid "$MONPID")"
    if valid_pid "$pid"; then
        cmd="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
        [[ "$cmd" == *camilladsp_output_shared* ]] || fail "Monitor PID $pid is not our local monitor; refusing to kill it"
        pgid="$(ps -o pgid= -p "$pid" | tr -d ' ')"
        if [[ "$pgid" == "$pid" ]]; then
            kill -TERM -- "-$pid" 2>/dev/null || true
        else
            kill -TERM "$pid" 2>/dev/null || true
        fi
        for ((i=0;i<30;i++)); do valid_pid "$pid" || break; sleep 0.1; done
    fi
    rm -f -- "$MONPID"
}
stop_camilla() {
    local pid comm
    pid="$(read_pid "$PIDFILE")"
    if valid_pid "$pid"; then
        comm="$(cat "/proc/$pid/comm" 2>/dev/null || true)"
        [[ "$comm" == camilladsp ]] || fail "PID file points to another process ($pid: $comm); refusing to kill it"
        kill -TERM "$pid" 2>/dev/null || true
        for ((i=0;i<30;i++)); do valid_pid "$pid" || break; sleep 0.1; done
        valid_pid "$pid" && fail 'CamillaDSP did not stop; refusing to start a second instance'
    fi
    rm -f -- "$PIDFILE"
}
start_camilla() {
    local profile="$1" pid
    "$CAMILLA" --check "$profile" >>"$ACTION_LOG" 2>&1 || return 1
    "$CAMILLA" "$profile" >>"$LOCAL_CAMILLA_LOG" 2>&1 </dev/null 9>&- &
    pid=$!
    printf '%s\n' "$pid" >"$PIDFILE"
    for ((i=0;i<20;i++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f -- "$PIDFILE"
            tail -n 25 "$LOCAL_CAMILLA_LOG" >&2
            return 1
        fi
        sleep 0.1
    done
    printf '%s\n' "$(basename "$profile")" >"$ACTIVE"
}

camilla_sink_exists() { sink_exists camilladsp; }
ensure_camilla_sink() {
    if ! camilla_sink_exists; then
        systemctl --user start camilladsp-system-audio.service >>"$ACTION_LOG" 2>&1 || return 1
        wait_for_sink camilladsp || return 1
    fi
    camilla_sink_exists
}
card_sinks() {
    local card="$1" graph
    graph="$(pw-dump 2>/dev/null)" || return 1
    jq -r --arg card "$card" '
      [.[] | select(.type == "PipeWire:Interface:Device" and .info.props."device.name" == $card) | (.id | tostring)] as $ids |
      .[] | select(.type == "PipeWire:Interface:Node" and .info.props."media.class" == "Audio/Sink" and
        .info.props."device.api" == "alsa" and .info.props."factory.name" == "api.alsa.pcm.sink") |
      (.info.props."device.id" // "" | tostring) as $id |
      select(($ids | index($id)) != null) | .info.props."node.name" // empty
    ' <<<"$graph" | sort -u
}
start_local_monitor() {
    local sink="$1" pid
    setsid bash -c '
      set -o pipefail
      arecord -q -D camilladsp_output_shared -r 96000 -f S32_LE -c 2 -t raw |
        pacat --playback --device="$1" --rate=96000 --format=s32le --channels=2
    ' _ "$sink" >>"$ACTION_LOG" 2>&1 </dev/null 9>&- &
    pid=$!
    printf '%s\n' "$pid" >"$MONPID"
    sleep 0.3
    if ! valid_pid "$pid"; then rm -f "$MONPID"; return 1; fi
}

mpris_playback_status() {
    local service="$1"

    busctl --user get-property \
        "$service" \
        /org/mpris/MediaPlayer2 \
        org.mpris.MediaPlayer2.Player \
        PlaybackStatus \
        2>/dev/null |
    awk -F'"' 'NF >= 2 {print $2; exit}'
}

mpv_is_playing() {
    local reply

    [[ -S /tmp/mpvsocket ]] || return 1
    command -v socat >/dev/null 2>&1 || return 1

    reply="$(
        printf '%s\n' '{"command":["get_property","pause"]}' |
            socat -T 1 - /tmp/mpvsocket 2>/dev/null |
            head -n1
    )"

    grep -Eq '"data"[[:space:]]*:[[:space:]]*false' <<<"$reply"
}

capture_media_resume_state() {
    local service
    local status

    [[ -e "$MEDIA_CAPTURED_FILE" ]] && return 0

    : >"$MEDIA_RESUME_FILE"

    if mpv_is_playing; then
        printf '%s\n' '__MPV__' >>"$MEDIA_RESUME_FILE"
    fi

    if command -v busctl >/dev/null 2>&1; then
        while read -r service; do
            [[ -n "$service" ]] || continue
            status="$(mpris_playback_status "$service" || true)"
            if [[ "$status" == "Playing" ]]; then
                printf '%s\n' "$service" >>"$MEDIA_RESUME_FILE"
            fi
        done < <(
            busctl --user --no-pager --no-legend list 2>/dev/null |
            awk '$1 ~ /^org\.mpris\.MediaPlayer2\./ {print $1}'
        )
    fi

    touch "$MEDIA_CAPTURED_FILE"
}

pause_active_media() {
    local service

    # Capture only once, before the first normalization. Later normalization
    # passes keep media paused without changing what will be resumed.
    capture_media_resume_state

    if [[ -S /tmp/mpvsocket ]] && command -v socat >/dev/null 2>&1; then
        printf 'set pause yes\n' |
            socat - /tmp/mpvsocket >>"$ACTION_LOG" 2>&1 || true
    fi

    if command -v busctl >/dev/null 2>&1; then
        while read -r service; do
            [[ -n "$service" ]] || continue

            if busctl --user call \
                "$service" \
                /org/mpris/MediaPlayer2 \
                org.mpris.MediaPlayer2.Player \
                Pause \
                >>"$ACTION_LOG" 2>&1; then
                printf 'Paused MPRIS player: %s\n' "$service" >>"$ACTION_LOG"
            else
                printf 'Failed to pause MPRIS player: %s\n' "$service" >>"$ACTION_LOG"
            fi
        done < <(
            busctl --user --no-pager --no-legend list 2>/dev/null |
            awk '$1 ~ /^org\.mpris\.MediaPlayer2\./ {print $1}'
        )
    fi
}

resume_previously_playing_media() {
    local entry

    [[ -r "$MEDIA_RESUME_FILE" ]] || return 0

    while IFS= read -r entry; do
        [[ -n "$entry" ]] || continue

        if [[ "$entry" == '__MPV__' ]]; then
            if [[ -S /tmp/mpvsocket ]] && command -v socat >/dev/null 2>&1; then
                printf 'set pause no\n' |
                    socat - /tmp/mpvsocket >>"$ACTION_LOG" 2>&1 || true
            fi
            continue
        fi

        if command -v busctl >/dev/null 2>&1; then
            if busctl --user call \
                "$entry" \
                /org/mpris/MediaPlayer2 \
                org.mpris.MediaPlayer2.Player \
                Play \
                >>"$ACTION_LOG" 2>&1; then
                printf 'Resumed MPRIS player: %s\n' "$entry" >>"$ACTION_LOG"
            else
                printf 'Warning: failed to resume MPRIS player: %s\n' \
                    "$entry" >>"$ACTION_LOG"
            fi
        fi
    done <"$MEDIA_RESUME_FILE"

    rm -f "$MEDIA_RESUME_FILE" "$MEDIA_CAPTURED_FILE"
}

normalize_audio_volumes() {
    local restore_volume="${1:-}" restore_sink="${2:-}" sof_card hyperx_card sink
    pause_active_media
    sof_card="$(aplay -l 2>/dev/null | awk -F': ' '/sof|SOF/ {print $1; exit}' | grep -o '[0-9]\+' || true)"
    [[ -n "$sof_card" ]] && amixer -c "$sof_card" sset Headphone 100% >/dev/null 2>&1 || true
    hyperx_card="$(aplay -l 2>/dev/null | awk -F': ' '/HyperX Cloud III/ {print $1; exit}' | grep -o '[0-9]\+' || true)"
    [[ -n "$hyperx_card" ]] && amixer -c "$hyperx_card" sset 'Speaker Volume' 100% unmute >/dev/null 2>&1 || true
    while read -r sink; do
        [[ -n "$sink" ]] || continue
        pactl set-sink-mute "$sink" 0 >/dev/null 2>&1 || true
        pactl set-sink-volume "$sink" 100% >/dev/null 2>&1 || true
    done < <(pactl list short sinks 2>/dev/null | awk '{print $2}')
    if [[ -n "$restore_volume" && -n "$restore_sink" ]] &&
       pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fqx "$restore_sink"; then
        pactl set-sink-volume "$restore_sink" "$restore_volume" >/dev/null 2>&1 || true
    fi
}
export -f mpris_playback_status mpv_is_playing capture_media_resume_state pause_active_media resume_previously_playing_media normalize_audio_volumes

ORIGINAL_SINK="$(pactl get-default-sink 2>/dev/null || true)"
ORIGINAL_VOLUME=""
if [[ -n "$ORIGINAL_SINK" ]] && pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fqx "$ORIGINAL_SINK"; then
    ORIGINAL_VOLUME="$(pactl get-sink-volume "$ORIGINAL_SINK" 2>/dev/null | grep -Po '[0-9]+%' | head -n1)"
fi

export RESULT_FILE ACTION_STARTED_FILE ACTION_LOG CARD_SELECTION_FILE PROFILE_SELECTION_FILE MEDIA_RESUME_FILE MEDIA_CAPTURED_FILE ORIGINAL_VOLUME ORIGINAL_SINK

mapfile -t CARDS < <(pactl list cards 2>/dev/null | awk '
function output_card(){if(card!=""){if(description=="")description=card;print card "|" description}}
/^Card #[0-9]+/{output_card();card="";description="";next}
/^[[:space:]]*Name:/{card=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",card);next}
/^[[:space:]]*device\.description[[:space:]]*=/{if(description==""){description=$0;sub(/^[[:space:]]*device\.description[[:space:]]*=[[:space:]]*/,"",description);gsub(/^"|"$/,"",description)}}
END{output_card()}')

if ((${#CARDS[@]})); then
    printf '%s\n' "${CARDS[@]}" >"$STATE_DIR/audio-cards-last.txt"
elif [[ -s "$STATE_DIR/audio-cards-last.txt" ]]; then
    mapfile -t CARDS <"$STATE_DIR/audio-cards-last.txt"
fi
card_display_label() {
    local card="$1" description="$2" label="$2"
    case "$card $description" in
        *HyperX_Cloud_III*|*"HyperX Cloud III"*) label="HyperX Cloud III" ;;
        *sof*|*SOF*|*"sof-hda-dsp"*|*"Built-in Audio"*|*Chipset*) label="Built-in Audio" ;;
        *HDMI*|*hdmi*) [[ "$description" == *HDMI* ]] || label="$description HDMI" ;;
    esac
    printf '%s\n' "$label"
}
profile_display_label() {
    local card="$1" profile="$2" description="$3" label="$3" hdmi
    if [[ "$description" == *Headphone* ]]; then label="Headphones"
    elif [[ "$description" == *Speaker* ]]; then label="Speaker"
    elif [[ "$description" == *"Pro Audio"* ]]; then label="Pro Audio"
    elif [[ "$description" == *HDMI* ]]; then
        hdmi=1; [[ "$description" == *"HDMI 2"* ]] && hdmi=2; [[ "$description" == *"HDMI 3"* ]] && hdmi=3
        label="HDMI $hdmi"; [[ "$description" == *"7.1"* ]] && label+=" 7.1"; [[ "$description" == *"5.1"* ]] && label+=" 5.1"; [[ "$description" == *Input* ]] && label+=" + Mic"
    elif [[ "$profile" == "input:analog-stereo" ]]; then label="Mic Only"
    elif [[ "$description" == *Analog* ]]; then label="Analog"; [[ "$description" == *Input* || "$description" == *Duplex* ]] && label+=" + Mic"
    fi
    if [[ "$card" == *HyperX_Cloud_III* ]]; then
        case "$profile" in
            output:analog-stereo) label="Cloud III Analog" ;;
            output:analog-stereo+input:mono-fallback) label="Cloud III Analog + Mic" ;;
            output:iec958-stereo) label="Cloud III Digital" ;;
            output:iec958-stereo+input:mono-fallback) label="Cloud III Digital + Mic" ;;
            input:mono-fallback) label="Cloud III Mic" ;;
            pro-audio) label="Cloud III Pro Audio" ;;
        esac
    fi
    printf '%s\n' "$label"
}
sink_display_label() {
    local sink="$1" label="$1"

    case "$sink" in
        *usb-HP__Inc_HyperX_Cloud_III*.analog-stereo) label="Cloud III Analog" ;;
        *usb-HP__Inc_HyperX_Cloud_III*.iec958-stereo) label="Cloud III Digital" ;;
        *platform-snd_aloop.0.analog-stereo) label="ALSA Loopback" ;;
        *HiFi__Headphones__sink*) label="Headphones" ;;
        *HiFi__Speaker__sink*) label="Speaker" ;;
        *HiFi__HDMI1__sink*) label="HDMI 1" ;;
        *HiFi__HDMI2__sink*) label="HDMI 2" ;;
        *HiFi__HDMI3__sink*) label="HDMI 3" ;;
        *pro-output-[0-9]*) label="${sink##*.}" ;;
        *hdmi*) label="HDMI" ;;
    esac

    printf '%s\n' "$label"
}

close_mpv_windows() {
    local _

    pkill -TERM -x mpv >>"$ACTION_LOG" 2>&1 || true

    for _ in {1..30}; do
        if ! pgrep -x mpv >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.1
    done

    pkill -KILL -x mpv >>"$ACTION_LOG" 2>&1 || true

    for _ in {1..20}; do
        if ! pgrep -x mpv >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.1
    done

    echo "Warning: one or more mpv processes are still running." \
        >>"$ACTION_LOG"

    return 1
}
hotspot_active() { nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fqx "$HOTSPOT_CONNECTION"; }

ARGS=(-t warning -y overlay -m "Audio Device Select")

ARGS+=(
    -z "Audio Stop"
    "touch '$ACTION_STARTED_FILE'; printf '%s\n' stop-audio >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
)

if hotspot_active; then
    ARGS+=(
        -z "Stop AirPlay Hotspot [active]"
        "touch '$ACTION_STARTED_FILE'; printf '%s\n' toggle-hotspot >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
    )
else
    ARGS+=(
        -z "Start AirPlay Hotspot [inactive]"
        "touch '$ACTION_STARTED_FILE'; printf '%s\n' toggle-hotspot >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
    )
fi

for i in "${!CARDS[@]}"; do
    card="${CARDS[$i]%%|*}"; description="${CARDS[$i]#*|}"
    ARGS+=( -z "$(card_display_label "$card" "$description")" "touch '$ACTION_STARTED_FILE'; printf '%s\n' '$i' >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
done

swaynag "${ARGS[@]}" >"$SWAYNAG_LOG" 2>&1 9>&- &
SWAYNAG_PID=$!
while [[ ! -f "$RESULT_FILE" ]]; do
    if kill -0 "$SWAYNAG_PID" 2>/dev/null; then sleep 0.1; continue; fi
    wait "$SWAYNAG_PID" 2>/dev/null; SWAYNAG_STATUS=$?
    if [[ -f "$ACTION_STARTED_FILE" ]]; then while [[ ! -f "$RESULT_FILE" ]]; do sleep 0.1; done; break; fi
    if [[ -s "$SWAYNAG_LOG" ]]; then echo; echo "Swaynag exited before completing the action."; echo; cat "$SWAYNAG_LOG"; exit "${SWAYNAG_STATUS:-1}"; fi
    echo; echo "No audio action was selected."; pause_before_close; exit 0
done
kill "$SWAYNAG_PID" 2>/dev/null || true
wait "$SWAYNAG_PID" 2>/dev/null || true
[[ -f "$CARD_SELECTION_FILE" ]] || { echo "No audio action was selected."; exit 1; }
SELECTED_CARD_VALUE="$(cat "$CARD_SELECTION_FILE")"

if [[ "$SELECTED_CARD_VALUE" == stop-audio ]]; then
    echo "Stopping all audio..." | tee -a "$ACTION_LOG"
    # Marker is shared with the webremote; it must survive its restart.
    server_pid="$(read_pid "$SERVERPID")"
    if valid_pid "$server_pid" &&
       tr '\0' ' ' <"/proc/$server_pid/cmdline" | grep -Fq 'camilladsp-server-sonobus.py'; then
        if [[ "$(cat "$STATE_DIR/audio-stop-capable.pid" 2>/dev/null || true)" != "$server_pid" ]]; then
            echo 'Webremote is still running old code; restart it with the patched server before Audio Stop.'
            exit 1
        fi
        rm -f -- "$STATE_DIR/audio-stop-complete"
        : >"$STATE_DIR/audio-stopped"
        kill -USR1 "$server_pid" || { echo 'Could not signal webremote to stop audio.'; exit 1; }
        for _ in {1..100}; do
            [[ -f "$STATE_DIR/audio-stop-complete" ]] && break
            sleep 0.1
        done
        [[ -f "$STATE_DIR/audio-stop-complete" ]] || { echo 'Webremote did not confirm audio stopped.'; exit 1; }
    fi
    : >"$STATE_DIR/audio-stopped"
    stop_monitor
    stop_camilla
    close_mpv_windows || true
    # SonoBus may be running without the webremote. Stop those instances too.
    for pid in $(pgrep -x sonobus 2>/dev/null; pgrep -x SonoBus 2>/dev/null); do
        [[ "$pid" =~ ^[0-9]+$ ]] && kill -TERM "$pid" 2>/dev/null || true
    done
    systemctl stop shairport-sync.service nqptp.service >>"$ACTION_LOG" 2>&1 || true
    systemctl --user stop camilladsp-system-audio.service >>"$ACTION_LOG" 2>&1 || true
    systemctl --user stop wireplumber.service pipewire-pulse.service pipewire.service pipewire-pulse.socket pipewire.socket >>"$ACTION_LOG" 2>&1 || true
    echo "Audio stopped. The webremote stays open; select a source or profile to resume."
    pause_before_close
    exit 0
fi
if [[ "$SELECTED_CARD_VALUE" == toggle-hotspot ]]; then
    if hotspot_active; then nmcli connection down "$HOTSPOT_CONNECTION" >>"$ACTION_LOG" 2>&1; echo "AirPlay hotspot stopped."
    else nmcli connection up "$HOTSPOT_CONNECTION" >>"$ACTION_LOG" 2>&1; echo "AirPlay hotspot started."; fi
    pause_before_close; exit 0
fi

[[ "$SELECTED_CARD_VALUE" =~ ^[0-9]+$ ]] && (( SELECTED_CARD_VALUE < ${#CARDS[@]} )) || { echo "Invalid audio device selection."; exit 1; }
if ! pulse_ready; then
    systemctl --user start pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service >>"$ACTION_LOG" 2>&1 || { echo "PipeWire failed to start; see $ACTION_LOG"; exit 1; }
    wait_for_pulse || { echo 'PipeWire did not become ready.'; exit 1; }
fi
SELECTED_CARD="${CARDS[$SELECTED_CARD_VALUE]%%|*}"
SELECTED_CARD_DESCRIPTION="${CARDS[$SELECTED_CARD_VALUE]#*|}"
SELECTED_CARD_LABEL="$(card_display_label "$SELECTED_CARD" "$SELECTED_CARD_DESCRIPTION")"

CURRENT_SINK="$(pactl get-default-sink 2>/dev/null || true)"
normalize_audio_volumes "$ORIGINAL_VOLUME" "$CURRENT_SINK"

mapfile -t PROFILES < <(pactl list cards | awk -v target="$SELECTED_CARD" '
/^[[:space:]]*Name:/{current=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",current);selected=(current==target);in_profiles=0;next}
selected&&/^[[:space:]]*Profiles:/{in_profiles=1;next}
selected&&in_profiles&&/^[[:space:]]*(Active Profile|Active Port|Ports):/{in_profiles=0;next}
selected&&in_profiles&&index($0,"(sinks:"){line=$0;sub(/^[[:space:]]*/,"",line);separator=index(line,": ");if(!separator)next;profile=substr(line,1,separator-1);description=substr(line,separator+2);if(profile=="off")next;availability="unknown";if(line~/available:[[:space:]]*yes/)availability="yes";else if(line~/available:[[:space:]]*no/)availability="no";sub(/[[:space:]]+\(sinks:.*/,"",description);print profile "|" description "|" availability}')
((${#PROFILES[@]})) || { echo "No profiles were found for: $SELECTED_CARD"; exit 1; }
get_active_profile() { pactl list cards | awk -v target="$SELECTED_CARD" '/^[[:space:]]*Name:/{x=$0;sub(/^[[:space:]]*Name:[[:space:]]*/,"",x);s=(x==target);next}s&&/^[[:space:]]*Active Profile:/{x=$0;sub(/^[[:space:]]*Active Profile:[[:space:]]*/,"",x);print x;exit}'; }
ACTIVE_PROFILE="$(get_active_profile)"; ACTIVE_PROFILE_LABEL="$ACTIVE_PROFILE"
for entry in "${PROFILES[@]}"; do profile="${entry%%|*}"; remainder="${entry#*|}"; description="${remainder%%|*}"; [[ "$profile" == "$ACTIVE_PROFILE" ]] && { ACTIVE_PROFILE_LABEL="$(profile_display_label "$SELECTED_CARD" "$profile" "$description")"; break; }; done

echo; echo "Selected Audio Device"; echo; echo "$SELECTED_CARD_LABEL"; echo; echo "Profiles"; echo
[[ -n "$ACTIVE_PROFILE" ]] && echo "[0] Keep current profile ($ACTIVE_PROFILE_LABEL)" || echo "[0] Keep current profile"
for i in "${!PROFILES[@]}"; do
    entry="${PROFILES[$i]}"; profile="${entry%%|*}"; remainder="${entry#*|}"; description="${remainder%%|*}"; availability="${remainder##*|}"
    label="$(profile_display_label "$SELECTED_CARD" "$profile" "$description")"
    [[ "$availability" == no ]] && label+=" (unavailable)"
    echo "[$((i+1))] $label"
done
read -rp "Select profile: " profile_choice
SELECTED_PROFILE="$ACTIVE_PROFILE"; SELECTED_PROFILE_LABEL="$ACTIVE_PROFILE_LABEL"
if [[ "$profile_choice" != 0 ]]; then
    [[ "$profile_choice" =~ ^[0-9]+$ ]] || { echo "Invalid profile selection."; exit 1; }
    profile_index=$((10#$profile_choice-1)); (( profile_index >= 0 && profile_index < ${#PROFILES[@]} )) || { echo "Invalid profile selection."; exit 1; }
    profile_entry="${PROFILES[$profile_index]}"; SELECTED_PROFILE="${profile_entry%%|*}"; remainder="${profile_entry#*|}"; description="${remainder%%|*}"; availability="${remainder##*|}"
    [[ "$availability" != no ]] || { echo "That profile is currently marked unavailable by PipeWire."; exit 1; }
    SELECTED_PROFILE_LABEL="$(profile_display_label "$SELECTED_CARD" "$SELECTED_PROFILE" "$description")"
    set_card_profile_stable "$SELECTED_CARD" "$SELECTED_PROFILE" || { echo "Failed to set profile '$SELECTED_PROFILE'. See: $ACTION_LOG"; exit 1; }
    printf '%s\n' "$SELECTED_PROFILE_LABEL" >"$PROFILE_SELECTION_FILE"
    PROFILE_OK=0; for _ in {1..50}; do [[ "$(get_active_profile)" == "$SELECTED_PROFILE" ]] && { PROFILE_OK=1; break; }; sleep 0.1; done
    (( PROFILE_OK )) || { echo "Warning: profile did not settle as '$SELECTED_PROFILE'." >>"$ACTION_LOG"; echo "Warning: profile switch did not settle. See: $ACTION_LOG"; }
fi

# Like camilladsp-server-sonobus.py, show every eligible real output after
# choosing the card/profile. A different card can own the selected sink.
# Loopback cards remain visible in Swaynag, but their loopback sinks cannot
# be final monitor targets (that would create a feedback loop).
real_sinks() {
    jq -r '.[] |
        (.name // "") as $name |
        ((.properties // {}) | [."device.description", ."node.description", ."node.nick", ."media.name"] | map(. // "") | join(" ") | ascii_downcase) as $label |
        select($name != "" and
          ($name | ascii_downcase | startswith("earpods_") or startswith("cloud3_") or startswith("cmf-buds-pro-2_") or
            contains("snd_aloop") or contains("loopback") or contains("camilladsp") or contains("sonobus") or contains("silent output") or contains("filter-chain") or contains("null-sink") | not) and
          ($label | contains("loopback") or contains("camilladsp") or contains("sonobus") or contains("silent output") or contains("filter-chain") or contains("null sink") | not)) |
        $name' <<<"$SINKS_JSON" | sort -u
}
SINKS_JSON="$(pactl --format=json list sinks)" || { echo 'Could not list sinks.'; exit 1; }
mapfile -t PHYSICAL_SINKS < <(real_sinks)
((${#PHYSICAL_SINKS[@]})) || { echo 'No usable playback sinks are available.'; exit 1; }
SAVED_CARD="$(jq -r '.card // ""' "$ROUTE_FILE" 2>/dev/null || true)"
SAVED_SINK="$(jq -r '.sink // ""' "$ROUTE_FILE" 2>/dev/null || true)"
CURRENT_PHYSICAL=""
if [[ -n "$SAVED_SINK" ]]; then
    for sink in "${PHYSICAL_SINKS[@]}"; do
        [[ "$sink" == "$SAVED_SINK" ]] && CURRENT_PHYSICAL="$sink"
    done
fi
if [[ -z "$CURRENT_PHYSICAL" ]]; then
    default="$(pactl get-default-sink 2>/dev/null || true)"
    for sink in "${PHYSICAL_SINKS[@]}"; do
        [[ "$sink" == "$default" ]] && CURRENT_PHYSICAL="$sink"
    done
fi
printf '\nPlayback outputs (all cards)\n' 
printf '[0] Keep current output (%s)\n' "$(sink_display_label "${CURRENT_PHYSICAL:-none}")"
for i in "${!PHYSICAL_SINKS[@]}"; do
    sink="${PHYSICAL_SINKS[$i]}"
    label="$(jq -r --arg name "$sink" '.[] | select(.name == $name) | .description // .name' <<<"$SINKS_JSON" | head -n1)"
    printf '[%d] %s\n' "$((i+1))" "$(sink_display_label "$sink")"
done
read -rp 'Select physical output: ' physical_choice
[[ "$physical_choice" =~ ^[0-9]+$ ]] || { echo 'Invalid output selection.'; exit 1; }
if [[ "$physical_choice" == 0 ]]; then
    [[ -n "$CURRENT_PHYSICAL" ]] || { echo 'No current output for this card; select a numbered output.'; exit 1; }
    PHYSICAL_SINK="$CURRENT_PHYSICAL"
else
    index=$((10#$physical_choice-1))
    ((index >= 0 && index < ${#PHYSICAL_SINKS[@]})) || { echo 'Invalid output selection.'; exit 1; }
    PHYSICAL_SINK="${PHYSICAL_SINKS[$index]}"
fi
wait_for_sink "$PHYSICAL_SINK" || { echo "Output disappeared: $PHYSICAL_SINK"; exit 1; }
# The server exposes sink ports as well; honor the saved port when available.
mapfile -t OUTPUT_PORTS < <(jq -r --arg name "$PHYSICAL_SINK" '
  .[] | select(.name == $name) | (.ports // []) |
  if type == "array" then .[] | select((.availability // .available // "unknown" | tostring | ascii_downcase) != "no") | .name
  else to_entries[] | select((.value.availability // .value.available // "unknown" | tostring | ascii_downcase) != "no") | .key end' <<<"$SINKS_JSON")
SELECTED_PORT=""
if ((${#OUTPUT_PORTS[@]})); then
    saved_port="$(jq -r '.port // ""' "$ROUTE_FILE" 2>/dev/null || true)"
    for port in "${OUTPUT_PORTS[@]}"; do
        [[ "$port" == "$saved_port" ]] && SELECTED_PORT="$port"
    done
    [[ -n "$SELECTED_PORT" ]] || SELECTED_PORT="${OUTPUT_PORTS[0]}"
    if ((${#OUTPUT_PORTS[@]} > 1)); then
        printf '\nOutput port\n[0] Keep current (%s)\n' "$SELECTED_PORT"
        for i in "${!OUTPUT_PORTS[@]}"; do
            port_label="$(jq -r --arg sink "$PHYSICAL_SINK" --arg port "${OUTPUT_PORTS[$i]}" '.[] | select(.name == $sink) | (.ports // []) | if type == "array" then .[] | select(.name == $port) | .description // .name else .[$port].description // $port end' <<<"$SINKS_JSON")"
            printf '[%d] %s\n' "$((i+1))" "${port_label:-${OUTPUT_PORTS[$i]}}"
        done
        read -rp 'Select output port: ' port_choice
        [[ "$port_choice" =~ ^[0-9]+$ ]] || { echo 'Invalid port.'; exit 1; }
        if [[ "$port_choice" != 0 ]]; then
            index=$((10#$port_choice-1))
            ((index >= 0 && index < ${#OUTPUT_PORTS[@]})) || { echo 'Invalid port.'; exit 1; }
            SELECTED_PORT="${OUTPUT_PORTS[$index]}"
        fi
    fi
    pactl set-sink-port "$PHYSICAL_SINK" "$SELECTED_PORT" >>"$ACTION_LOG" 2>&1 || { echo 'Failed to set output port.'; exit 1; }
fi

# One local CamillaDSP profile menu. CMF profiles remain available to the
# webremote but are deliberately excluded from this laptop selector.
[[ -n "$CAMILLA" && -d "$PROFILES_DIR" ]] || { echo 'CamillaDSP executable or profile directory missing.'; exit 1; }
CURRENT_DSP="$(cat "$ACTIVE" 2>/dev/null || true)"
short_profile_name() {
    local file="$1" stem
    case "$file" in
        00-filterless.y*ml) printf 'Filterless\n'; return ;;
        *-cloud3-*.y*ml) printf 'Cloud III Anechoic\n'; return ;;
        *-earpods-*.y*ml) stem="${file#*-earpods-}" ;;
        *) printf '%s\n' "$file"; return ;;
    esac
    stem="${stem%.yaml}"; stem="${stem%.yml}"
    stem="${stem//-/ }"
    printf 'EarPods %s\n' "$stem" | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2); print}'
}
# Sort by the generated prefix: 00 filterless, 01 anechoics, then the
# EarPods environments. Never include CMF in the local selector.
mapfile -t DSP_PROFILES < <(find "$PROFILES_DIR" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -printf '%f\n' |
    grep -E '^(00-filterless|[0-9][0-9]-(earpods|cloud3)-).*\.ya?ml$' | sort -u)
((${#DSP_PROFILES[@]})) || { echo 'No local CamillaDSP profiles found.'; exit 1; }
CURRENT_VALID=0
for dsp in "${DSP_PROFILES[@]}"; do
    [[ "$dsp" == "$CURRENT_DSP" ]] && CURRENT_VALID=1
done
echo; echo 'CamillaDSP profiles'; echo
if ((CURRENT_VALID)); then
    printf '[0] Keep current (%s)\n' "$(short_profile_name "$CURRENT_DSP")"
else
    echo '[0] No current local profile; choose a number'
fi
for i in "${!DSP_PROFILES[@]}"; do
    printf '[%d] %s\n' "$((i+1))" "$(short_profile_name "${DSP_PROFILES[$i]}")"
done
read -rp 'Select CamillaDSP profile: ' dsp_choice
[[ "$dsp_choice" =~ ^[0-9]+$ ]] || { echo 'Invalid CamillaDSP selection.'; exit 1; }
if [[ "$dsp_choice" == 0 ]]; then
    ((CURRENT_VALID)) || { echo 'No current local profile; choose a number.'; exit 1; }
    DSP_PROFILE="$CURRENT_DSP"
else
    index=$((10#$dsp_choice-1))
    ((index >= 0 && index < ${#DSP_PROFILES[@]})) || { echo 'Invalid CamillaDSP selection.'; exit 1; }
    DSP_PROFILE="${DSP_PROFILES[$index]}"
fi
"$CAMILLA" --check "$PROFILES_DIR/$DSP_PROFILE" >>"$ACTION_LOG" 2>&1 || { echo "Invalid CamillaDSP profile: $DSP_PROFILE"; exit 1; }
command -v arecord >/dev/null && command -v pacat >/dev/null || { echo 'arecord or pacat missing.'; exit 1; }
arecord -L | awk '$1 == "camilladsp_output_shared" {found=1} END {exit !found}' || { echo 'camilladsp_output_shared ALSA capture is missing.'; exit 1; }
# This local selection is laptop-only. Stop competing inputs after all choices validate.
server_pid="$(read_pid "$SERVERPID")"
if valid_pid "$server_pid" && tr '\0' ' ' <"/proc/$server_pid/cmdline" | grep -Fq 'camilladsp-server-sonobus.py'; then
    if [[ "$(cat "$STATE_DIR/audio-stop-capable.pid" 2>/dev/null || true)" != "$server_pid" ]]; then
        echo 'Webremote is running old code; restart the patched server before local selection.'
        exit 1
    fi
    rm -f -- "$STATE_DIR/audio-stop-complete"
    kill -USR1 "$server_pid" || { echo 'Could not release webremote audio engine.'; exit 1; }
    for _ in {1..100}; do [[ -f "$STATE_DIR/audio-stop-complete" ]] && break; sleep 0.1; done
    [[ -f "$STATE_DIR/audio-stop-complete" ]] || { echo 'Webremote did not release its audio engine.'; exit 1; }
fi
tracked="$(read_pid "$PIDFILE")"
while read -r pid; do
    [[ -z "$pid" ]] && continue
    valid_pid "$pid" || continue
    [[ "$pid" == "$tracked" ]] || { echo "Untracked CamillaDSP process $pid is running; refusing to replace it."; exit 1; }
done < <(pgrep -x camilladsp || true)
systemctl stop shairport-sync.service nqptp.service >>"$ACTION_LOG" 2>&1 || true
for pid in $(pgrep -x sonobus 2>/dev/null; pgrep -x SonoBus 2>/dev/null); do
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -TERM "$pid" 2>/dev/null || true
done
# Pause/resume and volume normalization remain the original functions.
stop_monitor
if ! ensure_camilla_sink; then echo "camilladsp desktop sink is unavailable. See: $ACTION_LOG"; exit 1; fi
if [[ "$(pactl get-default-sink 2>/dev/null || true)" != camilladsp ]]; then
    pactl set-default-sink camilladsp >>"$ACTION_LOG" 2>&1 || { echo 'Failed to select CamillaDSP desktop sink.'; exit 1; }
fi
move_application_inputs_to camilladsp
stop_camilla
if ! start_camilla "$PROFILES_DIR/$DSP_PROFILE"; then
    if [[ -n "$CURRENT_DSP" && "$CURRENT_DSP" != "$DSP_PROFILE" && -f "$PROFILES_DIR/$CURRENT_DSP" ]]; then
        start_camilla "$PROFILES_DIR/$CURRENT_DSP" || true
    fi
    echo "Failed to start CamillaDSP. See: $LOCAL_CAMILLA_LOG"
    exit 1
fi
if ! start_local_monitor "$PHYSICAL_SINK"; then
    echo "Local playback monitor failed. See: $ACTION_LOG"
    exit 1
fi
printf 'laptop_laptop\n' >"$REMOTE_STATE/mode"
rm -f -- "$STATE_DIR/audio-stopped"
# Save route only after a successful start; webremote can reuse this route.
jq -n --arg card "$SELECTED_CARD" --arg profile "$SELECTED_PROFILE" --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT"     '{card:$card,profile:$profile,sink:$sink,port:$port}' >"$ROUTE_FILE.tmp.$$" &&
    mv -f "$ROUTE_FILE.tmp.$$" "$ROUTE_FILE"
normalize_audio_volumes "$ORIGINAL_VOLUME" camilladsp
resume_previously_playing_media
echo; echo "Device: $SELECTED_CARD_LABEL"
echo "Card profile: $SELECTED_PROFILE_LABEL"
echo "Physical output: $(sink_display_label "$PHYSICAL_SINK")"
echo "CamillaDSP: $(short_profile_name "$DSP_PROFILE")"
echo "Desktop default: $(pactl get-default-sink 2>/dev/null || true)"
pause_before_close
