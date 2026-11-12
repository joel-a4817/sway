AUDIO_STOPPED_FILE="$HOME_DIR/.local/state/sway/audio-runtime/audio-stopped"
if [[ -e "$AUDIO_STOPPED_FILE" ]]; then
    AUDIO_TOGGLE_LABEL='Start audio'
else
    AUDIO_TOGGLE_LABEL='Stop audio'
fi
DELAY_JSON="$(python3 "$TOPOLOGY_SCRIPT" --both-delay-status 2>>"$ACTION_LOG" || true)"
BOTH_DELAY_MS="$(jq -r '.milliseconds // 0' <<<"$DELAY_JSON" 2>/dev/null)"
ARGS=(-t warning -y overlay -m 'Media control')
ARGS+=( -z "$AUDIO_TOGGLE_LABEL" "printf '%s\n' audio-toggle > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
CURRENT_MODE="$(python3 "$TOPOLOGY_SCRIPT" --mode-state 2>>"$ACTION_LOG" | jq -r '.mode // empty')"
if [[ "$CURRENT_MODE" == ipad_both || "$CURRENT_MODE" == laptop_both ]]; then
  ARGS+=( -z "Both-output sync delay (${BOTH_DELAY_MS} ms)" "printf '%s\n' both-delay > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
else
  RUNTIME_JSON="$(python3 "$TOPOLOGY_SCRIPT" --profile-runtime-status 2>>"$ACTION_LOG" || true)"
  runtime_available="$(jq -r '.available // false' <<<"$RUNTIME_JSON" 2>/dev/null)"
  if [[ "$runtime_available" == true ]]; then
    runtime_chunk="$(jq -r '.chunksize' <<<"$RUNTIME_JSON")"
    runtime_limiter="$(jq -r 'if .peakLimiter then "on" else "off" end' <<<"$RUNTIME_JSON")"
    ARGS+=( -z "Runtime ${runtime_chunk},${runtime_limiter}" "printf '%s\n' profile-runtime > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
  else
    ARGS+=( -z 'Runtime N/A' "printf '%s\n' profile-runtime > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
  fi
fi
SAFE_JSON="$(python3 "$TOPOLOGY_SCRIPT" --safe-volume-status 2>>"$ACTION_LOG" || true)"
if [[ "$(jq -r '.available // false' <<<"$SAFE_JSON" 2>/dev/null)" == true ]]; then
  SAFE_PERCENT="$(jq -r '(.percent | tonumber * 10 | round / 10)' <<<"$SAFE_JSON")"
  SAFE_VOLUME_DB="$(jq -r '(.percent | tonumber) as $p | if $p > 0 then (60 * (($p / 100) | log) / (10 | log)) else null end | if . == null then "N/A" else (. * 10 | round / 10) end' <<<"$SAFE_JSON")"
  ARGS+=( -z "Safe max ${SAFE_PERCENT}%, volume ${SAFE_VOLUME_DB} dB" "printf '%s\n' safe-volume > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
else
  ARGS+=( -z 'Safe max N/A' "printf '%s\n' safe-volume-na > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
fi
ARGS+=( -z 'Filters' "printf '%s\n' select-filter > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
ARGS+=( -z 'Custom filter' "printf '%s\n' create-custom-filter > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
ARGS+=( -z 'Status' "printf '%s\n' all-status > '$CARD_SELECTION_FILE'; touch '$RESULT_FILE'" )
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
normalize_menu_step || exit 1
case "$selected" in
 safe-volume) flock -u 9; python3 "$TOPOLOGY_SCRIPT" --safe-volume-apply || exit $?; wrap_line "Safe max applied: ${SAFE_PERCENT}%"; exit 0 ;;
 safe-volume-na) wrap_line 'Safe max unavailable.'; exit 0 ;;
 all-status)
    flock -u 9
    clear 2>/dev/null || true
    python3 "$TOPOLOGY_SCRIPT" --media-control-status || { wrap_line 'Unable to read Media Control status.'; exit 0; }
    exit 0
    ;;
 create-custom-filter)
    current_mode="$(python3 "$TOPOLOGY_SCRIPT" --mode-state 2>/dev/null | jq -r '.mode // empty')"
    [[ "$current_mode" != ipad_both && "$current_mode" != laptop_both ]] || { wrap_line 'Custom filters are locked while a both-output mode is active.'; exit 0; }
    flock -u 9; python3 "$TOPOLOGY_SCRIPT" --create-custom-filter; exit $? ;;
 profile-runtime)
    runtime_json="$(python3 "$TOPOLOGY_SCRIPT" --profile-runtime-status 2>>"$ACTION_LOG" || true)"
    if [[ "$(jq -r '.available // false' <<<"$runtime_json" 2>/dev/null)" != true ]]; then
      reason="$(jq -r '.reason // "unavailable"' <<<"$runtime_json" 2>/dev/null)"
      case "$reason" in
        no-filter) wrap_line 'CamillaDSP runtime is unavailable while No filter is selected because CamillaDSP is bypassed.' ;;
        both-output) wrap_line 'CamillaDSP runtime is unavailable in both-output mode; use Both-output sync delay instead.' ;;
        *) wrap_line 'Select a CamillaDSP listening filter before changing runtime settings.' ;;
      esac
      exit 0
    fi
    echo; wrap_line 'CamillaDSP runtime'; echo; printf '[E] Exit\n'
    wrap_line "Current chunksize: $(jq -r '.chunksize' <<<"$runtime_json") frames"
    while :; do
      read -r -p 'Chunksize (any positive whole number; E=Exit): ' runtime_chunk || exit 0
      [[ "$runtime_chunk" =~ ^[Ee]$ ]] && exit 0
      [[ "$runtime_chunk" =~ ^[1-9][0-9]*$ ]] && break
      wrap_line 'Enter any positive whole-number chunksize, or E to exit.'
    done
    echo; wrap_line 'Peak limiter'; echo
    printf '[E] Exit\n[B] Back\n[1] On\n[2] Off\n'
    while :; do
      read -r -p 'Select: ' limiter_choice || exit 0
      case "$limiter_choice" in
        [Ee]) exit 0 ;;
        [Bb]) exec "$0" profile-runtime ;;
        1) limiter_state=on;break ;;
        2) limiter_state=off;break ;;
        *) wrap_line 'Enter E, B, 1, or 2.' ;;
      esac
    done
    normalize_menu_step || exit 0; flock -u 9
    runtime_out="$(mktemp "$STATE_DIR/.runtime.stdout.XXXXXX")"
    runtime_err="$(mktemp "$STATE_DIR/.runtime.stderr.XXXXXX")"
    if python3 "$TOPOLOGY_SCRIPT" --profile-runtime-set "$runtime_chunk" "$limiter_state" >"$runtime_out" 2>"$runtime_err"; then
      runtime_result="$(tail -n 1 "$runtime_out")"
      cat "$runtime_out" "$runtime_err" >>"$ACTION_LOG"
      rm -f "$runtime_out" "$runtime_err"
      if ! jq -e '.chunksize and (.peakLimiter|type=="boolean")' >/dev/null 2>&1 <<<"$runtime_result"; then
        wrap_line 'Runtime settings were applied, but the backend returned an invalid result. Check the action log.'
        exit 0
      fi
      wrap_line "CamillaDSP runtime applied: $(jq -r '.chunksize' <<<"$runtime_result") frames; peak limiter $(jq -r 'if .peakLimiter then "on" else "off" end' <<<"$runtime_result")"
    else
      status=$?;cat "$runtime_out" "$runtime_err" >>"$ACTION_LOG"
      detail="$(tail -n 1 "$runtime_err")";rm -f "$runtime_out" "$runtime_err"
      wrap_line "CamillaDSP rejected those runtime settings${detail:+: $detail}"
      exit 0
    fi
    exit 0 ;;
 both-delay)
    current_mode="$(python3 "$TOPOLOGY_SCRIPT" --mode-state 2>/dev/null | jq -r '.mode // empty')"
    [[ "$current_mode" == ipad_both || "$current_mode" == laptop_both ]] || { wrap_line 'Both-output sync offset is available only while a both-output mode is active.'; exit 1; }
    echo
    wrap_line 'Both-output sync offset'
    echo
    printf '[E] Exit\n'
    wrap_line "Current sync offset: ${BOTH_DELAY_MS} ms"
    normalize_menu_step || exit 1
    while :; do
        read -r -p 'Sync offset in ms (-500 to 500, E=Exit): ' delay_value || exit 0
        [[ "$delay_value" =~ ^[Ee]$ ]] && exit 0
        if [[ "$delay_value" =~ ^-?([0-9]{1,3})(\.[0-9]{1,3})?$ ]] &&
           awk -v value="$delay_value" 'BEGIN { exit !(value >= -500 && value <= 500) }'; then
            normalize_menu_step || exit 1
            delay_stdout="$(mktemp "$STATE_DIR/.both-delay.stdout.XXXXXX")"
            delay_stderr="$(mktemp "$STATE_DIR/.both-delay.stderr.XXXXXX")"
            flock -u 9
            if python3 "$TOPOLOGY_SCRIPT" --both-delay-set "$delay_value" >"$delay_stdout" 2>"$delay_stderr"; then
                status=0
            else
                status=$?
            fi
            cat "$delay_stdout" "$delay_stderr" >>"$ACTION_LOG"
            if ((status != 0)); then
                cat "$delay_stdout" "$delay_stderr" >&2
                rm -f "$delay_stdout" "$delay_stderr"
                exit "$status"
            fi
            delay_result="$(grep -E '^\{.*\}$' "$delay_stdout" | while IFS= read -r line; do jq -ce 'select(type=="object" and (.milliseconds|type)=="number")' <<<"$line" 2>/dev/null || true; done | tail -n 1)"
            rm -f "$delay_stdout" "$delay_stderr"
            if [[ -z "$delay_result" ]]; then
                wrap_line 'The sync offset action returned no valid result. Check the Media Control action log.' >&2
                exit 1
            fi
            applied_delay="$(jq -r '.milliseconds' <<<"$delay_result")"
            wrap_line "Both-output sync offset applied: ${applied_delay} ms"
            exit 0
        fi
        wrap_line 'Enter E for Exit, or an offset from -500 through 500 ms. Positive delays PC; negative delays external/iPad.'
    done
    ;;
 select-output)
    while :; do
        choose_index --exit 'Audio output' \
          'Play external audio on external output' \
          'Play external audio on PC output' \
          'Play external audio on both outputs' \
          'Play PC audio on external output' \
          'Play PC audio on PC output' \
          'Play PC audio on both outputs'
        status=$?
        ((status==3)) && exit 0
        modes=(ipad_ipad ipad_laptop ipad_both laptop_ipad laptop_laptop laptop_both)
        selected_mode="${modes[number]}"
        matrix_left=''; matrix_right=''
        case "$selected_mode" in
          ipad_ipad|laptop_ipad)
            flock -u 9
            run_quiet_action python3 "$TOPOLOGY_SCRIPT" --set-mode "$selected_mode" || exit $?
            exit 0 ;;
          ipad_both|laptop_both)
            # Both destinations receive the same full stereo programme. Splitting
            # L/R across network and local branches produced incoherent playback.
            matrix_left=''; matrix_right=''
            break ;;
          *) break ;;
        esac
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
 vnc-toggle)
    flock -u 9
    toggle_result="$(python3 "$TOPOLOGY_SCRIPT" --vnc-toggle 2>&1)" || {
        status=$?
        printf '%s\n' "$toggle_result" >&2
        exit "$status"
    }
    printf '%s\n' "$toggle_result" >>"$ACTION_LOG"
    if [[ "$(jq -r 'if has("running") then (.running|tostring) else empty end' <<<"$toggle_result" 2>/dev/null)" == 'true' ]]; then
        wrap_line 'VNC started.'
    elif [[ "$(jq -r 'if has("running") then (.running|tostring) else empty end' <<<"$toggle_result" 2>/dev/null)" == 'false' ]]; then
        wrap_line 'VNC stopped.'
    else
        wrap_line 'VNC changed state, but the resulting state was not reported.' >&2
        exit 1
    fi
    exit 0 ;;
 select-filter)
    current_mode="$(python3 "$TOPOLOGY_SCRIPT" --mode-state 2>/dev/null | jq -r '.mode // empty')"
    [[ "$current_mode" != ipad_both && "$current_mode" != laptop_both ]] || { wrap_line 'Listening filters are locked while a both-output mode is active. Both-output uses 00-filterless.yml.'; exit 0; }
    filter_path=''
    while :; do
        directory="$(python3 "$TOPOLOGY_SCRIPT" --dsp-directory "$filter_path")" || exit 1
        mapfile -t entry_rows < <(jq -r '.entries[] | select(.id != "00-filterless.yml") | [.type,.id,.label] | @tsv' <<<"$directory")
        labels=(); ids=(); types=()
        if [[ -z "$filter_path" ]]; then
            : # No filter is reserved as option 0 at the root
        fi
        for row in "${entry_rows[@]}"; do
            type="${row%%$'\t'*}"; rest="${row#*$'\t'}"; id="${rest%%$'\t'*}"; label="${rest#*$'\t'}"
            types+=("$type"); ids+=("$id")
            labels+=("$label")
        done
        echo; wrap_line "Listening profiles${filter_path:+ / $filter_path}"; echo
        if [[ -z "$filter_path" ]]; then printf '[0] Exit\n'; else printf '[0] Back\n'; fi
        [[ -z "$filter_path" ]] && print_menu_item 1 'No filter (bypass CamillaDSP)'
        if [[ -z "$filter_path" ]]; then filter_menu_offset=2; else filter_menu_offset=1; fi
        for i in "${!labels[@]}"; do print_menu_item "$((i + filter_menu_offset))" "${labels[i]}"; done
        [[ -z "$filter_path" ]] && print_menu_item "$((${#labels[@]}+2))" 'Custom filter'
        ((${#labels[@]})) || { [[ -n "$filter_path" ]] || { wrap_line 'No filter folders or YAML files found.' >&2; exit 1; }; }
        while :; do
            read -r -p 'Select: ' answer || exit 0
            [[ "$answer" =~ ^[0-9]+$ ]] || { wrap_line 'Enter a listed number.'; continue; }
            number=$((10#$answer))
            if ((number==0)) && [[ -z "$filter_path" ]]; then exit 0; fi
            if ((number==1)) && [[ -z "$filter_path" ]]; then normalize_menu_step || exit 1; selected_filter='__no_filter__'; selected_label='No filter (bypass CamillaDSP)'; break 2; fi
            if ((number==0)) && [[ -n "$filter_path" ]]; then
                filter_path="$(jq -r '.parent // empty' <<<"$directory")"; continue 2
            fi
            if [[ -z "$filter_path" ]] && ((number==${#labels[@]}+2)); then normalize_menu_step || exit 1; flock -u 9; python3 "$TOPOLOGY_SCRIPT" --create-custom-filter; exit $?; fi
            minimum=$filter_menu_offset
            maximum=$((${#labels[@]} + filter_menu_offset - 1))
            ((number>=minimum && number<=maximum)) || { wrap_line 'Enter a listed number.'; continue; }
            index=$((number-filter_menu_offset)); break
        done
        normalize_menu_step || exit 1
        if [[ "${types[index]}" == directory ]]; then filter_path="${ids[index]}"; continue; fi
        selected_filter="${ids[index]}"; selected_label="${labels[index]}"; break
    done
    flock -u 9
    run_quiet_action python3 "$TOPOLOGY_SCRIPT" --select-filter "$selected_filter" || exit $?
    wrap_line "Listening filter applied: $selected_label"
    exit 0 ;;
esac
# Open the transaction before the first mutation so cancel and abandoned-preview
# recovery can restore the original mode, routes and streams.
if ! preview_error="$(MEDIA_CONTROL_PICKER_PID=$$ MEDIA_CONTROL_PICKER_START="$(process_start "$$")" python3 "$TOPOLOGY_SCRIPT" --begin-output-preview 2>&1 >/dev/null)"; then
    printf '%s\n' "$preview_error" >&2; exit 1
fi
OUTPUT_PREVIEW_ACTIVE=1
# Return success and set `number` only when exactly one entry is valid.
# This is intentionally limited to staged topology pickers whose labels carry
# an explicit [N/A] validity marker; ordinary menus must still be shown.
select_only_valid_suboption() {
    local summary_label="${1:-Selection}" index valid_count=0 valid_index=-1
    for index in "${!labels[@]}"; do
        [[ "${labels[index]}" == '[N/A] '* ]] && continue
        valid_index=$index
        ((valid_count+=1))
    done
    ((valid_count==1)) || return 1
    number=$valid_index
    AUTO_SELECTED_SUMMARY+=("$summary_label: ${labels[valid_index]}")
}

cancel_output_picker() {
    ((OUTPUT_PREVIEW_ACTIVE)) || return 0
    MEDIA_CONTROL_PICKER_PID=$$ python3 "$TOPOLOGY_SCRIPT" --cancel-output-preview >>"$ACTION_LOG" 2>&1 || true
    OUTPUT_PREVIEW_ACTIVE=0
}

# The output picker intentionally does not mutate the live graph between menu
# stages.  It commits only the final complete request, so Back is lossless.
while :; do
    TOPOLOGY_JSON="$(output_topology)" || exit 1
    mapfile -t card_rows < <(jq -r '.cards[] | @base64' <<<"$TOPOLOGY_JSON")
    cards=(); labels=()
    for row in "${card_rows[@]}"; do
        item="$(base64 -d <<<"$row")"
        value="$(jq -r '.name' <<<"$item")"
        label="$(jq -r '.label' <<<"$item")"
        internal="$(jq -r '.internal // false' <<<"$item")"
        cards+=("$value")
        [[ "$internal" == true && "$label" != '[INT] '* && "$label" != '[LOOP] '* && "$label" != '[VIRT] '* ]] && label="[INT] $label"
        labels+=("$label")
    done
    choose_index --exit 'Audio outputs | Device' "${labels[@]}"
    choice_status=$?
    if ((choice_status==3)); then cancel_output_picker; exit 0; fi
    card="${cards[number]}";card_label="${labels[number]}"
    [[ "$card_label" != '[INT] '* && "$card_label" != '[LOOP] '* && "$card_label" != '[VIRT] '* ]] || { wrap_line 'That device is informational and cannot be selected.'; continue; }

    while :; do
        mapfile -t profile_rows < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.profiles[]|@base64' <<<"$TOPOLOGY_JSON")
        profiles=();labels=()
        for row in "${profile_rows[@]}"; do
            item="$(base64 -d <<<"$row")"
            profiles+=("$(jq -r '.index' <<<"$item")")
            label="$(jq -r '.label // .description // .name // "Unnamed profile"' <<<"$item")"
            available="$(jq -r '.available // "unknown"' <<<"$item")"
            [[ "$available" == no || "$available" == false ]] && label="[N/A] $label"
            labels+=("$label")
        done
        if select_only_valid_suboption "Profile"; then
            choice_status=0
        else
            choose_index --back "Card: $card_label | Profile" "${labels[@]}"
            choice_status=$?
        fi
        ((choice_status==3)) && { cancel_output_picker; exit 0; }
        ((choice_status==2)) && break
        profile="${profiles[number]}"
        [[ "${labels[number]}" != '[N/A] '* ]] || { wrap_line 'That profile is unavailable.'; continue; }

        while :; do
            mapfile -t route_rows < <(jq -r --arg n "$card" --argjson p "$profile" '
              .cards[]|select(.name==$n)|.routes[]|
              select((.profiles // []) | map(tonumber) | index($p|tonumber))|
              @base64' <<<"$TOPOLOGY_JSON")
            routes=();labels=()
            for row in "${route_rows[@]}"; do
                item="$(base64 -d <<<"$row")"
                value="$(jq -r '.index' <<<"$item")"
                label="$(jq -r '.label // .description // .name // "Unnamed route"' <<<"$item")"
                available="$(jq -r '.available // "unknown"' <<<"$item")"
                routes+=("$value")
                [[ "$available" == no || "$available" == false ]] && label="[N/A] $label"
                labels+=("$label")
            done
            ((${#labels[@]})) || { wrap_line 'No routes are compatible with that profile.'; break; }
            if select_only_valid_suboption "Output route"; then
                choice_status=0
            else
                choose_index --back 'Output route' "${labels[@]}"
                choice_status=$?
            fi
            ((choice_status==3)) && { cancel_output_picker; exit 0; }
            ((choice_status==2)) && break
            route="${routes[number]}"
            [[ "${labels[number]}" != '[N/A] '* ]] || { wrap_line 'That route is unavailable.'; continue; }

            while :; do
                mapfile -t sink_rows < <(jq -r --arg n "$card" --argjson route "$route" '
                  .cards[]|select(.name==$n) as $card|
                  ($card.routes[]|select((.index|tonumber)==$route)) as $selectedRoute|
                  $card.sinks[]|
                  select((.profileDevice // -1) as $device | ($selectedRoute.devices // []) | map(tonumber) | index($device|tonumber))|
                  @base64' <<<"$TOPOLOGY_JSON")
                sinks=();labels=()
                for row in "${sink_rows[@]}"; do
                    item="$(base64 -d <<<"$row")"
                    sinks+=("$(jq -r '.name' <<<"$item")")
                    labels+=("$(jq -r '.label // .name // "Unnamed sink"' <<<"$item")")
                done
                if ((${#labels[@]}==0)); then
                    wrap_line 'No playback sinks are compatible with that route.'
                    break
                fi
                if select_only_valid_suboption "Playback sink"; then
                    choice_status=0
                else
                    choose_index --back 'Playback sink' "${labels[@]}"
                    choice_status=$?
                fi
                ((choice_status==3)) && { cancel_output_picker; exit 0; }
                ((choice_status==2)) && break
                sink="${sinks[number]}"
                request="$(jq -nc --arg card "$card" --arg profile "$profile" --arg port "$route" --arg sink "$sink" \
                  --arg matrixLeft "${matrix_left:-}" --arg matrixRight "${matrix_right:-}" \
                  '{card:$card,profile:$profile,port:$port,sink:$sink} + (if ($matrixLeft!="" and $matrixRight!="") then {bothMatrix:{left:$matrixLeft,right:$matrixRight}} else {} end)')"
                flock -u 9
                if [[ -n "${selected_mode:-}" ]]; then
                    run_quiet_action python3 "$TOPOLOGY_SCRIPT" --set-mode "$selected_mode" "$request" || exit $?
                else
                    run_quiet_action python3 "$TOPOLOGY_SCRIPT" --switch-output "$request" || exit $?
                fi
                OUTPUT_PREVIEW_ACTIVE=0
                wrap_line "Playback output applied: $card_label"
                exit 0
            done
            # Back from Playback sink returns to Output route.
            continue
        done
        # Back from Output route returns to Profile.
        continue
    done
    # Back from Profile returns to Device.
done
