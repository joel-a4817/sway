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
    if (( ! FINAL_PAUSE_REACHED && ! ${REPLACED:-0} )); then
        if (( status != 0 )); then
            echo; echo "Audio script exited unexpectedly."; echo "Exit status: $status"
            [[ -s "$ACTION_LOG" ]] && { echo; echo "Action log:"; echo "$ACTION_LOG"; echo; tail -n 45 "$ACTION_LOG"; }
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
# The server pauses only currently-playing media, restores the saved master,
# then resumes only that snapshot before the menu appears.
if ! python3 "$TOPOLOGY_SCRIPT" --normalize-menu-open >>"$ACTION_LOG" 2>&1; then
    echo 'Audio normalization failed while opening Audio Control.' >&2
    tail -n 25 "$ACTION_LOG" >&2
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
# Selection does not pause or mutate. The server owns pause, routing,
# normalization, and resume as one transaction after the final choice.
TOPOLOGY_JSON="$(output_topology 2>/dev/null)" || TOPOLOGY_JSON='{"cards":[]}'
mapfile -t CARDS < <(jq -r '.cards[] | [.name,.label] | @tsv' <<<"$TOPOLOGY_JSON")
if ((${#CARDS[@]})); then printf '%s\n' "${CARDS[@]}" > "$STATE_DIR/audio-control-cards-last.txt"
elif [[ -s "$STATE_DIR/audio-control-cards-last.txt" ]]; then mapfile -t CARDS < "$STATE_DIR/audio-control-cards-last.txt"; fi
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
    ((width >= 12)) || width=12
    while IFS= read -r line || [[ -n "$line" ]]; do
        if ((first)); then printf '%s%s\n' "$prefix" "$line"; first=0
        else printf '%s%s\n' "$continuation" "$line"; fi
    done < <(printf '%s\n' "$text" | expand -t 4 | fold -s -w "$width")
}
print_menu_item() {
    wrap_prefixed_line "[$1] " "$2"
}
choose_index() {
    local title="$1" answer i=0; shift
    ((${#@})) || { echo "No choices for $title" >&2; return 1; }
    echo; wrap_line "$title"; echo
    for label in "$@"; do print_menu_item "$i" "$label"; i=$((i+1)); done
    while :; do
        read -r -p 'Select: ' answer || return 1
        [[ "$answer" =~ ^[0-9]+$ && ${#answer} -le 9 ]] || { wrap_line 'Enter a listed number.'; continue; }
        number=$((10#$answer))
        ((number < i)) && return 0
        wrap_line 'Enter a listed number.'
    done
}
ARGS=(-t warning -y overlay -m 'Choose card, then playback profile')
ARGS+=( -z 'Audio services' "printf '%s\n' audio-services > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
ARGS+=( -z 'CamillaDSP filters' "printf '%s\n' select-filter > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
for i in "${!CARDS[@]}"; do
    ARGS+=( -z "${CARDS[i]#*$'\t'}" "printf '%s\n' '$i' > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
done
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
 audio-services)
    choose_index 'Audio services' \
        'Audio stop / start (toggle)' \
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
        local filter="$1" room_label="$2" key
        key="${filter,,}"
        case "$key" in
            *cloud3*) printf 'HyperX Cloud III | %s\n' "$room_label" ;;
            *earpods*) printf 'Apple EarPods | %s\n' "$room_label" ;;
            *filterless*) printf '%s\n' "$room_label" ;;
            *) printf '%s\n' "$room_label" ;;
        esac
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
    filter_choices=("Currently selected filter ($current_label)" "${labels[@]}")
    choose_index 'CamillaDSP listening filter' "${filter_choices[@]}" || exit 0
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
active="$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.activeProfile // empty' <<<"$TOPOLOGY_JSON")"
mapfile -t profiles < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.profiles[]|select(.available!="no" and .available!="false" and ((.name|ascii_downcase)!="off"))|.index' <<<"$TOPOLOGY_JSON")
last_profile="$(python3 "$TOPOLOGY_SCRIPT" --remembered-profile "$card" 2>/dev/null || true)"
printf '%s\n' "${profiles[@]}" | grep -Fqx -- "$last_profile" || last_profile="$active"
printf '%s\n' "${profiles[@]}" | grep -Fqx -- "$last_profile" || last_profile=''
last_profile_label="$(jq -r --arg n "$card" --arg p "$last_profile" '.cards[]|select(.name==$n)|.profiles[]|select((.index|tostring)==$p)|.label' <<<"$TOPOLOGY_JSON")"
labels=("Last known profile${last_profile_label:+ ($last_profile_label)}")
for profile in "${profiles[@]}"; do
    labels+=("$(jq -r --arg n "$card" --arg p "$profile" '.cards[]|select(.name==$n)|.profiles[]|select((.index|tostring)==$p)|.label' <<<"$TOPOLOGY_JSON")")
done
choose_index "Card: $card_label | Playback profile" "${labels[@]}" || exit 0
if ((number==0)); then
    [[ -n "$last_profile" ]] || { echo 'No last known available profile for this device.' >&2; exit 1; }
    profile="$last_profile"
else profile="${profiles[number-1]}"; fi
mapfile -t routes < <(jq -r --arg n "$card" --arg p "$profile" '.cards[]|select(.name==$n)|.routes[]|select(.available!="no" and .available!="false" and ((.profiles|length)==0 or ([.profiles[]|tostring]|index($p))!=null))|.index' <<<"$TOPOLOGY_JSON")
route=''
if ((${#routes[@]})); then
    remembered_sink="$(python3 "$TOPOLOGY_SCRIPT" --remembered-sink "$card" "$profile" 2>/dev/null || true)"
    last_route="$(python3 "$TOPOLOGY_SCRIPT" --remembered-port "$card" "$profile" "$remembered_sink" 2>/dev/null || true)"
    printf '%s\n' "${routes[@]}" | grep -Fqx -- "$last_route" || last_route="$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.activeRoutes[0] // empty' <<<"$TOPOLOGY_JSON")"
    printf '%s\n' "${routes[@]}" | grep -Fqx -- "$last_route" || last_route=''
    last_route_label="$(jq -r --arg n "$card" --arg r "$last_route" '.cards[]|select(.name==$n)|.routes[]|select((.index|tostring)==$r)|.label' <<<"$TOPOLOGY_JSON")"
    labels=("Last known route${last_route_label:+ ($last_route_label)}")
    for r in "${routes[@]}"; do
        labels+=("$(jq -r --arg n "$card" --arg r "$r" '.cards[]|select(.name==$n)|.routes[]|select((.index|tostring)==$r)|.label' <<<"$TOPOLOGY_JSON")")
    done
    choose_index 'Output route' "${labels[@]}" || exit 0
    if ((number>0)); then route="${routes[number-1]}"; else
        [[ -n "$last_route" ]] || { echo 'No last known available route for this profile.' >&2; exit 1; }
        route="$last_route"
    fi
fi
# 0 selects the last known still-exposed sink; numbered items are live sinks.
saved="$(jq -r '.saved.sink // empty' <<<"$TOPOLOGY_JSON")"
sink=''
if [[ "$profile" == "$active" ]]; then
    mapfile -t sinks < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.sinks[].name' <<<"$TOPOLOGY_JSON")
    last_sink="$(python3 "$TOPOLOGY_SCRIPT" --remembered-sink "$card" "$profile" 2>/dev/null || true)"
    printf '%s\n' "${sinks[@]}" | grep -Fqx -- "$last_sink" || last_sink="$saved"
    printf '%s\n' "${sinks[@]}" | grep -Fqx -- "$last_sink" || last_sink=''
    last_sink_label="$(jq -r --arg n "$card" --arg s "$last_sink" '.cards[]|select(.name==$n)|.sinks[]|select(.name==$s)|.label' <<<"$TOPOLOGY_JSON")"
    labels=("Last known sink${last_sink_label:+ ($last_sink_label)}")
    for x in "${sinks[@]}"; do
        labels+=("$(jq -r --arg n "$card" --arg s "$x" '.cards[]|select(.name==$n)|.sinks[]|select(.name==$s)|.label' <<<"$TOPOLOGY_JSON")")
    done
    if ((${#sinks[@]})); then
        while :; do
            choose_index 'Playback sink' "${labels[@]}" || exit 0
            if ((number>0)); then sink="${sinks[number-1]}"; break; fi
            if [[ -n "$last_sink" ]]; then sink="$last_sink"; break; fi
            echo 'No last known available sink; choose a numbered sink.'
        done
    fi
fi
request="$(jq -nc --arg card "$card" --arg profile "$profile" --arg port "$route" --arg sink "$sink" '{card:$card,profile:$profile,port:$port,sink:$sink}')"
# The running server serializes the entire pause -> output -> normalize -> resume.
flock -u 9
run_quiet_action python3 "$TOPOLOGY_SCRIPT" --switch-output "$request" || exit $?
result_line="Playback output applied: $card_label"
[[ -n "${last_sink_label:-}" && "$sink" == "${last_sink:-}" ]] && result_line+=" / $last_sink_label"
wrap_line "$result_line"
