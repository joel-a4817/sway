#!/usr/bin/env bash
set -u

HOME_DIR="${HOME:?HOME is required}"
STATE_DIR="$HOME_DIR/.local/state/sway/media-control"
REMOTE_STATE="$HOME_DIR/.local/state/sway/camilladsp-webremote"
LEGACY_STATE="$HOME_DIR/.local/state/sway/audio"

# Prevent overlapping selectors from changing the graph concurrently.
mkdir -p "$STATE_DIR" "$REMOTE_STATE"
# Do not migrate an active old server's PID/markers: restart the paired server first.
legacy_server_pid="$(cat "$LEGACY_STATE/camilladsp-webremote/web-server.pid" 2>/dev/null || true)"
if [[ "$legacy_server_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$legacy_server_pid" 2>/dev/null; then
    echo 'Restart the updated webremote server before using the updated media-control.' >&2
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
LAUNCH_LOCK="$STATE_DIR/media-control-launch.lock"
OWNER_FILE="$STATE_DIR/media-control-owner"
LOCK_FILE="$STATE_DIR/media-control.lock"
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
    [[ "$arg" == */audio-control.sh || "$arg" == audio-control.sh ]] || return 1
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
    echo 'Previous media-control or webremote startup still holds the audio lock.' >&2
    exit 1
fi
printf '%s %s\n' "$$" "$(process_start "$$")" >"$OWNER_FILE"
flock -u 8
exec 8>&-

ACTION_LOG="$STATE_DIR/media-control.log"
RESULT_FILE="$STATE_DIR/audio-toggle-complete.$$"
ACTION_STARTED_FILE="$STATE_DIR/audio-toggle-started.$$"
CARD_SELECTION_FILE="$STATE_DIR/audio-card-selected.$$"
SWAYNAG_LOG="$STATE_DIR/media-control-swaynag.$$"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
printf '[%s] media-control pid=%s\n' "$(date -Iseconds 2>/dev/null || date)" "$$" >"$ACTION_LOG"
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
    if [[ ${OUTPUT_PREVIEW_ACTIVE:-0} == 1 ]]; then
        python3 "$TOPOLOGY_SCRIPT" --cancel-output-preview >/dev/null 2>>"$ACTION_LOG" || echo 'Output preview rollback failed; inspect the log.' >&2
    fi
    if (( ! FINAL_PAUSE_REACHED && ! ${REPLACED:-0} )); then
        if (( status != 0 )); then
            echo; echo "Audio script exited unexpectedly."; echo "Exit status: $status"
            [[ -s "$ACTION_LOG" ]] && echo "Details: $ACTION_LOG"
        fi
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

TOPOLOGY_SCRIPT="$HOME_DIR/.config/sway/scripts/network/camilladsp-server-sonobus.py"
[[ -f "$TOPOLOGY_SCRIPT" ]] || { echo "Missing paired server: $TOPOLOGY_SCRIPT" >&2; exit 1; }
# Opening Audio Control itself runs the requested normalization transaction.
# The paired CLI pauses only currently-playing media, restores the saved master,
# then leaves playback paused before the menu appears.
if ! python3 "$TOPOLOGY_SCRIPT" --normalize-menu-open >>"$ACTION_LOG" 2>&1; then
    echo 'Audio normalization failed while opening Audio Control.' >&2
    echo "Details: $ACTION_LOG" >&2
    exit 1
fi
output_topology() { python3 "$TOPOLOGY_SCRIPT" --output-topology; }
run_quiet_action() {
    local output status
    output="$("$@" 2>&1)"; status=$?
    if (( status != 0 )); then
        printf '%s\n' "$output" >&2
        return "$status"
    fi
}
# Output devices are enumerated when Outputs is selected, not cached.
terminal_columns() {
    local columns
    columns="$(stty size </dev/tty 2>/dev/null | awk '{print $2}')"
    [[ "$columns" =~ ^[0-9]+$ && "$columns" -ge 20 ]] || columns="$(tput cols 2>/dev/null || true)"
    [[ "$columns" =~ ^[0-9]+$ && "$columns" -ge 20 ]] || columns="${COLUMNS:-80}"
    [[ "$columns" =~ ^[0-9]+$ && "$columns" -ge 20 ]] || columns=80
    printf '%s\n' "$columns"
}
wrap_line() {
    local text="$1" columns
    columns="$(terminal_columns)"
    printf '%s\n' "$text" | expand -t 4 | fold -s -w "$columns"
}
wrap_prefixed_line() {
    local prefix="$1" text="$2" columns width continuation line first=1
    columns="$(terminal_columns)"
    continuation="$(printf '%*s' "${#prefix}" '')"
    width=$((columns - ${#prefix}))
    ((width >= 1)) || width=1
    while IFS= read -r line || [[ -n "$line" ]]; do
        if ((first)); then printf '%s%s\n' "$prefix" "$line"; first=0
        else printf '%s%s\n' "$continuation" "$line"; fi
    done < <(printf '%s\n' "$text" | expand -t 4 | fold -s -w "$width")
}
compact_menu_label() {
    local text="$1"
    text="${text//Currently selected/Current}"
    text="${text//Last known/Last}"
    text="${text//Playback device/Device}"
    text="${text//Playback profile/Profile}"
    text="${text//Playback sink/Sink}"
    text="${text//Capture device/Device}"
    text="${text//Physical device/Device}"
    text="${text//Camera endpoint/Endpoint}"
    printf '%s' "$text"
}
# Presentation only: one status tag, while original labels retain validation.
compact_item_label() {
    local label="$1" tag='' prefix
    case "$label" in
        '[UNAVAILABLE] '*) tag='[N/A]'; label="${label#'[UNAVAILABLE] '}" ;;
        '[INTERNAL] '*) tag='[INT]'; label="${label#'[INTERNAL] '}" ;;
        '[MONITOR - NOT SELECTABLE] '*) tag='[MON]'; label="${label#'[MONITOR - NOT SELECTABLE] '}" ;;
        '[MONITOR] '*) tag='[MON]'; label="${label#'[MONITOR] '}" ;;
        '[LOOPBACK] '*) tag='[LOOP]'; label="${label#'[LOOPBACK] '}" ;;
        '[VIRTUAL] '*) tag='[VIRT]'; label="${label#'[VIRTUAL] '}" ;;
    esac
    while :; do
        case "$label" in
            '[UNAVAILABLE] '*) label="${label#'[UNAVAILABLE] '}" ;;
            '[INTERNAL] '*) label="${label#'[INTERNAL] '}" ;;
            '[MONITOR] '*) label="${label#'[MONITOR] '}" ;;
            '[MONITOR - NOT SELECTABLE] '*) label="${label#'[MONITOR - NOT SELECTABLE] '}" ;;
            '[LOOPBACK] '*) label="${label#'[LOOPBACK] '}" ;;
            '[VIRTUAL] '*) label="${label#'[VIRTUAL] '}" ;;
            *) break ;;
        esac
    done
    label="${label% (current)}"
    label="${label/Monitor of /}"
    [[ -n "$tag" ]] && printf '%s %s' "$tag" "$label" || printf '%s' "$label"
}
print_menu_item() {
    local text tag='' label
    text="$(compact_item_label "$2")"
    case "$text" in
        '[N/A] '*|'[INT] '*|'[MON] '*|'[LOOP] '*|'[VIRT] '*)
            tag="${text%% *} "; text="${text#* }" ;;
    esac
    wrap_prefixed_line "${tag}[$1] " "$text"
}
choose_index() {
    local title="$1" answer i=0; shift
    ((${#@})) || { echo "No choices for $title" >&2; return 1; }
    echo; wrap_line "$(compact_menu_label "$title")"; echo
    for label in "$@"; do print_menu_item "$i" "$label"; i=$((i+1)); done
    while :; do
        read -r -p 'Select: ' answer || return 1
        [[ "$answer" =~ ^[0-9]+$ && ${#answer} -le 9 ]] || { wrap_line 'Enter a listed number.'; continue; }
        number=$((10#$answer))
        if ((number < i)); then
            local choice="${@:$((number+1)):1}"
            case "$choice" in
                '[UNAVAILABLE] '*|'[INTERNAL] '*|'[MONITOR] '*|'[MONITOR - NOT SELECTABLE] '*|'[LOOPBACK] '*|'[VIRTUAL] '*)
                    wrap_line 'That labeled item is not selectable. Select again.'; continue ;;
            esac
            return 0
        fi
        wrap_line 'Enter a listed number.'
    done
}
# Capture control uses both PulseAudio compatibility data and PipeWire's graph.
# pactl supplies readable audio source ports and movable recording streams;
# pw-dump supplies physical camera/device relationships; wpctl commits defaults.
camera_topology() {
    pw-dump | python3 -c '
import json,sys
x=json.load(sys.stdin); objs={o.get("id"):o for o in x}; devices={}; defaults=set()
for o in x:
 p=o.get("info",{}).get("props",{}); c=p.get("media.class","")
 if c=="Video/Source":
  did=p.get("device.id"); d=objs.get(did,{}) if isinstance(did,int) else {}
  dp=d.get("info",{}).get("props",{})
  physical=str(did) if isinstance(did,int) else "node:"+str(o.get("id"))
  dl=dp.get("device.description") or dp.get("device.nick") or dp.get("device.product.name") or p.get("device.description") or "Camera"
  nl=p.get("node.description") or p.get("node.nick") or p.get("node.name") or str(o.get("id"))
  devices.setdefault(physical,{"key":physical,"label":dl,"nodes":[]})["nodes"].append({"id":o.get("id"),"name":p.get("node.name",""),"label":nl})
print(json.dumps(list(devices.values())))'
}
camera_picker() (
    local topo current device_key device_label node_id node_name node_label i
    local -a device_keys=() device_labels=() node_ids=() node_names=() node_labels=()
    topo="$(camera_topology)" || return 1
    mapfile -t device_keys < <(jq -r '.[].key' <<<"$topo"); mapfile -t device_labels < <(jq -r '.[].label' <<<"$topo")
    ((${#device_keys[@]})) || { wrap_line 'No physical cameras are exposed.'; return 1; }
    current="$(wpctl list video sources | awk -F '\t' '$4=="*"{print $2;exit}')"
    current_device=''; current_device_label=''
    for i in "${!device_keys[@]}"; do
      jq -e --arg k "${device_keys[i]}" --arg n "$current" '.[]|select(.key==$k)|.nodes[]|select(.name==$n)' <<<"$topo" >/dev/null && { current_device="${device_keys[i]}"; current_device_label="${device_labels[i]}"; }
    done
    while :; do
    choose_index 'Cameras | Physical device' "Current${current_device_label:+ ($(compact_item_label "$current_device_label"))}" "${device_labels[@]}" || return 1
    if ((number==0)); then [[ -n "$current_device" ]] || { wrap_line 'No current camera is exposed; select a numbered camera.'; continue; }; device_key="$current_device"; device_label="$current_device_label"
    else device_key="${device_keys[number-1]}"; device_label="${device_labels[number-1]}"; fi
    break
    done
    # Device Select applies its last endpoint before the endpoint menu.
    local camera_committed=0 original_camera_id='' camera_clients='[]'
    trap 'if (( ! camera_committed )); then if [[ "$camera_clients" != "[]" ]]; then python3 "$TOPOLOGY_SCRIPT" --camera-restore-clients "$camera_clients" >/dev/null || echo "Camera client rollback failed" >&2; fi; if [[ "$original_camera_id" =~ ^[0-9]+$ ]]; then wpctl set-default "$original_camera_id" >/dev/null 2>&1 || echo "Camera rollback failed" >&2; fi; fi' EXIT
    original_camera="$current"
    original_camera_id="$(jq -r --arg n "$original_camera" '.[]|.nodes[]|select(.name==$n)|.id' <<<"$topo" | head -n 1)"
    [[ "$original_camera_id" =~ ^[0-9]+$ ]] || { wrap_line 'Cannot identify the current camera for rollback; no camera change was made.' >&2; return 1; }
    camera_last="$STATE_DIR/camera-last-endpoints.json"
    remembered_node="$(jq -r --arg k "$device_key" '.[$k] // empty' "$camera_last" 2>/dev/null || true)"
    stage_node="$(jq -r --arg k "$device_key" --arg n "$remembered_node" '.[]|select(.key==$k)|.nodes[]|select(.name==$n)|.id' <<<"$topo" | head -n 1)"
    [[ "$stage_node" =~ ^[0-9]+$ ]] || stage_node="$(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[0].id // empty' <<<"$topo")"
    [[ "$stage_node" =~ ^[0-9]+$ ]] || { wrap_line 'Selected camera has no endpoint.' >&2; return 1; }
    wpctl set-default "$stage_node" || return 1
    stage_name="$(jq -r --arg k "$device_key" --argjson id "$stage_node" '.[]|select(.key==$k)|.nodes[]|select(.id==$id)|.name' <<<"$topo")"
    wpctl list video sources | awk -F '\t' -v n="$stage_name" '$2==n && $4=="*"{ok=1} END{exit !ok}' || { wrap_line 'Camera device stage did not become default.' >&2; return 1; }
    current="$stage_name"
    mapfile -t node_ids < <(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[].id' <<<"$topo")
    mapfile -t node_names < <(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[].name' <<<"$topo")
    mapfile -t node_labels < <(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[].label' <<<"$topo")
    while :; do
      current_node_label=''; for i in "${!node_names[@]}"; do [[ "${node_names[i]}" == "$current" ]] && current_node_label="${node_labels[i]}"; done
      choose_index "$device_label | Camera endpoint" "Current${current_node_label:+ ($(compact_item_label "$current_node_label"))}" "${node_labels[@]}" || { [[ "$original_camera_id" =~ ^[0-9]+$ ]] && wpctl set-default "$original_camera_id" >/dev/null 2>&1; return 1; }
      if ((number==0)); then
        if [[ -z "$current_node_label" ]]; then wrap_line 'No current endpoint belongs to this camera. Select again.'; continue; fi
        for i in "${!node_names[@]}"; do [[ "${node_names[i]}" == "$current" ]] && node_id="${node_ids[i]}" && node_name="${node_names[i]}" && node_label="${node_labels[i]}"; done
      else node_id="${node_ids[number-1]}"; node_name="${node_names[number-1]}"; node_label="${node_labels[number-1]}"; fi
      break
    done
    # Revalidate against a fresh graph, then set and confirm the default.
    fresh="$(camera_topology)" || return 1
    jq -e --arg k "$device_key" --argjson id "$node_id" --arg n "$node_name" '.[]|select(.key==$k)|.nodes[]|select(.id==$id and .name==$n)' <<<"$fresh" >/dev/null || { wrap_line 'Camera topology changed; reopen Media Control.'; return 1; }
    old_camera_id="$(jq -r --arg n "$original_camera" '.[] | .nodes[] | select(.name==$n) | .id' <<<"$topo" | head -n 1)"
    wpctl set-default "$node_id" || return 1
    if ! wpctl list video sources | awk -F '\t' -v n="$node_name" '$2==n && $4=="*"{ok=1} END{exit !ok}'; then
        [[ "$old_camera_id" =~ ^[0-9]+$ ]] && wpctl set-default "$old_camera_id" || true
        wrap_line 'Camera default change could not be confirmed.'; return 1
    fi
    if ! camera_status="$(python3 "$TOPOLOGY_SCRIPT" --camera-selection-status "$node_id")"; then
        [[ "$old_camera_id" =~ ^[0-9]+$ ]] && wpctl set-default "$old_camera_id" || true
        return 1
    fi
    camera_clients="$(jq -c '.previousClients // []' <<<"$camera_status")"
    if [[ -s "$camera_last" ]]; then camera_saved="$(cat "$camera_last")"; else camera_saved='{}'; fi
    if ! jq -n --argjson old "$camera_saved" --arg k "$device_key" --arg n "$node_name" '$old + {($k):$n}' >"$camera_last.tmp" || ! mv -- "$camera_last.tmp" "$camera_last"; then
        rm -f -- "$camera_last.tmp"
        wrap_line 'Could not save camera endpoint; restoring previous camera.' >&2
        return 1
    fi
    camera_committed=1
    wrap_line "Camera selected: $device_label${node_label:+ | $node_label}"
)
ARGS=(-t warning -y overlay -m 'Media control')
ARGS+=( -z 'Audio services' "printf '%s\n' audio-services > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
ARGS+=( -z 'CamillaDSP filters' "printf '%s\n' select-filter > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
ARGS+=( -z 'Audio input' "printf '%s\n' select-input > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
ARGS+=( -z 'Camera' "printf '%s\n' select-camera > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
ARGS+=( -z 'Audio output' "printf '%s\n' select-output > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
swaynag "${ARGS[@]}" >"$SWAYNAG_LOG" 2>&1 9>&- &
SWAYNAG_PID=$!
while [[ ! -e "$RESULT_FILE" ]]; do
    if ! kill -0 "$SWAYNAG_PID" 2>/dev/null; then
        wait "$SWAYNAG_PID" || { cat "$SWAYNAG_LOG" >&2; exit 1; }
        [[ -e "$RESULT_FILE" ]] || { echo 'No audio action selected.'; exit 0; }
    fi
    sleep 0.1
done
kill "$SWAYNAG_PID" 2>/dev/null || true
wait "$SWAYNAG_PID" 2>/dev/null || true
selected="$(cat "$CARD_SELECTION_FILE")"
case "$selected" in
 select-output)
    if ! preview_error="$(MEDIA_CONTROL_PICKER_PID=$$ MEDIA_CONTROL_PICKER_START="$(process_start "$$")" python3 "$TOPOLOGY_SCRIPT" --begin-output-preview 2>&1 >/dev/null)"; then
        printf '%s\n' "${preview_error##*$'\n'}" >&2
        exit 1
    fi
    export MEDIA_CONTROL_PICKER_PID=$$
    OUTPUT_PREVIEW_ACTIVE=1
    TOPOLOGY_JSON="$(output_topology)" || { wrap_line 'Output topology unavailable.' >&2; exit 1; }
    mapfile -t CARDS < <(jq -r '.cards[] | [.name,.label] | @tsv' <<<"$TOPOLOGY_JSON")
    ((${#CARDS[@]})) || { wrap_line 'No playback devices currently exposed.' >&2; exit 1; }
    labels=()
    for item in "${CARDS[@]}"; do labels+=("${item#*$'\t'}"); done
    current_card="$(jq -r '.saved.card // empty' <<<"$TOPOLOGY_JSON")"
    current_card_index=''
    current_card_label=''
    for i in "${!CARDS[@]}"; do
        if [[ "${CARDS[i]%%$'\t'*}" == "$current_card" ]]; then
            current_card_index="$i"; current_card_label="${labels[i]}"; break
        fi
    done
    while :; do
        choose_index 'Audio outputs | Playback device' "Current${current_card_label:+ ($(compact_item_label "$current_card_label"))}" "${labels[@]}" || exit 0
        if ((number == 0)); then
            [[ -n "$current_card_index" ]] || { wrap_line 'No current output is exposed; select a numbered device.'; continue; }
            selected="$current_card_index"
        else selected="$((number-1))"; fi
        [[ "$(jq -r --arg n "${CARDS[selected]%%$'\t'*}" '.cards[]|select(.name==$n)|.internal // false' <<<"$TOPOLOGY_JSON")" != true ]] && break
        wrap_line 'That device is internal. Select an available playback device.'
    done
    ;;
 select-input)
    python3 -c '
import importlib.util, json, sys
spec=importlib.util.spec_from_file_location("media_control_backend",sys.argv[1])
backend=importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
def blocked(row):
    text=str(row.get("label") or row.get("name") or "")
    return (not backend._available_option(row) or
            text.startswith(("[UNAVAILABLE] ","[INTERNAL] ","[MONITOR] ","[LOOPBACK] ","[VIRTUAL] ")))
def display(row):
    text=str(row.get("label") or row.get("name") or row.get("index") or "")
    tags=("[UNAVAILABLE] ","[INTERNAL] ","[MONITOR - NOT SELECTABLE] ",
          "[MONITOR] ","[LOOPBACK] ","[VIRTUAL] ")
    tag=next((t for t in tags if text.startswith(t)),"")
    if tag:text=text[len(tag):]
    while any(text.startswith(t) for t in tags):
        text=text[len(next(t for t in tags if text.startswith(t))):]
    if text.startswith("Monitor of "):text=text[len("Monitor of "):]
    status={"[UNAVAILABLE] ":"N/A","[INTERNAL] ":"INT",
            "[MONITOR - NOT SELECTABLE] ":"MON","[MONITOR] ":"MON",
            "[LOOPBACK] ":"LOOP","[VIRTUAL] ":"VIRT"}.get(tag)
    if not status and not backend._available_option(row):status="N/A"
    return ("["+status+"] " if status else "")+text
def wrapped(prefix,text):
    import shutil,textwrap
    width=shutil.get_terminal_size(fallback=(80,24)).columns
    available=max(1,width-len(prefix))
    for line in str(text).splitlines() or [""]:
        pieces=textwrap.wrap(line,width=available,break_long_words=True,
                             break_on_hyphens=False,replace_whitespace=False,
                             drop_whitespace=True) or [""]
        for index,piece in enumerate(pieces):
            print((prefix if index==0 else " "*len(prefix))+piece)
def pick(label,rows,current=None):
    if label=="Audio input | Device":
        result=backend.run([backend.exe("pactl"),"get-default-source"],False,5,backend.media_env())
        if result.returncode:raise RuntimeError("Cannot read current input source")
        source=result.stdout.strip()
        current=next((row["name"] for row in rows if any(
            node["name"]==source for node in row["sources"])),None)
    if not rows:raise RuntimeError("No "+label+" choices are exposed")
    selected=next((row for row in rows if str(row.get("index",row.get("name")))==str(current)
                   and not blocked(row)),None) if current is not None else None
    print()
    wrapped("",label)
    if selected:
        wrapped("[0] ","Current ("+display(selected)+")")
    else:
        wrapped("[N/A] [0] ","Current")
    for number,row in enumerate(rows,1):
        text=display(row)
        tag=next((t for t in ("[N/A] ","[INT] ","[MON] ","[LOOP] ","[VIRT] ")
                  if text.startswith(t)),"")
        wrapped("{}[{}] ".format(tag,number),text[len(tag):])
    while True:
        answer=input("Select: ").strip()
        if answer.isdecimal():
            number=int(answer)
            if number==0 and selected is not None:return selected
            if 1<=number<=len(rows) and not blocked(rows[number-1]):return rows[number-1]
        print("Select an available number.")

def staged_pick(label,rows,current=None):
    selected=pick(label,rows,current)
    if label=='Audio input | Device':
        saved_playback=backend.saved_output_route()
        if saved_playback.get('card')==selected['name'] and not backend.STOPPED.exists():
            live_playback=backend._find_card(backend.audio_topology(),selected['name'])
            backend.MEDIA_TRANSACTION.input_playback_before={
                'profile':live_playback['activeProfile'],
                'routes':list(live_playback['activeRoutes']),
                'monitor':backend.alive(backend.rpid(backend.LOCALMONPID))}
        backend.MEDIA_TRANSACTION.input_original=backend._input_card(selected['name'])
        backend.MEDIA_TRANSACTION.input_ports=backend._input_stage_port_snapshot(selected['name'])
        backend.MEDIA_TRANSACTION.input_default=backend.run([backend.exe('pactl'),'get-default-source'],False,5,backend.media_env()).stdout.strip()
        backend.MEDIA_TRANSACTION.input_stream_origins=backend.input_stream_origins()
        backend._input_stage_card=selected['name']
    elif label=='Audio input | Profile':
        backend._input_stage(backend._input_stage_card,selected['index'])
    elif label=='Audio input | Route':
        backend._input_stage(backend._input_stage_card,backend._input_stage_profile,selected['index'])
    if label=='Audio input | Profile':backend._input_stage_profile=selected['index']
    return selected
backend._pick_input=staged_pick
from contextlib import nullcontext
backend.media_change=nullcontext
try:
    def finalize():
        if not backend.STOPPED.exists():backend.normalize_audio_volumes()
    result=backend.select_input_interactive(finalize=finalize)
    print(json.dumps(result,ensure_ascii=False))
except KeyboardInterrupt:
    sys.exit(130)
except Exception as error:
    print(str(error),file=sys.stderr)
    sys.exit(1)
finally:
    backend.pause_for_normalization()
' "$TOPOLOGY_SCRIPT" || exit $?
    exit 0 ;;
 select-camera)
    camera_picker || exit $?
    exit 0 ;;
 audio-services)
    choose_index 'Audio services' \
        'Stop / start audio' \
        'Restart CamillaDSP' \
        'Restart SonoBus' \
        'Restart AirPlay' \
        'Restart VNC' || exit 0
    flock -u 9
    if [[ "$number" == 0 ]]; then
        run_quiet_action python3 "$TOPOLOGY_SCRIPT" --audio-toggle || exit $?
        wrap_line 'Audio stop/start toggled.'
    else
        run_quiet_action python3 "$TOPOLOGY_SCRIPT" --restart-service "$number" || exit $?
        case "$number" in
            1) echo 'CamillaDSP restarted.' ;;
            2) echo 'SonoBus restarted.' ;;
            3) echo 'AirPlay restarted.' ;;
            4) echo 'VNC restarted.' ;;
        esac
    fi
    exit 0 ;;
 select-filter)
    profiles="$(python3 "$TOPOLOGY_SCRIPT" --dsp-profiles)" || exit 1
    info="$(python3 "$TOPOLOGY_SCRIPT" --dsp-filter-info)" || exit 1
    mapfile -t filters < <(jq -r '.[] | select((ascii_downcase|contains("cmf"))|not)' <<<"$profiles")
    ((${#filters[@]})) || { echo 'No listening filters found.' >&2; exit 1; }
    filter_device_label() {
        local filter="$1" room_label="$2" group
        group="$(jq -r --arg f "$filter" '.[$f].group // "Other"' <<<"$info")"
        [[ "$group" == Other ]] && printf '%s\n' "$room_label" || printf '%s | %s\n' "$group" "$room_label"
    }
    labels=()
    for filter in "${filters[@]}"; do
        room_label="$(jq -r --arg f "$filter" '.[$f].label // $f' <<<"$info")"
        labels+=("$(filter_device_label "$filter" "$room_label")")
    done
    current_filter="$(python3 "$TOPOLOGY_SCRIPT" --selected-filter 2>/dev/null || true)"
    current_room_label="$(jq -r --arg f "$current_filter" '.[$f].label // $f' <<<"$info")"
    current_label="$(filter_device_label "$current_filter" "$current_room_label")"
    if ! printf '%s\n' "${filters[@]}" | grep -Fqx -- "$current_filter"; then
        echo 'The currently selected filter is not available.' >&2
        exit 1
    fi
    filter_choices=("Current filter ($current_label)" "${labels[@]}")
    choose_index 'Listening filter' "${filter_choices[@]}" || exit 0
    answer="$number"
    if ((number == 0)); then
        selected_filter="$current_filter"
        selected_label="$current_label"
    else
        selected_filter="${filters[$((10#$answer-1))]}"
        selected_label="${labels[$((10#$answer-1))]}"
    fi
    flock -u 9
    run_quiet_action python3 "$TOPOLOGY_SCRIPT" --select-filter "$selected_filter" || exit $?
    wrap_line "Listening filter applied: $selected_label"
    exit 0 ;;
esac
[[ "$selected" =~ ^[0-9]+$ ]] && ((selected < ${#CARDS[@]})) || { echo 'Invalid device selection' >&2; exit 1; }
card="${CARDS[selected]%%$'\t'*}"
card_label="${CARDS[selected]#*$'\t'}"
# Refresh after the Swaynag selection, without changing the live graph.
TOPOLOGY_JSON="$(output_topology)" || { echo "Start audio services before choosing a playback output." >&2; exit 1; }
# Device selection is a real output apply, exactly like the web picker.
# Reuse the active profile if usable, otherwise the remembered usable profile,
# then the first exposed usable profile. The paired controller owns pause and
# normalization for this stage, including when the web host is not running.
TOPOLOGY_JSON="$(python3 "$TOPOLOGY_SCRIPT" --apply-output-device "$card")" || {
    wrap_line 'Playback device could not be applied.' >&2; exit 1;
}
active="$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.activeProfile // empty' <<<"$TOPOLOGY_JSON")"
mapfile -t profiles < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.profiles[]|.index' <<<"$TOPOLOGY_JSON")
last_profile="$(jq -r '.stageSelection.profile // empty' <<<"$TOPOLOGY_JSON")"
printf '%s\n' "${profiles[@]}" | grep -Fqx -- "$last_profile" || last_profile="$active"
printf '%s\n' "${profiles[@]}" | grep -Fqx -- "$last_profile" || last_profile=''
last_profile_label="$(jq -r --arg n "$card" --arg p "$last_profile" '.cards[]|select(.name==$n)|.profiles[]|select((.index|tostring)==$p)|.label' <<<"$TOPOLOGY_JSON")"
labels=("Last profile${last_profile_label:+ ($last_profile_label)}")
for profile in "${profiles[@]}"; do
    labels+=("$(jq -r --arg n "$card" --arg p "$profile" '.cards[]|select(.name==$n)|.profiles[]|select((.index|tostring)==$p)|(if (.available=="no" or .available=="false" or ((.name|ascii_downcase)=="off")) then "[UNAVAILABLE] " else "" end) + .label' <<<"$TOPOLOGY_JSON")")
done
while :; do
    choose_index "Card: $card_label | Playback profile" "${labels[@]}" || exit 0
    if ((number==0)); then
        [[ -n "$last_profile" ]] || { wrap_line 'The last known profile is unavailable. Select again.'; continue; }
        profile="$last_profile"
    else profile="${profiles[number-1]}"; fi
    availability="$(jq -r --arg n "$card" --arg p "$profile" '.cards[]|select(.name==$n)|.profiles[]|select((.index|tostring)==$p)|.available' <<<"$TOPOLOGY_JSON")"
    [[ "$availability" != no && "$availability" != false && "$(jq -r --arg n "$card" --arg p "$profile" '.cards[]|select(.name==$n)|.profiles[]|select((.index|tostring)==$p)|.name' <<<"$TOPOLOGY_JSON")" != off ]] && break
    wrap_line 'That playback profile is unavailable. Select again.'
done
# The selected profile is applied now so its actual sinks/routes appear next.
TOPOLOGY_JSON="$(python3 "$TOPOLOGY_SCRIPT" --apply-output-profile "$card" "$profile")" || { wrap_line 'Playback profile could not be applied.' >&2; exit 1; }
active="$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.activeProfile // empty' <<<"$TOPOLOGY_JSON")"
mapfile -t routes < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.routes[].index' <<<"$TOPOLOGY_JSON")
route=''
last_route="$(jq -r '.stageSelection.port // empty' <<<"$TOPOLOGY_JSON")"
printf '%s\n' "${routes[@]}" | grep -Fqx -- "$last_route" || last_route="$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.activeRoutes[0] // empty' <<<"$TOPOLOGY_JSON")"
printf '%s\n' "${routes[@]}" | grep -Fqx -- "$last_route" || last_route=''
last_route_label="$(jq -r --arg n "$card" --arg r "$last_route" '.cards[]|select(.name==$n)|.routes[]|select((.index|tostring)==$r)|.label' <<<"$TOPOLOGY_JSON")"
labels=("Last route${last_route_label:+ ($last_route_label)}")
if ((${#routes[@]})); then
    for r in "${routes[@]}"; do
        labels+=("$(jq -r --arg n "$card" --arg p "$profile" --arg r "$r" '.cards[]|select(.name==$n)|.routes[]|select((.index|tostring)==$r)|(if (.available=="no" or .available=="false" or ((.name|ascii_downcase)=="off") or ((.profiles|length)>0 and ([.profiles[]|tostring]|index($p))==null)) then "[UNAVAILABLE] " else "" end) + .label' <<<"$TOPOLOGY_JSON")")
    done
else
    labels+=("[UNAVAILABLE] No output routes exposed")
fi
if ((${#routes[@]})); then
while :; do
    choose_index 'Output route' "${labels[@]}" || exit 0
    if ((number==0)); then
        [[ -n "$last_route" ]] || { wrap_line 'The last known route is unavailable. Select again.'; continue; }
        candidate="$last_route"
    else
        ((number-1 < ${#routes[@]})) || { wrap_line 'That output route is unavailable. Select again.'; continue; }
        candidate="${routes[number-1]}"
    fi
    usable="$(jq -r --arg n "$card" --arg p "$profile" --arg r "$candidate" '.cards[]|select(.name==$n)|.routes[]|select((.index|tostring)==$r)|(.available!="no" and .available!="false" and ((.name|ascii_downcase)!="off") and ((.profiles|length)==0 or ([.profiles[]|tostring]|index($p))!=null))' <<<"$TOPOLOGY_JSON")"
    [[ "$usable" == true ]] && { route="$candidate"; break; }
    wrap_line 'That output route is unavailable for this profile. Select again.'
done
else
    wrap_line 'No output routes exposed for this device; continuing to playback sinks.'
fi

if [[ -n "$route" ]]; then
    TOPOLOGY_JSON="$(python3 "$TOPOLOGY_SCRIPT" --apply-output-route "$card" "$profile" "$route")" || { wrap_line 'Output route could not be applied.' >&2; exit 1; }
fi
# 0 keeps the last known still-exposed sink; numbered items are live sinks.
sink=''
mapfile -t sinks < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.sinks[].name' <<<"$TOPOLOGY_JSON")
last_sink="$(jq -r '.stageSelection.sink // empty' <<<"$TOPOLOGY_JSON")"
if [[ -z "$last_sink" && "$(jq -r '.saved.card // empty' <<<"$TOPOLOGY_JSON")" == "$card" ]]; then
    last_sink="$(jq -r '.saved.sink // empty' <<<"$TOPOLOGY_JSON")"
fi
last_sink_label="$(jq -r --arg n "$card" --arg s "$last_sink" '.cards[]|select(.name==$n)|.sinks[]|select(.name==$s)|.label' <<<"$TOPOLOGY_JSON")"
labels=("Last sink${last_sink_label:+ ($last_sink_label)}")
for x in "${sinks[@]}"; do
    labels+=("$(jq -r --arg n "$card" --arg s "$x" --arg r "$route" '
      .cards[]|select(.name==$n) as $card|.sinks[]|select(.name==$s) as $sink|
      ([$card.routes[]|select((.index|tostring)==$r)|.devices[]|tostring]) as $devices|
      (if ($sink.internal or (($devices|length)>0 and $sink.profileDevice != null and
            ($devices|index($sink.profileDevice|tostring))==null))
       then "[UNAVAILABLE] " else "" end)+$sink.label' <<<"$TOPOLOGY_JSON")")
done
((${#sinks[@]})) || { wrap_line 'No playback sinks exposed by the selected profile.' >&2; exit 1; }
while :; do
    choose_index 'Playback sink' "${labels[@]}" || exit 0
    if ((number==0)); then
        [[ -n "$last_sink" ]] || { wrap_line 'No last known sink is exposed. Select again.'; continue; }
        candidate="$last_sink"
        idx=''
        for i in "${!sinks[@]}"; do [[ "${sinks[i]}" == "$candidate" ]] && idx="$((i+1))"; done
        [[ -n "$idx" ]] || { wrap_line 'Last known sink is no longer exposed. Select again.'; continue; }
    else
        ((number-1 < ${#sinks[@]})) || { wrap_line 'Select an exposed sink.'; continue; }
        idx="$number";candidate="${sinks[number-1]}"
    fi
    [[ "${labels[idx]}" != '[UNAVAILABLE] '* && "${labels[idx]}" != '[INTERNAL] '* ]] && { sink="$candidate"; break; }
    wrap_line 'That sink cannot be selected. Select again.'
done

request="$(jq -nc --arg card "$card" --arg profile "$profile" --arg port "$route" --arg sink "$sink" '{card:$card,profile:$profile,port:$port,sink:$sink}')"
# The paired controller serializes pause -> output -> normalize; playback stays paused.
flock -u 9
run_quiet_action python3 "$TOPOLOGY_SCRIPT" --switch-output "$request" || exit $?
OUTPUT_PREVIEW_ACTIVE=0
result_line="Playback output applied: $card_label"
[[ -n "${last_sink_label:-}" && "$sink" == "${last_sink:-}" ]] && result_line+=" / $last_sink_label"
wrap_line "$result_line"
