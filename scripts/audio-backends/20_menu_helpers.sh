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
            case "$(compact_item_label "$choice")" in
                '[N/A] '*|'[INT] '*|'[MON] '*|'[LOOP] '*|'[VIRT] '*)
                    wrap_line 'That item cannot be selected.'; continue ;;
            esac
            return 0
        fi
        wrap_line 'Enter a listed number.'
    done
}
