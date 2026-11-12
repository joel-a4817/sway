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
compact_item_label() {
    local label="$1"
    label="${label% (current)}"
    label="${label/Monitor of /}"
    printf '%s' "$label"
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
    local allow_back=0 title answer choice key last_label label compact
    local i=1 scan_index=0 valid_count=0 valid_index=-1 last_index=-1 last_choice=''
    if [[ "${1:-}" == --exit ]]; then shift
    elif [[ "${1:-}" == --back ]]; then allow_back=1; shift
    fi
    title="$1"; shift
    ((${#@})) || { echo "No choices for $title" >&2; return 1; }
    normalize_menu_step || return 1
    key="$(printf '%s' "$title" | sha256sum | cut -d' ' -f1)"
    mkdir -p "$STATE_DIR/menu-last"
    last_label="$(cat "$STATE_DIR/menu-last/$key" 2>/dev/null || true)"

    for choice in "$@"; do
        compact="$(compact_item_label "$choice")"
        case "$compact" in
          '[N/A] '*|'[INT] '*|'[MON] '*|'[LOOP] '*|'[VIRT] '*) ;;
          *)
            valid_index=$scan_index
            ((valid_count+=1))
            if [[ -n "$last_label" && "$compact" == "$last_label" ]]; then
                last_index=$scan_index; last_choice="$choice"
            fi
            ;;
        esac
        ((scan_index+=1))
    done

    if ((valid_count==1)); then
        number=$valid_index
        choice="${@:$((number+1)):1}"
        compact="$(compact_item_label "$choice")"
        printf '%s\n' "$compact" >"$STATE_DIR/menu-last/$key"
        AUTO_SELECTED_SUMMARY+=("$(compact_menu_label "$title"): $compact")
        normalize_menu_step || return 1
        return 0
    fi

    echo; wrap_line "$(compact_menu_label "$title")"; echo
    print_menu_item E 'Exit'
    ((allow_back)) && print_menu_item B 'Back'
    ((last_index>=0)) && print_menu_item 0 "Use last valid: $last_choice"
    i=1
    for label in "$@"; do print_menu_item "$i" "$label"; ((i+=1)); done
    while :; do
        read -r -p 'Select: ' answer || return 3
        case "$answer" in
          [Ee]) return 3 ;;
          [Bb]) ((allow_back)) && return 2; wrap_line 'Back is unavailable at this first step.'; continue ;;
          0)
            ((last_index>=0)) || { wrap_line 'No previous valid selection is saved for this menu.'; continue; }
            number=$last_index ;;
          *)
            [[ "$answer" =~ ^[0-9]+$ && ${#answer} -le 9 ]] || { wrap_line 'Enter E, B where shown, 0 where shown, or a listed number.'; continue; }
            number=$((10#$answer-1))
            ((number>=0 && number<${#@})) || { wrap_line 'Enter a listed option.'; continue; } ;;
        esac
        choice="${@:$((number+1)):1}"
        case "$(compact_item_label "$choice")" in
          '[N/A] '*|'[INT] '*|'[MON] '*|'[LOOP] '*|'[VIRT] '*) wrap_line 'That item cannot be selected.'; continue ;;
        esac
        printf '%s\n' "$(compact_item_label "$choice")" >"$STATE_DIR/menu-last/$key"
        normalize_menu_step || return 1
        return 0
    done
}
