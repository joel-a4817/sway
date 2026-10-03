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
    [[ "$arg" == */audio-control.sh || "$arg" == audio-control.sh || "$arg" == */audio-switch.sh || "$arg" == audio-switch.sh ]] || return 1
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
    end_media_change
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
    end_media_change
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

end_media_change() { :; }
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
# Selection does not pause or mutate. The server owns pause, routing,
# normalization, and resume as one transaction after the final choice.
TOPOLOGY_JSON="$(output_topology 2>/dev/null)" || TOPOLOGY_JSON='{"cards":[]}'
mapfile -t CARDS < <(jq -r '.cards[] | [.name,.label] | @tsv' <<<"$TOPOLOGY_JSON")
if ((${#CARDS[@]})); then printf '%s\n' "${CARDS[@]}" > "$STATE_DIR/audio-cards-last.txt"
elif [[ -s "$STATE_DIR/audio-cards-last.txt" ]]; then mapfile -t CARDS < "$STATE_DIR/audio-cards-last.txt"; fi
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
choose_index() {
    local title="$1" answer i=0; shift
    ((${#@})) || { echo "No choices for $title" >&2; return 1; }
    echo; echo "$title"; echo
    for label in "$@"; do print_menu_item "$i" "$label"; i=$((i+1)); done
    while :; do
        read -rp 'Select: ' answer || return 1
        [[ "$answer" =~ ^[0-9]+$ && ${#answer} -le 9 ]] || { echo 'Enter a listed number.'; continue; }
        number=$((10#$answer))
        ((number < i)) && return 0
        echo 'Enter a listed number.'
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
    echo; echo 'Audio services'; echo
    print_menu_item 0 'Audio stop / start (toggle)'
    print_menu_item 1 'Restart CamillaDSP'
    print_menu_item 2 'Restart SonoBus'
    print_menu_item 3 'Restart AirPlay'
    print_menu_item 4 'Restart VNC'
    while :; do
        read -rp 'Select service action [0-4]: ' number || exit 0
        [[ "$number" =~ ^[0-4]$ ]] && break
        echo 'Enter 0, 1, 2, 3 or 4.'
    done
    flock -u 9
    if [[ "$number" == 0 ]]; then python3 "$TOPOLOGY_SCRIPT" --audio-toggle
    else python3 "$TOPOLOGY_SCRIPT" --restart-service "$number"; fi
    exit $? ;;
 select-filter)
    profiles="$(python3 "$TOPOLOGY_SCRIPT" --dsp-profiles)" || exit 1
    info="$(python3 "$TOPOLOGY_SCRIPT" --dsp-filter-info)" || exit 1
    mapfile -t filters < <(jq -r '.[] | select((ascii_downcase|contains("cmf"))|not)' <<<"$profiles")
    ((${#filters[@]})) || { echo 'No listening filters found.' >&2; exit 1; }
    labels=()
    for filter in "${filters[@]}"; do
        labels+=("$(jq -r --arg f "$filter" '.[$f].label // $f' <<<"$info")")
    done
    echo; echo 'CamillaDSP listening filter'; echo
    for i in "${!labels[@]}"; do print_menu_item "$((i+1))" "${labels[i]}"; done
    while :; do
        read -rp 'Select CamillaDSP filter: ' answer || exit 0
        [[ "$answer" =~ ^[0-9]+$ && ${#answer} -le 9 ]] || { echo 'Enter a listed number.'; continue; }
        ((10#$answer >= 1 && 10#$answer <= ${#filters[@]})) && break
        echo 'Enter a listed number.'
    done
    flock -u 9
    python3 "$TOPOLOGY_SCRIPT" --select-filter "${filters[$((10#$answer-1))]}"
    exit $? ;;
esac
[[ "$selected" =~ ^[0-9]+$ ]] && ((selected < ${#CARDS[@]})) || { echo 'Invalid device selection' >&2; exit 1; }
card="${CARDS[selected]%%$'\t'*}"
card_label="${CARDS[selected]#*$'\t'}"
# Refresh after the Swaynag selection, without changing the live graph.
TOPOLOGY_JSON="$(output_topology)" || { echo "Start audio services before choosing a playback output." >&2; exit 1; }
active="$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.activeProfile // empty' <<<"$TOPOLOGY_JSON")"
mapfile -t profiles < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.profiles[]|select(.available!="no" and .available!="false" and ((.name|ascii_downcase)!="off"))|.index' <<<"$TOPOLOGY_JSON")
labels=("Keep current profile")
for profile in "${profiles[@]}"; do
    labels+=("$(jq -r --arg n "$card" --arg p "$profile" '.cards[]|select(.name==$n)|.profiles[]|select((.index|tostring)==$p)|.label' <<<"$TOPOLOGY_JSON")")
done
choose_index "Card: $card_label | Playback profile" "${labels[@]}" || exit 0
if ((number==0)); then
    [[ -n "$active" ]] || { echo 'No current profile to keep.' >&2; exit 1; }
    profile="$active"
else profile="${profiles[number-1]}"; fi
mapfile -t routes < <(jq -r --arg n "$card" --arg p "$profile" '.cards[]|select(.name==$n)|.routes[]|select(.available!="no" and .available!="false" and ((.profiles|length)==0 or ([.profiles[]|tostring]|index($p))!=null))|.index' <<<"$TOPOLOGY_JSON")
route=''
if ((${#routes[@]})); then
    labels=('Keep current route')
    for r in "${routes[@]}"; do
        labels+=("$(jq -r --arg n "$card" --arg r "$r" '.cards[]|select(.name==$n)|.routes[]|select((.index|tostring)==$r)|.label' <<<"$TOPOLOGY_JSON")")
    done
    choose_index 'Output route' "${labels[@]}" || exit 0
    if ((number>0)); then route="${routes[number-1]}";
    elif [[ "$profile" != "$active" ]]; then
        echo 'Choose an output route for the new profile (0 cannot keep the old route).' >&2; exit 1
    fi
fi
# 0 means keep the actual current output, not cancel or auto-pick.
saved="$(jq -r '.saved.sink // empty' <<<"$TOPOLOGY_JSON")"
sink=''
if [[ "$profile" == "$active" ]]; then
    mapfile -t sinks < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.sinks[].name' <<<"$TOPOLOGY_JSON")
    labels=()
    if [[ -n "$saved" ]] && printf '%s\n' "${sinks[@]}" | grep -Fqx -- "$saved"; then
        current_label="$(jq -r --arg n "$card" --arg s "$saved" '.cards[]|select(.name==$n)|.sinks[]|select(.name==$s)|.label' <<<"$TOPOLOGY_JSON")"
        labels+=("Keep current output ($current_label)")
    else labels+=('Keep current output (unavailable; choose a sink)'); fi
    for x in "${sinks[@]}"; do
        labels+=("$(jq -r --arg n "$card" --arg s "$x" '.cards[]|select(.name==$n)|.sinks[]|select(.name==$s)|.label' <<<"$TOPOLOGY_JSON")")
    done
    if ((${#sinks[@]})); then
        while :; do
            choose_index 'Playback sink' "${labels[@]}" || exit 0
            if ((number>0)); then sink="${sinks[number-1]}"; break; fi
            if [[ -n "$saved" && "${labels[0]}" == Keep\ current\ output* ]]; then sink="$saved"; break; fi
            echo 'No current output to keep; choose a numbered sink.'
        done
    fi
fi
request="$(jq -nc --arg card "$card" --arg profile "$profile" --arg port "$route" --arg sink "$sink" '{card:$card,profile:$profile,port:$port,sink:$sink}')"
# The running server serializes the entire pause -> output -> normalize -> resume.
flock -u 9
python3 "$TOPOLOGY_SCRIPT" --switch-output "$request"
