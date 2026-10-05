AUDIO_STOPPED_FILE="$HOME_DIR/.local/state/sway/camilladsp-webremote/audio-stopped"
if [[ -e "$AUDIO_STOPPED_FILE" ]]; then
    AUDIO_TOGGLE_LABEL='Start audio'
else
    AUDIO_TOGGLE_LABEL='Stop audio'
fi

ARGS=(-t warning -y overlay -m 'Media control')
ARGS+=( -z "$AUDIO_TOGGLE_LABEL" "printf '%s\n' audio-toggle > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
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
    # Topology discovery is read-only. Do not create a preview or change modes
    # until the user chooses an actual playback device.
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
        break
    done
    ;;
 select-input)
    python3 "$TOPOLOGY_SCRIPT" --interactive-input || exit $?
    exit 0 ;;
 select-camera)
    camera_picker || exit $?
    exit 0 ;;
 audio-toggle)
    flock -u 9
    toggle_result="$(python3 "$TOPOLOGY_SCRIPT" --audio-toggle 2>&1)" || {
        status=$?
        printf '%s\n' "$toggle_result" >&2
        exit "$status"
    }
    printf '%s\n' "$toggle_result" >>"$ACTION_LOG"
    if [[ "$(jq -r 'if has("stopped") then (.stopped|tostring) else empty end' <<<"$toggle_result" 2>/dev/null)" == 'true' ]]; then
        wrap_line 'Audio stopped.'
    elif [[ "$(jq -r 'if has("stopped") then (.stopped|tostring) else empty end' <<<"$toggle_result" 2>/dev/null)" == 'false' ]]; then
        wrap_line 'Audio started.'
    else
        wrap_line 'Audio changed state, but the resulting state was not reported.' >&2
        exit 1
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
# The first concrete output selection owns the transition to the local
# Laptop -> Laptop boundary. Force that complete mode transition before the
# output transaction is opened, so preview validation cannot reject an
# External-only starting mode.
if ! boundary_error="$(python3 "$TOPOLOGY_SCRIPT" --laptop-laptop-boundary 2>&1)"; then
    printf '%s\n' "$boundary_error" >>"$ACTION_LOG"
    wrap_line 'Could not force Laptop -> Laptop before applying the output.' >&2
    exit 1
fi
if ! preview_error="$(MEDIA_CONTROL_PICKER_PID=$$ MEDIA_CONTROL_PICKER_START="$(process_start "$$")" python3 "$TOPOLOGY_SCRIPT" --begin-output-preview 2>&1 >/dev/null)"; then
    printf '%s\n' "$preview_error" >>"$ACTION_LOG"
    wrap_line 'Could not begin output selection after switching to Laptop -> Laptop.' >&2
    exit 1
fi
export MEDIA_CONTROL_PICKER_PID=$$
OUTPUT_PREVIEW_ACTIVE=1
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
      (if ((($devices|length)>0 and $sink.profileDevice != null and
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
    [[ "${labels[idx]}" != '[UNAVAILABLE] '* ]] && { sink="$candidate"; break; }
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
