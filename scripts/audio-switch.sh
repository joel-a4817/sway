#!/usr/bin/env bash
set -u

HOME_DIR="${HOME:?HOME is required}"
STATE_DIR="$HOME_DIR/.local/state/sway/audio-switch"
REMOTE_STATE="$HOME_DIR/.local/state/sway/camilladsp-webremote"
LEGACY_STATE="$HOME_DIR/.local/state/sway/audio"

# Prevent overlapping selectors from changing the graph concurrently.
mkdir -p "$STATE_DIR" "$REMOTE_STATE"
# Do not migrate an active old server's PID/markers: restart the paired server first.
legacy_server_pid="$(cat "$LEGACY_STATE/camilladsp-webremote/web-server.pid" 2>/dev/null || true)"
if [[ "$legacy_server_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$legacy_server_pid" 2>/dev/null; then
    echo 'Restart the updated webremote server before using the updated audio switch.' >&2
    exit 1
fi
# First-run migration: copy persistent values without overwriting new state.
if [[ ! -e "$STATE_DIR/state-migrated" && -d "$LEGACY_STATE" ]]; then
    for old in "$LEGACY_STATE"/*; do
        [[ -f "$old" && ! -L "$old" ]] || continue
        case "${old##*/}" in audio-stopped|audio-stop-complete|audio-stop-capable.pid) target="$REMOTE_STATE/${old##*/}" ;; *) target="$STATE_DIR/${old##*/}" ;; esac
        [[ -e "$target" ]] || mv -- "$old" "$target" 2>/dev/null || true
    done
    if [[ -d "$LEGACY_STATE/camilladsp-webremote" ]]; then
        for old in "$LEGACY_STATE/camilladsp-webremote"/*; do
            [[ -f "$old" && ! -L "$old" ]] || continue
            target="$REMOTE_STATE/${old##*/}"
            [[ -e "$target" ]] || mv -- "$old" "$target" 2>/dev/null || true
        done
    fi
    touch "$STATE_DIR/state-migrated"
    rmdir "$LEGACY_STATE/camilladsp-webremote" "$LEGACY_STATE" 2>/dev/null || true
fi
# Serialize new launches separately from the lock shared with webremote startup.
LAUNCH_LOCK="$STATE_DIR/audio-switch-launch.lock"
OWNER_FILE="$STATE_DIR/audio-switch-owner"
LOCK_FILE="$STATE_DIR/audio-switch.lock"
exec 8>"$LAUNCH_LOCK"
flock -x 8 || exit 1
# Match a switch process by its script argument, not by its terminal or audio engine.
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
switch_process() {
    local pid="$1" arg comm
    [[ "$pid" =~ ^[1-9][0-9]*$ && "$pid" != "$$" && -r "/proc/$pid/cmdline" ]] || return 1
    comm="$(cat "/proc/$pid/comm" 2>/dev/null)"
    [[ "$comm" == bash ]] || return 1
    local -a args=()
    mapfile -d '' -t args <"/proc/$pid/cmdline" 2>/dev/null || return 1
    # The script must be Bash's script operand, not a path in unrelated arguments.
    arg="${args[1]:-}"
    [[ "$arg" == "$SCRIPT_PATH" ]] && return 0
    [[ "$arg" == */audio-switch.sh || "$arg" == audio-switch.sh ]] || return 1
    [[ "$(readlink -f -- "$arg" 2>/dev/null)" == "$SCRIPT_PATH" ]]
}
process_start() {
    local stat
    stat="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
    stat="${stat##*) }"
    set -- $stat
    printf '%s\n' "${20:-}"
}
# Replace only a verified switch process. The /proc start time protects
# against PID reuse; the scan also covers the first launch after upgrading.
for entry in /proc/[0-9]*; do
    previous="${entry##*/}"
    switch_process "$previous" || continue
    [[ "$(stat -c %u "$entry" 2>/dev/null)" == "$(id -u)" ]] || continue
    start="$(process_start "$previous")"
    [[ -n "$start" ]] || continue
    # Older copies do not handle USR2 and would leave their Swaynag child behind.
    while read -r child; do
        [[ "$child" =~ ^[1-9][0-9]*$ ]] && kill -TERM "$child" 2>/dev/null || true
    done < <(pgrep -P "$previous" -x swaynag 2>/dev/null || true)
    kill -USR2 "$previous" 2>/dev/null || kill -TERM "$previous" 2>/dev/null || true
    for ((i=0;i<30;i++)); do
        switch_process "$previous" || break
        sleep 0.1
    done
    if switch_process "$previous" && [[ "$(process_start "$previous")" == "$start" ]]; then
        kill -KILL "$previous" 2>/dev/null || true
    fi
done
exec 9>"$LOCK_FILE"
if ! flock -w 10 9; then
    echo 'Previous audio switch or webremote startup still holds the audio lock.' >&2
    exit 1
fi
printf '%s %s\n' "$$" "$(process_start "$$")" >"$OWNER_FILE"
flock -u 8
exec 8>&-

ACTION_LOG="$STATE_DIR/audio-switch.log"
RESULT_FILE="$STATE_DIR/audio-toggle-complete.$$"
ACTION_STARTED_FILE="$STATE_DIR/audio-toggle-started.$$"
CARD_SELECTION_FILE="$STATE_DIR/audio-card-selected.$$"
SWAYNAG_LOG="$STATE_DIR/audio-switch-swaynag.$$"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
printf '\n[%s] audio-switch pid=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$$" >>"$ACTION_LOG"
FINAL_PAUSE_REACHED=0
EXIT_HANDLER_RUNNING=0

cleanup() {
    if [[ "${SWAYNAG_PID:-}" =~ ^[1-9][0-9]*$ ]] &&
       [[ "$(ps -o ppid= -p "$SWAYNAG_PID" 2>/dev/null | tr -d ' ')" == "$$" ]] &&
       [[ "$(cat "/proc/$SWAYNAG_PID/comm" 2>/dev/null)" == swaynag ]]; then
        kill "$SWAYNAG_PID" 2>/dev/null || true
        wait "$SWAYNAG_PID" 2>/dev/null || true
    fi
    rm -f "$RESULT_FILE" "$ACTION_STARTED_FILE" "$CARD_SELECTION_FILE" "$SWAYNAG_LOG"
    local owner start
    if [[ -r "$OWNER_FILE" ]]; then
        read -r owner start <"$OWNER_FILE" || true
        if [[ "$owner" == "$$" && "$start" == "$(process_start "$$")" ]]; then rm -f -- "$OWNER_FILE"; fi
    fi
}
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
    if (( status != 0 && ! FINAL_PAUSE_REACHED && ! ${REPLACED:-0} )); then
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
trap 'REPLACED=1; exit 0' USR2
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

move_application_inputs_to() {
    local wanted="$1"
    local id
    while read -r id _; do
        [[ -n "$id" ]] || continue
        pactl move-sink-input "$id" "$wanted" \
            >>"$ACTION_LOG" 2>&1 || true
    done < <(pactl list short sink-inputs 2>/dev/null || true)
}


# Local laptop-only CamillaDSP state. The phone server remains independent.
fail() { printf 'Error: %s\n' "$*" | tee -a "$ACTION_LOG" >&2; exit 1; }
CAMILLA="$(command -v camilladsp || true)"
PROFILES_DIR="$HOME_DIR/Documents/prefs/audio/camilladsp"
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
    local profile="$1" pid gain
    "$CAMILLA" --check "$profile" >>"$ACTION_LOG" 2>&1 || return 1
    local existing
    existing="$(pgrep -x camilladsp 2>/dev/null || true)"
    if [[ -n "$existing" ]]; then
        printf 'CamillaDSP still running (PID %s); refusing second instance\n' "$existing" >>"$ACTION_LOG"
        return 1
    fi
    if (echo > /dev/tcp/127.0.0.1/8767) >/dev/null 2>&1; then
        echo 'CamillaDSP websocket port 8767 is occupied; refusing second instance.' >>"$ACTION_LOG"
        return 1
    fi
    gain="0.0"
    "$CAMILLA" --port=8767 --gain="$gain" "$profile" >>"$LOCAL_CAMILLA_LOG" 2>&1 </dev/null 9>&- &
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
start_local_monitor() {
    local sink="$1" pid
    setsid bash -c '
      set -o pipefail
      arecord -q -D camilladsp_output_shared -r 96000 -f S32_LE -c 2 -t raw |
        pacat --playback --device="$1" --rate=96000 --format=s32le --channels=2
    ' _ "$sink" >>"$ACTION_LOG" 2>&1 </dev/null 9>&- &
    pid=$!
    printf '%s\n' "$pid" >"$MONPID"
    # Wait until pacat has registered a playback stream on the chosen sink.
    local i inputs client target pgid
    for ((i=0;i<40;i++)); do
        valid_pid "$pid" || { rm -f -- "$MONPID"; return 1; }
        target="$(pactl --format=json list sinks 2>/dev/null | jq -r --arg name "$sink" '.[] | select(.name==$name) | .index' | head -n1)"
        inputs="$(pactl --format=json list sink-inputs 2>/dev/null || true)"
        if [[ "$target" =~ ^[0-9]+$ && -n "$inputs" ]]; then
            while read -r client; do
                [[ "$client" =~ ^[0-9]+$ ]] || continue
                pgid="$(ps -o pgid= -p "$client" 2>/dev/null | tr -d ' ')"
                if [[ "$pgid" == "$pid" ]]; then return 0; fi
            done < <(jq -r --argjson target "$target" '.[] | select(.sink==$target) | .properties."application.process.id" // empty' <<<"$inputs" 2>/dev/null)
        fi
        sleep 0.1
    done
    stop_monitor
    return 1
}

pause_active_media() {
    local service

    if [[ -S /tmp/mpvsocket ]] && command -v socat >/dev/null 2>&1; then
        printf 'set pause yes\n' |
            socat - /tmp/mpvsocket >>"$ACTION_LOG" 2>&1 || true
    fi

    # Webremote MPV has its own IPC socket, independent of /tmp/mpvsocket.
    if [[ -S "$REMOTE_STATE/mpv.sock" ]] && command -v socat >/dev/null 2>&1; then
        printf '%s\n' '{"command":["set_property","pause",true]}' |
            socat -T 1 - "UNIX-CONNECT:$REMOTE_STATE/mpv.sock" >>"$ACTION_LOG" 2>&1 || true
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

# Shared saved master: default PipeWire sink and direct-ALSA MPV.
MASTER_VOLUME_FILE="$STATE_DIR/master-volume"
# One normalization implementation shared with webremote. The switch alone
# pauses desktop media; the server's normalizer never pauses playback.
normalize_audio_volumes() {
    pause_active_media
    python3 "$TOPOLOGY_SCRIPT" --normalize-audio >>"$ACTION_LOG" 2>&1 || {
        tail -n 8 "$ACTION_LOG" >&2
        return 1
    }
}
TOPOLOGY_SCRIPT="$HOME_DIR/.config/sway/scripts/network/camilladsp-server-sonobus.py"
[[ -f "$TOPOLOGY_SCRIPT" ]] || fail "Paired server script missing: $TOPOLOGY_SCRIPT"
output_topology() { python3 "$TOPOLOGY_SCRIPT" --output-topology; }
retry_selection() {
    printf '%s\n' "$1"
}

if pulse_ready; then
    TOPOLOGY_JSON="$(output_topology)" || fail 'Could not discover audio cards using paired server logic'
    mapfile -t CARDS < <(jq -r '.cards[] | [.name,.label] | @tsv' <<<"$TOPOLOGY_JSON")
    printf '%s\n' "${CARDS[@]}" >"$STATE_DIR/audio-cards-last.txt"
else
    # Audio Stop deliberately leaves PipeWire offline; preserve its menu.
    CARDS=()
    if [[ -s "$STATE_DIR/audio-cards-last.txt" ]]; then
        while IFS= read -r entry; do
            [[ "$entry" == *$'\t'* ]] && CARDS+=("$entry")
            [[ "$entry" != *$'\t'* && "$entry" == *'|'* ]] && CARDS+=("${entry%%|*}"$'\t'"${entry#*|}")
        done <"$STATE_DIR/audio-cards-last.txt"
    fi
fi
# Keep wrapped menu labels aligned after the numeric choice column.
print_menu_item() {
    local prefix="[$1] " label="$2" columns width line first=1
    columns="$(tput cols 2>/dev/null || true)"
    [[ "$columns" =~ ^[0-9]+$ ]] || columns="${COLUMNS:-80}"
    [[ "$columns" =~ ^[0-9]+$ ]] || columns=80
    width=$((columns - ${#prefix}))
    ((width >= 12)) || width=12
    while IFS= read -r line || [[ -n "$line" ]]; do
        if ((first)); then
            printf '%s%s\n' "$prefix" "$line"
            first=0
        else
            printf '%*s%s\n' "${#prefix}" '' "$line"
        fi
    done < <(printf '%s\n' "$label" | fold -s -w "$width")
}
sink_display_label() {
    jq -r --arg name "$1" '[.cards[0].sinks[] | select(.name==$name) | .label][0] // $name' <<<"$TOPOLOGY_JSON"
}
port_display_label() {
    jq -r --arg sink "$PHYSICAL_SINK" --arg port "$1" '[.cards[0].sinks[] | select(.name==$sink) | .ports[] | select(.name==$port) | .label][0] // $port' <<<"$TOPOLOGY_JSON"
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

# Opening the selector is itself a normalization checkpoint. This pauses
# current media before any Swaynag button can be pressed. If Audio Stop left
# PipeWire offline, ALSA normalization and media pausing still run; sink
# normalization is retried after PipeWire starts for a selected card.
# Audio actions normalize after selection; Away/display is controlled by the web button or Sway binding.
commit_local_output() {
    local server_pid tracked pid
# This local selection is laptop-only. Stop competing inputs after all choices validate.
server_pid="$(read_pid "$SERVERPID")"
if valid_pid "$server_pid" && tr '\0' ' ' <"/proc/$server_pid/cmdline" | grep -Fq 'camilladsp-server-sonobus.py'; then
    if [[ "$(cat "$REMOTE_STATE/audio-stop-capable.pid" 2>/dev/null || true)" != "$server_pid" ]]; then
        echo 'Webremote is running old code; restart the patched server before local selection.'
        exit 1
    fi
    rm -f -- "$REMOTE_STATE/audio-stop-complete" "$REMOTE_STATE/audio-start-error"
    kill -USR1 "$server_pid" || { echo 'Could not release webremote audio engine.'; exit 1; }
    for _ in {1..100}; do [[ -f "$REMOTE_STATE/audio-stop-complete" ]] && break; sleep 0.1; done
    [[ -f "$REMOTE_STATE/audio-stop-complete" ]] || { echo 'Webremote did not release its audio engine.'; exit 1; }
    [[ ! -s "$REMOTE_STATE/audio-start-error" ]] || fail "$(cat "$REMOTE_STATE/audio-start-error")"
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
# Normalization pauses media; nothing automatically resumes it.
stop_monitor
systemctl --user start pipewire.socket pipewire-pulse.socket wireplumber.service >>"$ACTION_LOG" 2>&1 || fail 'Could not start PipeWire after handoff'
wait_for_pulse || fail 'PipeWire did not become ready after handoff'
if ! ensure_camilla_sink; then echo "camilladsp desktop sink is unavailable. See: $ACTION_LOG"; exit 1; fi
if [[ "$(pactl get-default-sink 2>/dev/null || true)" != camilladsp ]]; then
    pactl set-default-sink camilladsp >>"$ACTION_LOG" 2>&1 || { echo 'Failed to select CamillaDSP desktop sink.'; exit 1; }
fi
move_application_inputs_to camilladsp
if valid_pid "$(read_pid "$PIDFILE")" && [[ "$(cat "$ACTIVE" 2>/dev/null || true)" == "$DSP_PROFILE" ]]; then
    echo 'Keeping the running CamillaDSP engine; switching only the physical monitor.' >>"$ACTION_LOG"
else
    stop_camilla
    if ! start_camilla "$PROFILES_DIR/$DSP_PROFILE"; then
    if [[ -n "$CURRENT_DSP" && "$CURRENT_DSP" != "$DSP_PROFILE" && -f "$PROFILES_DIR/$CURRENT_DSP" ]]; then
        start_camilla "$PROFILES_DIR/$CURRENT_DSP" || true
    fi
    echo "Failed to start CamillaDSP. See: $LOCAL_CAMILLA_LOG"
    exit 1
    fi
fi
wait_for_sink "$PHYSICAL_SINK" || { echo "Output disappeared before monitor start: $PHYSICAL_SINK"; exit 1; }
if ! start_local_monitor "$PHYSICAL_SINK"; then
    echo "Local playback monitor failed. See: $ACTION_LOG"
    exit 1
fi
printf 'laptop_laptop\n' >"$REMOTE_STATE/mode"
rm -f -- "$REMOTE_STATE/audio-stopped"
# Normalize once, after the complete route is saved.
}
save_selected_route() {
    TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh committed route'
    jq -e --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT" 'any(.cards[0].sinks[]?; .name==$sink and ($port=="" or (.activePort==$port and any(.ports[]?; .name==$port and .available!="no" and .available!="false"))))' <<<"$TOPOLOGY_JSON" >/dev/null || fail 'Output changed before saving the route'
    SINK_CARD="$(jq -r --arg sink "$PHYSICAL_SINK" '[.cards[0].sinks[] | select(.name==$sink) | .cardName][0] // ""' <<<"$TOPOLOGY_JSON")"
    SINK_PROFILE="$(jq -r --arg card "$SINK_CARD" '[.cards[] | select(.name==$card) | .activeProfile][0] // ""' <<<"$TOPOLOGY_JSON")"
    jq -n --arg card "$SELECTED_CARD" --arg profile "$SELECTED_PROFILE" --arg sinkCard "$SINK_CARD" --arg sinkProfile "$SINK_PROFILE" --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT" --arg label "$(sink_display_label "$PHYSICAL_SINK")" '{card:$card,profile:$profile,sink:$sink,port:$port,label:$label,sinkCard:$sinkCard,sinkProfile:$sinkProfile}' >"$ROUTE_FILE.tmp.$$" &&
        mv -f "$ROUTE_FILE.tmp.$$" "$ROUTE_FILE" || fail 'Could not save output route'
    python3 "$TOPOLOGY_SCRIPT" --remember-output "$(cat "$ROUTE_FILE")" >/dev/null || fail 'Could not remember output for this card/profile'
}
select_filter() {
    local current list info choice index selected label i
    current="$(cat "$ACTIVE" 2>/dev/null || true)"
    list="$(python3 "$TOPOLOGY_SCRIPT" --dsp-profiles)" || fail 'Could not list listening filters'
    info="$(python3 "$TOPOLOGY_SCRIPT" --dsp-filter-info)" || fail 'Could not load filter labels'
    mapfile -t DSP_PROFILES < <(jq -r '.[] | select((ascii_downcase | contains("cmf")) | not)' <<<"$list")
    ((${#DSP_PROFILES[@]})) || fail 'No listening filters found'
    echo; echo 'CamillaDSP listening filter'; echo
    for i in "${!DSP_PROFILES[@]}"; do
        label="$(jq -r --arg name "${DSP_PROFILES[$i]}" '.[$name].label // $name' <<<"$info")"
        print_menu_item "$((i+1))" "$label"
    done
    while :; do
        read -rp 'Select CamillaDSP filter: ' choice || { echo 'Selection cancelled.'; return 0; }
        [[ "$choice" =~ ^[0-9]+$ && ${#choice} -le 9 ]] || { echo 'Enter a listed number.'; continue; }
        index=$((10#$choice-1))
        ((index>=0 && index<${#DSP_PROFILES[@]})) || { echo 'Enter a listed number.'; continue; }
        selected="${DSP_PROFILES[$index]}"
        break
    done
    python3 "$TOPOLOGY_SCRIPT" --select-filter "$selected" || fail 'Filter reload failed'
    echo "Listening filter: $selected"
}
ARGS=(-t warning -y overlay -m "Choose card, then playback profile")

ARGS+=(
    -z "Audio services"
    "touch '$ACTION_STARTED_FILE'; printf '%s\n' audio-services >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
)
ARGS+=(
    -z "CamillaDSP filters"
    "touch '$ACTION_STARTED_FILE'; printf '%s\n' select-filter >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'"
)
for i in "${!CARDS[@]}"; do
    card="${CARDS[$i]%%$'\t'*}"; description="${CARDS[$i]#*$'\t'}"
    ARGS+=( -z "${description}" "touch '$ACTION_STARTED_FILE'; printf '%s\n' '$i' >'$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
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

if [[ "$SELECTED_CARD_VALUE" == audio-services ]]; then
    echo; echo 'Audio services'; echo
    print_menu_item 0 "Audio stop / start (toggle)"
    print_menu_item 1 "Restart CamillaDSP"
    print_menu_item 2 "Restart SonoBus"
    print_menu_item 3 "Restart AirPlay"
    print_menu_item 4 "Restart VNC"
    while :; do
        read -rp 'Select service action [0-4]: ' service_choice || { echo 'Cancelled.'; pause_before_close; exit 0; }
        [[ "$service_choice" =~ ^[0-4]$ ]] && break
        echo 'Enter 0, 1, 2, 3 or 4.'
    done
    if [[ "$service_choice" == 0 ]]; then
        python3 "$TOPOLOGY_SCRIPT" --audio-toggle || fail 'Audio stop/start failed'
    else
        python3 "$TOPOLOGY_SCRIPT" --restart-service "$service_choice" || fail 'Service restart failed'
    fi
    pause_before_close; exit 0
fi
if [[ "$SELECTED_CARD_VALUE" == select-filter ]]; then
    select_filter
    pause_before_close; exit 0
fi
[[ "$SELECTED_CARD_VALUE" =~ ^[0-9]+$ ]] && (( SELECTED_CARD_VALUE < ${#CARDS[@]} )) || { echo "Invalid audio device selection."; exit 1; }
if ! pulse_ready; then
    echo 'PipeWire is stopped; cannot enumerate playback profiles without starting audio. Output unchanged.'
    pause_before_close; exit 0
fi
# Save the actual master before profile activation changes the live graph.
current_master="$(pactl get-sink-volume @DEFAULT_SINK@ 2>/dev/null | grep -oE '[0-9]+%' | head -n1)"
if [[ "$current_master" =~ ^([0-9]|[1-9][0-9]|100)%$ ]]; then
    printf '%s\n' "$current_master" >"$STATE_DIR/master-volume"
fi
SELECTED_CARD="${CARDS[$SELECTED_CARD_VALUE]%%$'\t'*}"
SELECTED_CARD_LABEL="${CARDS[$SELECTED_CARD_VALUE]#*$'\t'}"
TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh audio cards'
jq -e --arg card "$SELECTED_CARD" 'any(.cards[]; .name==$card)' <<<"$TOPOLOGY_JSON" >/dev/null || fail "Selected card is no longer available: $SELECTED_CARD"

# Card choice is staged. The current profile is read only for the menu.
CARD_ACTIVE="$(jq -r --arg card "$SELECTED_CARD" '[.cards[] | select(.name==$card) | .activeProfile][0] // ""' <<<"$TOPOLOGY_JSON")"
TOPOLOGY_JSON="$(output_topology)"
mapfile -t PROFILES < <(jq -r --arg card "$SELECTED_CARD" '.cards[] | select(.name==$card) | .profiles[] | [.name,.label,.available] | @tsv' <<<"$TOPOLOGY_JSON")
((${#PROFILES[@]})) || fail "No playback profiles for: $SELECTED_CARD"
ACTIVE_PROFILE="$(jq -r --arg card "$SELECTED_CARD" '.cards[] | select(.name==$card) | .activeProfile' <<<"$TOPOLOGY_JSON")"
ACTIVE_PROFILE_LABEL="$(jq -r --arg card "$SELECTED_CARD" --arg profile "$ACTIVE_PROFILE" '[.cards[] | select(.name==$card) | .profiles[] | select(.name==$profile) | .label][0] // $profile' <<<"$TOPOLOGY_JSON")"
echo; echo "Card: $SELECTED_CARD_LABEL"; echo; echo "Card playback profile"; echo
[[ -n "$ACTIVE_PROFILE" ]] && print_menu_item 0 "Keep current profile ($ACTIVE_PROFILE_LABEL)" || print_menu_item 0 "Keep current profile"
for i in "${!PROFILES[@]}"; do
    IFS=$'\t' read -r profile description availability <<<"${PROFILES[$i]}"
    label="${description}"
    [[ "$availability" == no || "$availability" == false ]] && label+=" (unavailable)"
    print_menu_item "$((i+1))" "$label"
done
# Keep the menu visible while rejecting unavailable or invalid choices.
while :; do
    read -rp "Select profile: " profile_choice || { echo 'Selection cancelled.'; pause_before_close; exit 0; }
    if [[ ! "$profile_choice" =~ ^[0-9]+$ ]]; then
        retry_selection 'Enter one of the listed numbers.'; continue
    fi
    if [[ "$profile_choice" == 0 ]]; then
        if [[ -z "$ACTIVE_PROFILE" ]]; then
            retry_selection 'No current profile. Select a numbered profile.'; continue
        fi
        active_available="$(jq -r --arg card "$SELECTED_CARD" --arg profile "$ACTIVE_PROFILE" '[.cards[] | select(.name==$card) | .profiles[] | select(.name==$profile) | .available][0] // "unknown"' <<<"$TOPOLOGY_JSON")"
        if [[ "$active_available" == no || "$active_available" == false ]]; then
            retry_selection 'Current profile is unavailable. Select a numbered profile.'; continue
        fi
        SELECTED_PROFILE="$ACTIVE_PROFILE"
        SELECTED_PROFILE_LABEL="$ACTIVE_PROFILE_LABEL"
        break
    fi
    [[ ${#profile_choice} -le 9 ]] || { retry_selection 'Enter one of the listed numbers.'; continue; }
    profile_index=$((10#$profile_choice-1))
    if (( profile_index < 0 || profile_index >= ${#PROFILES[@]} )); then
        retry_selection 'Enter one of the listed numbers.'; continue
    fi
    IFS=$'\t' read -r candidate_profile description availability <<<"${PROFILES[$profile_index]}"
    if [[ "$availability" == no || "$availability" == false ]]; then
        retry_selection 'That profile is unavailable. Select another number.'; continue
    fi
    SELECTED_PROFILE="$candidate_profile"
    SELECTED_PROFILE_LABEL="${description}"
    break
done
restore_profile_on_cancel() {
    if [[ -n "${ORIGINAL_PROFILE:-}" && "$SELECTED_PROFILE" != "$ORIGINAL_PROFILE" ]]; then
        python3 "$TOPOLOGY_SCRIPT" --output-profile "$SELECTED_CARD" "$ORIGINAL_PROFILE" >/dev/null ||
            echo 'Warning: original card profile could not be restored.' >&2
    fi
}
# Activating a profile creates its live playback sinks. Restore on cancel.
ORIGINAL_PROFILE="$ACTIVE_PROFILE"
if [[ "$SELECTED_PROFILE" != "$ACTIVE_PROFILE" ]]; then
    python3 "$TOPOLOGY_SCRIPT" --output-profile "$SELECTED_CARD" "$SELECTED_PROFILE" >/dev/null || fail 'Could not activate playback profile'
fi
TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh sinks after profile activation'
ACTIVE_PROFILE="$SELECTED_PROFILE"
# The web picker and terminal now consume the same post-profile snapshot.
TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh output topology'
mapfile -t PHYSICAL_SINKS < <(jq -r --arg card "$SELECTED_CARD" '.cards[0].sinks[]?.name' <<<"$TOPOLOGY_JSON")
# [0] is the actual currently routed sink, not a profile recommendation.
CURRENT_PHYSICAL="$(jq -r '.sink // ""' "$ROUTE_FILE" 2>/dev/null || true)"
if valid_pid "$(read_pid "$MONPID")"; then
    monitor_pid="$(read_pid "$MONPID")"
    while IFS= read -r candidate_pid; do
        [[ "$candidate_pid" =~ ^[0-9]+$ ]] || continue
        [[ "$(cat "/proc/$candidate_pid/comm" 2>/dev/null)" == pacat ]] || continue
        mapfile -d '' -t monitor_args <"/proc/$candidate_pid/cmdline" 2>/dev/null || continue
        for arg in "${monitor_args[@]}"; do
            [[ "$arg" == --device=* ]] && CURRENT_PHYSICAL="${arg#--device=}"
        done
    done < <(pgrep -g "$monitor_pid" 2>/dev/null || true)
fi
printf '\nPlayback sink (actual destination; may belong to another card)\n' 
if [[ -n "$CURRENT_PHYSICAL" ]] && ! jq -e --arg sink "$CURRENT_PHYSICAL" 'any(.cards[0].sinks[]?; .name==$sink)' <<<"$TOPOLOGY_JSON" >/dev/null; then
    CURRENT_PHYSICAL=""
fi
if [[ -n "$CURRENT_PHYSICAL" ]]; then
    current_label="$(python3 "$TOPOLOGY_SCRIPT" --sink-label "$CURRENT_PHYSICAL")"
    print_menu_item 0 "Keep current output ($current_label)"
else
    echo 'No current output to keep; select a numbered sink.'
fi 
for i in "${!PHYSICAL_SINKS[@]}"; do
    sink="${PHYSICAL_SINKS[$i]}"
    sink_label="$(sink_display_label "$sink")"
    sink_usable="$(jq -r --arg sink "$sink" '[.cards[0].sinks[] | select(.name==$sink) | .ports[] | select(.available!="no" and .available!="false")] | length' <<<"$TOPOLOGY_JSON")"
    [[ "$sink_usable" == 0 && "$SELECTED_PROFILE" == "$ACTIVE_PROFILE" ]] && sink_label+=' (unavailable)'
    print_menu_item "$((i+1))" "$sink_label"
done
while :; do
    read -rp 'Select playback sink: ' physical_choice || { echo 'Selection cancelled; restoring previous card profile.'; restore_profile_on_cancel; pause_before_close; exit 0; }
    [[ "$physical_choice" =~ ^[0-9]+$ && ${#physical_choice} -le 9 ]] || { echo 'Enter a listed output number.'; continue; }
    if [[ "$physical_choice" == 0 ]]; then
        [[ -n "$CURRENT_PHYSICAL" ]] || { echo 'No current output; choose a numbered sink.'; continue; }
        PHYSICAL_SINK="$CURRENT_PHYSICAL"
    else
        index=$((10#$physical_choice-1))
        ((index>=0 && index<${#PHYSICAL_SINKS[@]})) || { echo 'Enter a listed output number.'; continue; }
        PHYSICAL_SINK="${PHYSICAL_SINKS[$index]}"
    fi
    break
done
# Ports are read from the refreshed, post-profile topology.
mapfile -t OUTPUT_PORTS < <(jq -r --arg sink "$PHYSICAL_SINK" '.cards[0].sinks[]? | select(.name==$sink) | .ports[] | select(.available!="no" and .available!="false") | .name' <<<"$TOPOLOGY_JSON")
SELECTED_PORT="$(python3 "$TOPOLOGY_SCRIPT" --remembered-port "$SELECTED_CARD" "$SELECTED_PROFILE" "$PHYSICAL_SINK")" || fail 'Could not read remembered port'
if [[ -n "$SELECTED_PORT" ]] && ! printf '%s\n' "${OUTPUT_PORTS[@]}" | grep -Fqx -- "$SELECTED_PORT"; then
    SELECTED_PORT=""
fi
if ((${#OUTPUT_PORTS[@]}==1)); then SELECTED_PORT="${OUTPUT_PORTS[0]}"; fi
if ((${#OUTPUT_PORTS[@]}>1)); then
    echo; echo 'Output port'; echo
    for i in "${!OUTPUT_PORTS[@]}"; do
        print_menu_item "$((i+1))" "$(port_display_label "${OUTPUT_PORTS[$i]}")"
    done
    while :; do
        read -rp 'Select output port: ' port_choice || { echo 'Selection cancelled; restoring previous card profile.'; restore_profile_on_cancel; pause_before_close; exit 0; }
        [[ "$port_choice" =~ ^[0-9]+$ && ${#port_choice} -le 9 ]] || { echo 'Enter a listed port number.'; continue; }
        index=$((10#$port_choice-1))
        ((index>=0 && index<${#OUTPUT_PORTS[@]})) || { echo 'Enter a listed port number.'; continue; }
        SELECTED_PORT="${OUTPUT_PORTS[$index]}"
        break
    done
fi
# Final commit. The server resolver activates the selected profile, discovers
# its resulting sinks/ports, and rejects an explicitly missing output.
REQUEST="$(jq -n --arg card "$SELECTED_CARD" --arg profile "$SELECTED_PROFILE" --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT" '{card:$card,profile:$profile,sink:$sink,port:$port}')"
ROUTE="$(python3 "$TOPOLOGY_SCRIPT" --resolve-output "$REQUEST")" || fail 'Could not activate and resolve selected output'
PHYSICAL_SINK="$(jq -r '.sink' <<<"$ROUTE")"
SELECTED_PORT="$(jq -r '.port // ""' <<<"$ROUTE")"
CURRENT_DSP="$(cat "$ACTIVE" 2>/dev/null || true)"
DSP_PROFILE="$CURRENT_DSP"
if [[ -z "$DSP_PROFILE" || ! -f "$PROFILES_DIR/$DSP_PROFILE" ]]; then
    DSP_LIST_JSON="$(python3 "$TOPOLOGY_SCRIPT" --dsp-profiles)" || fail 'Could not list listening filters'
    DSP_PROFILE="$(jq -r '.[0] // ""' <<<"$DSP_LIST_JSON")"
fi
[[ -n "$CAMILLA" && -n "$DSP_PROFILE" && -f "$PROFILES_DIR/$DSP_PROFILE" ]] || fail 'No valid listening filter exists'
"$CAMILLA" --check "$PROFILES_DIR/$DSP_PROFILE" >>"$ACTION_LOG" 2>&1 || fail 'Listening filter is invalid'
commit_local_output
save_selected_route
normalize_audio_volumes || fail 'Normalization failed after output commit'
echo; echo "Device: $SELECTED_CARD_LABEL"
echo "Card profile: $SELECTED_PROFILE_LABEL"
echo "Physical output: $(sink_display_label "$PHYSICAL_SINK")"
echo "CamillaDSP: $DSP_PROFILE"
pause_before_close
