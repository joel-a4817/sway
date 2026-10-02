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
# Lock lives under ~/.local/state/sway/audio-switch; children close fd 9.
LOCK_FILE="$STATE_DIR/audio-switch.lock"
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

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
printf '\n[%s] audio-switch pid=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$$" >>"$ACTION_LOG"
FINAL_PAUSE_REACHED=0
EXIT_HANDLER_RUNNING=0

cleanup() { rm -f "$RESULT_FILE" "$ACTION_STARTED_FILE" "$CARD_SELECTION_FILE" "$PROFILE_SELECTION_FILE" "$SWAYNAG_LOG"; }
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
read_master_volume() {
    local value
    value="$(cat "$MASTER_VOLUME_FILE" 2>/dev/null || true)"
    if [[ "$value" =~ ^([0-9]|[1-9][0-9]|100)(\.[0-9]+)?%$ ]]; then
        printf '%s\n' "$value"
        return
    fi
    value="$(pactl get-sink-volume "$(pactl get-default-sink 2>/dev/null)" 2>/dev/null | grep -oE '[0-9]+%' | head -n1)"
    [[ "$value" =~ ^([0-9]|[1-9][0-9]|100)%$ ]] || value=100%
    printf '%s\n' "$value" >"$MASTER_VOLUME_FILE"
    printf '%s\n' "$value"
}
# Parse the kernel's ALSA card list, not playback-only aplay -l. This also
# includes capture-only devices. Only controls advertising a dB scale qualify.
normalize_alsa_controls() {
    local card entry control info direction failures=0 count=0
    while IFS= read -r card; do
        [[ -n "$card" ]] || continue
        while IFS= read -r entry; do
            [[ -n "$entry" ]] || continue
            control="${entry#*\'}"; control="${control%%\'*}"
            # Include the index for identically named mixer controls.
            if [[ "$entry" =~ ,([0-9]+)$ ]]; then control+=",${BASH_REMATCH[1]}"; fi
            info="$(amixer -c "$card" sget "$control" 2>/dev/null)" || continue
            [[ "$info" == *'dB'* ]] || continue
            for direction in playback capture; do
                [[ "$info" == *"${direction^} channels:"* ]] || continue
                if amixer -c "$card" sset "$control" "$direction" 0dB >/dev/null 2>>"$ACTION_LOG"; then
                    ((count+=1))
                else
                    ((failures+=1))
                    printf 'Warning: could not set card %s control %s %s to 0dB\n' "$card" "$control" "$direction" >>"$ACTION_LOG"
                fi
            done
        done < <(amixer -c "$card" scontrols 2>/dev/null | sed -n "/^Simple mixer control /p")
    done < <(awk '/^[[:space:]]*[0-9]+[[:space:]]+\[/{print $1}' /proc/asound/cards 2>/dev/null)
    printf 'ALSA normalization: %s dB-capable playback/capture directions set to 0dB; %s failed\n' "$count" "$failures" >>"$ACTION_LOG"
    (( failures == 0 ))
}
normalize_audio_volumes() {
    local sink default master
    pause_active_media
    master="$(read_master_volume)"
    normalize_alsa_controls || echo 'Warning: some ALSA controls could not reach 0dB; see action log.' >&2
    default="$(pactl get-default-sink 2>/dev/null || true)"
    while IFS= read -r sink; do
        [[ -n "$sink" ]] || continue
        if [[ "$sink" != "$default" ]]; then
            pactl set-sink-volume "$sink" 100% >>"$ACTION_LOG" 2>&1 || true
        fi
    done < <(pactl list short sinks 2>/dev/null | awk '{print $2}')
    if [[ -n "$default" ]] && ! valid_pid "$(read_pid "$PIDFILE")"; then
        pactl set-sink-volume @DEFAULT_SINK@ "$master" >>"$ACTION_LOG" 2>&1 || return 1
    fi
    # Webremote MPV bypasses pactl via ALSA. Restore its gain from the same
    # master-volume file without changing playback state or routing.
    if [[ -S "$REMOTE_STATE/mpv.sock" ]] && command -v socat >/dev/null 2>&1; then
        printf '{"command":["set_property","volume",%s]}\n' "${master%\%}" |
            socat -T 2 - "UNIX-CONNECT:$REMOTE_STATE/mpv.sock" >/dev/null 2>>"$ACTION_LOG" || true
    fi
    if valid_pid "$(read_pid "$PIDFILE")"; then restore_saved_master || return 1; fi
}
restore_saved_master() {
    local master
    master="$(read_master_volume)"
    if pulse_ready; then
        pactl set-sink-volume @DEFAULT_SINK@ "$master" >>"$ACTION_LOG" 2>&1 || return 1
    fi
    if valid_pid "$(read_pid "$PIDFILE")"; then
        CAMILLA_GAIN=0 python3 - <<'PYCODE' >>"$ACTION_LOG" 2>&1 || return 1
import base64,hashlib,json,os,socket,struct
key=base64.b64encode(os.urandom(16)).decode()
with socket.create_connection(('127.0.0.1',8767),timeout=2) as sock:
    sock.settimeout(2)
    sock.sendall((f'GET / HTTP/1.1\r\nHost: 127.0.0.1:8767\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
    header=b''
    while b'\r\n\r\n' not in header:
        part=sock.recv(1)
        if not part or len(header)>8192:raise RuntimeError('Websocket handshake failed')
        header+=part
    expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).lower()
    if not header.startswith(b'HTTP/1.1 101 ') or b'sec-websocket-accept: '+expected not in header.lower():raise RuntimeError('Unexpected websocket server')
    payload=json.dumps({'SetVolume':0.0}).encode();mask=os.urandom(4);n=len(payload)
    size=bytes([n]) if n<126 else b'\x7e'+struct.pack('!H',n)
    sock.sendall(b'\x81'+bytes([size[0]|128])+size[1:]+mask+bytes(v^mask[i%4] for i,v in enumerate(payload)))
    head=sock.recv(2)
    if len(head)!=2 or head[0]&15!=1:raise RuntimeError('No CamillaDSP response')
    n=head[1]&127
    if n==126:n=struct.unpack('!H',sock.recv(2))[0]
    data=b''
    while len(data)<n:
        chunk=sock.recv(n-len(data))
        if not chunk:raise RuntimeError('CamillaDSP response closed')
        data+=chunk
    if json.loads(data).get('SetVolume',{}).get('result')!='Ok':raise RuntimeError('CamillaDSP unity failed')
PYCODE
    fi
}
export -f pause_active_media read_master_volume normalize_alsa_controls normalize_audio_volumes
export MASTER_VOLUME_FILE
export RESULT_FILE ACTION_STARTED_FILE ACTION_LOG CARD_SELECTION_FILE PROFILE_SELECTION_FILE
TOPOLOGY_SCRIPT="$HOME_DIR/.config/sway/scripts/network/camilladsp-server-sonobus.py"
[[ -f "$TOPOLOGY_SCRIPT" ]] || fail "Paired server script missing: $TOPOLOGY_SCRIPT"
output_topology() { python3 "$TOPOLOGY_SCRIPT" --output-topology; }
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
card_display_label() { printf '%s\n' "$2"; }
profile_display_label() { printf '%s\n' "$3"; }
sink_display_label() {
    jq -r --arg name "$1" '[.cards[].sinks[]? | select(.name==$name) | .label][0] // $name' <<<"$TOPOLOGY_JSON"
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

# Opening the selector is itself a normalization checkpoint. This pauses
# current media before any Swaynag button can be pressed. If Audio Stop left
# PipeWire offline, ALSA normalization and media pausing still run; sink
# normalization is retried after PipeWire starts for a selected card.
if pulse_ready; then
    live="$(pactl get-sink-volume @DEFAULT_SINK@ 2>/dev/null | grep -oE '[0-9]+%' | head -n1)"
    if [[ "$live" =~ ^([0-9]|[1-9][0-9]|100)%$ ]]; then printf '%s\n' "$live" >"$MASTER_VOLUME_FILE"; fi
fi
normalize_audio_volumes || fail 'Normalization failed while opening audio switch'

ARGS=(-t warning -y overlay -m "Select audio card")

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
    card="${CARDS[$i]%%$'\t'*}"; description="${CARDS[$i]#*$'\t'}"
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
normalize_audio_volumes || fail "Normalization failed after audio action selection"

if [[ "$SELECTED_CARD_VALUE" == stop-audio ]]; then
    echo "Stopping all audio..." | tee -a "$ACTION_LOG"
    # Marker is shared with the webremote; it must survive its restart.
    server_pid="$(read_pid "$SERVERPID")"
    if valid_pid "$server_pid" &&
       tr '\0' ' ' <"/proc/$server_pid/cmdline" | grep -Fq 'camilladsp-server-sonobus.py'; then
        if [[ "$(cat "$REMOTE_STATE/audio-stop-capable.pid" 2>/dev/null || true)" != "$server_pid" ]]; then
            echo 'Webremote is still running old code; restart it with the patched server before Audio Stop.'
            exit 1
        fi
        rm -f -- "$REMOTE_STATE/audio-stop-complete"
        : >"$REMOTE_STATE/audio-stopped"
        kill -USR1 "$server_pid" || { echo 'Could not signal webremote to stop audio.'; exit 1; }
        for _ in {1..100}; do
            [[ -f "$REMOTE_STATE/audio-stop-complete" ]] && break
            sleep 0.1
        done
        [[ -f "$REMOTE_STATE/audio-stop-complete" ]] || { echo 'Webremote did not confirm audio stopped.'; exit 1; }
    fi
    : >"$REMOTE_STATE/audio-stopped"
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
    normalize_audio_volumes || fail "Normalization failed after hotspot selection"
    pause_before_close; exit 0
fi

[[ "$SELECTED_CARD_VALUE" =~ ^[0-9]+$ ]] && (( SELECTED_CARD_VALUE < ${#CARDS[@]} )) || { echo "Invalid audio device selection."; exit 1; }
if ! pulse_ready; then
    systemctl --user start pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service >>"$ACTION_LOG" 2>&1 || { echo "PipeWire failed to start; see $ACTION_LOG"; exit 1; }
    wait_for_pulse || { echo 'PipeWire did not become ready.'; exit 1; }
fi
SELECTED_CARD="${CARDS[$SELECTED_CARD_VALUE]%%$'\t'*}"
TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh audio cards'
jq -e --arg card "$SELECTED_CARD" 'any(.cards[]; .name==$card)' <<<"$TOPOLOGY_JSON" >/dev/null || fail "Selected card is no longer available: $SELECTED_CARD"
SELECTED_CARD_DESCRIPTION="${CARDS[$SELECTED_CARD_VALUE]#*$'\t'}"
SELECTED_CARD_LABEL="$(card_display_label "$SELECTED_CARD" "$SELECTED_CARD_DESCRIPTION")"

normalize_audio_volumes || fail "Normalization failed after card selection"

TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh card profiles'
mapfile -t PROFILES < <(jq -r --arg card "$SELECTED_CARD" '.cards[] | select(.name==$card) | .profiles[] | [.name,.label,.available] | @tsv' <<<"$TOPOLOGY_JSON")
((${#PROFILES[@]})) || fail "No playback profiles for: $SELECTED_CARD"
ACTIVE_PROFILE="$(jq -r --arg card "$SELECTED_CARD" '.cards[] | select(.name==$card) | .activeProfile' <<<"$TOPOLOGY_JSON")"
ACTIVE_PROFILE_LABEL="$(jq -r --arg card "$SELECTED_CARD" --arg profile "$ACTIVE_PROFILE" '[.cards[] | select(.name==$card) | .profiles[] | select(.name==$profile) | .label][0] // $profile' <<<"$TOPOLOGY_JSON")"
echo; echo "Card: $SELECTED_CARD_LABEL"; echo; echo "Card playback profile"; echo
[[ -n "$ACTIVE_PROFILE" ]] && echo "[0] Keep current profile ($ACTIVE_PROFILE_LABEL)" || echo "[0] Keep current profile"
for i in "${!PROFILES[@]}"; do
    IFS=$'\t' read -r profile description availability <<<"${PROFILES[$i]}"
    label="$(profile_display_label "$SELECTED_CARD" "$profile" "$description")"
    [[ "$availability" == no ]] && label+=" (unavailable)"
    echo "[$((i+1))] $label"
done
read -rp "Select profile: " profile_choice
SELECTED_PROFILE="$ACTIVE_PROFILE"; SELECTED_PROFILE_LABEL="$ACTIVE_PROFILE_LABEL"
[[ "$profile_choice" != 0 || -n "$SELECTED_PROFILE" ]] || fail "No active profile; select a numbered profile."
if [[ "$profile_choice" != 0 ]]; then
    [[ "$profile_choice" =~ ^[0-9]+$ ]] || { echo "Invalid profile selection."; exit 1; }
    profile_index=$((10#$profile_choice-1)); (( profile_index >= 0 && profile_index < ${#PROFILES[@]} )) || { echo "Invalid profile selection."; exit 1; }
    IFS=$'\t' read -r SELECTED_PROFILE description availability <<<"${PROFILES[$profile_index]}"
    [[ "$availability" != no ]] || { echo "That profile is currently marked unavailable by PipeWire."; exit 1; }
    SELECTED_PROFILE_LABEL="$(profile_display_label "$SELECTED_CARD" "$SELECTED_PROFILE" "$description")"
    TOPOLOGY_JSON="$(python3 "$TOPOLOGY_SCRIPT" --output-profile "$SELECTED_CARD" "$SELECTED_PROFILE")" || fail "Could not activate profile: $SELECTED_PROFILE"
    printf '%s\n' "$SELECTED_PROFILE_LABEL" >"$PROFILE_SELECTION_FILE"
fi
normalize_audio_volumes || fail "Normalization failed after profile selection"
# The web picker and terminal now consume the same post-profile snapshot.
TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh output topology'
mapfile -t PHYSICAL_SINKS < <(jq -r --arg card "$SELECTED_CARD" '.cards[] | select(.name==$card) | .sinks[]?.name' <<<"$TOPOLOGY_JSON")
((${#PHYSICAL_SINKS[@]})) || fail 'No usable playback sinks are available.'
# Prefer the running monitor, then the saved route, then a physical default.
CURRENT_PHYSICAL=""
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
if [[ -z "$CURRENT_PHYSICAL" ]]; then
    CURRENT_PHYSICAL="$(jq -r '.sink // ""' "$ROUTE_FILE" 2>/dev/null || true)"
fi
if [[ -z "$CURRENT_PHYSICAL" ]]; then
    default_sink="$(pactl get-default-sink 2>/dev/null || true)"
    if [[ "$default_sink" != camilladsp ]]; then CURRENT_PHYSICAL="$default_sink"; fi
fi
if [[ -n "$CURRENT_PHYSICAL" ]] && ! printf '%s\n' "${PHYSICAL_SINKS[@]}" | grep -Fqx -- "$CURRENT_PHYSICAL"; then
    CURRENT_PHYSICAL=""
fi
printf '\nPlayback sink (actual destination; may belong to another card)\n' 
if [[ -n "$CURRENT_PHYSICAL" ]]; then
    printf '[0] Keep current output (%s)\n' "$(sink_display_label "$CURRENT_PHYSICAL")"
else
    echo "No current physical output; select a numbered output."
fi
for i in "${!PHYSICAL_SINKS[@]}"; do
    sink="${PHYSICAL_SINKS[$i]}"
    printf '[%d] %s\n' "$((i+1))" "$(sink_display_label "$sink")"
done
read -rp 'Select playback sink: ' physical_choice
[[ "$physical_choice" =~ ^[0-9]+$ ]] || { echo 'Invalid output selection.'; exit 1; }
if [[ "$physical_choice" == 0 ]]; then
    [[ -n "$CURRENT_PHYSICAL" ]] || fail 'No current physical output; select a numbered output.'
    PHYSICAL_SINK="$CURRENT_PHYSICAL"
else
    index=$((10#$physical_choice-1))
    ((index >= 0 && index < ${#PHYSICAL_SINKS[@]})) || { echo 'Invalid output selection.'; exit 1; }
    PHYSICAL_SINK="${PHYSICAL_SINKS[$index]}"
fi
TOPOLOGY_JSON="$(output_topology)" || fail 'Could not refresh output topology'
jq -e --arg card "$SELECTED_CARD" --arg sink "$PHYSICAL_SINK" 'any(.cards[] | select(.name==$card) | .sinks[]?; .name==$sink)' <<<"$TOPOLOGY_JSON" >/dev/null || fail "Selected output disappeared: $PHYSICAL_SINK"
normalize_audio_volumes || fail "Normalization failed after playback sink selection"
# Use the live sink ports; option 0 retains the active port.
mapfile -t OUTPUT_PORTS < <(jq -r --arg card "$SELECTED_CARD" --arg sink "$PHYSICAL_SINK" '.cards[] | select(.name==$card) | .sinks[] | select(.name==$sink) | .ports[].name' <<<"$TOPOLOGY_JSON")
SELECTED_PORT=""
if ((${#OUTPUT_PORTS[@]})); then
    active_port="$(jq -r --arg card "$SELECTED_CARD" --arg name "$PHYSICAL_SINK" '.cards[] | select(.name==$card) | .sinks[] | select(.name==$name) | .activePort' <<<"$TOPOLOGY_JSON")"
    for port in "${OUTPUT_PORTS[@]}"; do
        [[ "$port" == "$active_port" ]] && SELECTED_PORT="$port"
    done
    if [[ -n "$SELECTED_PORT" ]]; then
        selected_available="$(jq -r --arg card "$SELECTED_CARD" --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT" '[.cards[] | select(.name==$card) | .sinks[] | select(.name==$sink) | .ports[] | select(.name==$port) | .available][0] // "unknown"' <<<"$TOPOLOGY_JSON")"
        [[ "$selected_available" != no && "$selected_available" != false ]] || SELECTED_PORT=""
    fi
    if ((${#OUTPUT_PORTS[@]} > 1)); then
        printf '\nOutput port\n[0] Keep current (%s)\n' "${SELECTED_PORT:-none}"
        for i in "${!OUTPUT_PORTS[@]}"; do
            port_label="$(jq -r --arg card "$SELECTED_CARD" --arg sink "$PHYSICAL_SINK" --arg port "${OUTPUT_PORTS[$i]}" '.cards[] | select(.name==$card) | .sinks[] | select(.name==$sink) | .ports[] | select(.name==$port) | .label' <<<"$TOPOLOGY_JSON")"
            availability="$(jq -r --arg card "$SELECTED_CARD" --arg sink "$PHYSICAL_SINK" --arg port "${OUTPUT_PORTS[$i]}" '[.cards[] | select(.name==$card) | .sinks[] | select(.name==$sink) | .ports[] | select(.name==$port) | .available][0] // "unknown"' <<<"$TOPOLOGY_JSON")"
            if [[ "$availability" == no || "$availability" == false ]]; then port_label+=" (unavailable)"; fi
            printf '[%d] %s\n' "$((i+1))" "${port_label:-${OUTPUT_PORTS[$i]}}"
        done
        read -rp 'Select output port: ' port_choice
        [[ "$port_choice" =~ ^[0-9]+$ ]] || { echo 'Invalid port.'; exit 1; }
        if [[ "$port_choice" == 0 && -z "$SELECTED_PORT" ]]; then
            fail 'No currently active, available port; select a numbered port.'
        fi
        if [[ "$port_choice" != 0 ]]; then
            index=$((10#$port_choice-1))
            ((index >= 0 && index < ${#OUTPUT_PORTS[@]})) || { echo 'Invalid port.'; exit 1; }
            SELECTED_PORT="${OUTPUT_PORTS[$index]}"
        fi
    fi
    if [[ -z "$SELECTED_PORT" && ${#OUTPUT_PORTS[@]} -eq 1 ]]; then
        SELECTED_PORT="${OUTPUT_PORTS[0]}"
    fi
    PORT_AVAILABILITY="$(jq -r --arg card "$SELECTED_CARD" --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT" '[.cards[] | select(.name==$card) | .sinks[] | select(.name==$sink) | .ports[] | select(.name==$port) | .available][0] // "unknown"' <<<"$TOPOLOGY_JSON")"
    [[ "$PORT_AVAILABILITY" != no && "$PORT_AVAILABILITY" != false ]] || fail "Selected output port is reported unavailable: $SELECTED_PORT"
    if [[ -n "$SELECTED_PORT" ]]; then
        pactl set-sink-port "$PHYSICAL_SINK" "$SELECTED_PORT" >>"$ACTION_LOG" 2>&1 || fail 'Failed to set output port.'
    fi
fi
normalize_audio_volumes || fail "Normalization failed after output port selection"

TOPOLOGY_JSON="$(output_topology)" || fail 'Could not verify output after port selection'
jq -e --arg card "$SELECTED_CARD" --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT" 'any(.cards[] | select(.name==$card) | .sinks[]?; .name==$sink and any(.ports[]?; .name==$port))' <<<"$TOPOLOGY_JSON" >/dev/null || fail 'Selected output or port disappeared'
# Use the same CamillaDSP profile list as the web picker.
[[ -n "$CAMILLA" && -d "$PROFILES_DIR" ]] || fail 'CamillaDSP executable or profile directory missing'
CURRENT_DSP="$(cat "$ACTIVE" 2>/dev/null || true)"
short_profile_name() {
    local stem="${1%.yaml}"
    stem="${stem%.yml}"
    stem="${stem#[0-9][0-9]-}"
    stem="${stem//-/ }"
    printf '%s\n' "$stem" | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2); print}'
}
DSP_LIST_JSON="$(python3 "$TOPOLOGY_SCRIPT" --dsp-profiles)" || fail 'Could not list CamillaDSP profiles from paired server'
mapfile -t DSP_PROFILES < <(jq -r '.[] | select((ascii_downcase | contains("cmf")) | not)' <<<"$DSP_LIST_JSON")
((${#DSP_PROFILES[@]})) || fail 'No CamillaDSP profiles found'
CURRENT_VALID=0
for dsp in "${DSP_PROFILES[@]}"; do
    [[ "$dsp" == "$CURRENT_DSP" ]] && CURRENT_VALID=1
done
echo; echo 'CamillaDSP listening profile (filter before the playback sink)'; echo
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
normalize_audio_volumes || fail "Normalization failed after CamillaDSP filter selection"
"$CAMILLA" --check "$PROFILES_DIR/$DSP_PROFILE" >>"$ACTION_LOG" 2>&1 || { echo "Invalid CamillaDSP profile: $DSP_PROFILE"; exit 1; }
command -v arecord >/dev/null && command -v pacat >/dev/null || { echo 'arecord or pacat missing.'; exit 1; }
arecord -L | awk '$1 == "camilladsp_output_shared" {found=1} END {exit !found}' || { echo 'camilladsp_output_shared ALSA capture is missing.'; exit 1; }
# This local selection is laptop-only. Stop competing inputs after all choices validate.
server_pid="$(read_pid "$SERVERPID")"
if valid_pid "$server_pid" && tr '\0' ' ' <"/proc/$server_pid/cmdline" | grep -Fq 'camilladsp-server-sonobus.py'; then
    if [[ "$(cat "$REMOTE_STATE/audio-stop-capable.pid" 2>/dev/null || true)" != "$server_pid" ]]; then
        echo 'Webremote is running old code; restart the patched server before local selection.'
        exit 1
    fi
    rm -f -- "$REMOTE_STATE/audio-stop-complete"
    kill -USR1 "$server_pid" || { echo 'Could not release webremote audio engine.'; exit 1; }
    for _ in {1..100}; do [[ -f "$REMOTE_STATE/audio-stop-complete" ]] && break; sleep 0.1; done
    [[ -f "$REMOTE_STATE/audio-stop-complete" ]] || { echo 'Webremote did not release its audio engine.'; exit 1; }
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
wait_for_sink "$PHYSICAL_SINK" || { echo "Output disappeared before monitor start: $PHYSICAL_SINK"; exit 1; }
if ! start_local_monitor "$PHYSICAL_SINK"; then
    echo "Local playback monitor failed. See: $ACTION_LOG"
    exit 1
fi
printf 'laptop_laptop\n' >"$REMOTE_STATE/mode"
rm -f -- "$REMOTE_STATE/audio-stopped"  # successful switch clears the stopped marker
# Save route only after a successful start; webremote can reuse this route.
# Resolve the selected live sink's owning card via PipeWire device.id.
# If graph metadata is unavailable, leave ownership unknown rather than guessing.
TOPOLOGY_JSON="$(output_topology)" || fail 'Could not verify selected route'
SINK_CARD="$(jq -r --arg sink "$PHYSICAL_SINK" '[.cards[].sinks[] | select(.name==$sink) | .cardName][0] // ""' <<<"$TOPOLOGY_JSON")"
SINK_PROFILE="$(jq -r --arg card "$SINK_CARD" '[.cards[] | select(.name==$card) | .activeProfile][0] // ""' <<<"$TOPOLOGY_JSON")"
jq -n --arg card "$SELECTED_CARD" --arg profile "$SELECTED_PROFILE" --arg sinkCard "$SINK_CARD" --arg sinkProfile "$SINK_PROFILE" --arg sink "$PHYSICAL_SINK" --arg port "$SELECTED_PORT" --arg label "$(sink_display_label "$PHYSICAL_SINK")" '{card:$card,profile:$profile,sink:$sink,port:$port,label:$label,sinkCard:$sinkCard,sinkProfile:$sinkProfile}' >"$ROUTE_FILE.tmp.$$" &&
    mv -f "$ROUTE_FILE.tmp.$$" "$ROUTE_FILE"
normalize_audio_volumes || fail 'Could not restore saved master after normalization'
echo; echo "Device: $SELECTED_CARD_LABEL"
echo "Card profile: $SELECTED_PROFILE_LABEL"
echo "Physical output: $(sink_display_label "$PHYSICAL_SINK")"
echo "CamillaDSP: $(short_profile_name "$DSP_PROFILE")"
echo "Desktop default: $(pactl get-default-sink 2>/dev/null || true)"
pause_before_close
