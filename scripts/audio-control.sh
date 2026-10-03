#!/usr/bin/env bash
set -euo pipefail
SERVER="$HOME/.config/sway/scripts/network/camilladsp-server-sonobus.py"
SELF="$(readlink -f -- "$0")"
[[ -f "$SERVER" ]] || { echo "Missing paired server: $SERVER" >&2; exit 1; }
choose() {
  local title="$1" answer i; shift
  (($#)) || { echo "No choices for $title" >&2; return 1; }
  printf '\n%s\n' "$title"
  i=0
  for label in "$@"; do i=$((i+1)); printf '%2d) %s\n' "$i" "$label"; done
  printf ' 0) Cancel\nChoice: '
  IFS= read -r answer || return 1
  [[ "$answer" =~ ^[0-9]+$ ]] && ((10#$answer >= 1 && 10#$answer <= $#)) || return 1
  number=$((10#$answer-1))
}
if [[ "${1:-}" != --pick ]]; then
  [[ -t 0 && -t 1 ]] || { echo 'Run audio-control.sh from an interactive terminal' >&2; exit 1; }
  command -v swaynag >/dev/null || { echo 'swaynag not found' >&2; exit 1; }
  state="$HOME/.local/state/sway/audio-switch"; mkdir -p "$state"
  selection="$(mktemp "$state/selection.XXXXXXXX")"
  # Like nixos-rebuild.sh: the nagbar runs in the background and its button
  # writes a marker. This shell remains attached to the original terminal.
  swaynag -t warning -y overlay -m 'Audio control' \
    -z Outputs "printf outputs > '$selection'" \
    -z Filters "printf filters > '$selection'" \
    -z Services "printf services > '$selection'" &
  nag_pid=$!
  trap 'kill "$nag_pid" 2>/dev/null || true; wait "$nag_pid" 2>/dev/null || true; rm -f -- "$selection"' EXIT
  while [[ ! -s "$selection" ]]; do
    if ! kill -0 "$nag_pid" 2>/dev/null; then
      wait "$nag_pid" || { echo 'swaynag exited before a selection' >&2; exit 1; }
      echo 'Audio control cancelled' >&2; exit 0
    fi
    sleep 0.1
  done
  choice="$(cat "$selection")"
  kill "$nag_pid" 2>/dev/null || true
  wait "$nag_pid" 2>/dev/null || true
  case "$choice" in
    outputs|filters|services) "$SELF" --pick "$choice" ;;
    *) echo "Invalid selection: $choice" >&2; exit 1 ;;
  esac
  exit 0
fi
command -v jq >/dev/null || { echo 'jq not found' >&2; exit 1; }
case "${2:-}" in
 outputs)
  topology="$(python3 "$SERVER" --output-topology)"
  mapfile -t cards < <(jq -r '.cards[].name' <<<"$topology")
  labels=(); for card in "${cards[@]}"; do labels+=("$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.label' <<<"$topology")"); done
  choose 'Playback device' "${labels[@]}" || exit 0; card="${cards[number]}"
  mapfile -t profiles < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.profiles[]|select(.available!="no")|.index' <<<"$topology")
  labels=(); for profile in "${profiles[@]}"; do labels+=("$(jq -r --arg n "$card" --argjson p "$profile" '.cards[]|select(.name==$n)|.profiles[]|select(.index==$p)|.label' <<<"$topology")"); done
  choose 'Device profile' "${labels[@]}" || exit 0; profile="${profiles[number]}"
  mapfile -t routes < <(jq -r --arg n "$card" --argjson p "$profile" '.cards[]|select(.name==$n)|.routes[]|select(.available!="no" and ((.profiles|length)==0 or (.profiles|index($p)!=null)))|.index' <<<"$topology")
  route=''
  if ((${#routes[@]})); then
    labels=('Keep current route')
    for r in "${routes[@]}"; do labels+=("$(jq -r --arg n "$card" --argjson r "$r" '.cards[]|select(.name==$n)|.routes[]|select(.index==$r)|.label' <<<"$topology")"); done
    choose 'Output route' "${labels[@]}" || exit 0
    if ((number>0)); then route="${routes[number-1]}"; fi
  fi
  sink=''
  active="$(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.activeProfile' <<<"$topology")"
  if [[ "$profile" == "$active" ]]; then
    mapfile -t sinks < <(jq -r --arg n "$card" '.cards[]|select(.name==$n)|.sinks[].name' <<<"$topology")
    if ((${#sinks[@]})); then
      labels=('Automatic (selected route)')
      for x in "${sinks[@]}"; do labels+=("$(jq -r --arg n "$card" --arg x "$x" '.cards[]|select(.name==$n)|.sinks[]|select(.name==$x)|.label' <<<"$topology")"); done
      choose 'Playback sink' "${labels[@]}" || exit 0
      if ((number>0)); then sink="${sinks[number-1]}"; fi
    fi
  fi
  request="$(jq -nc --arg card "$card" --arg profile "$profile" --arg port "$route" --arg sink "$sink" '{card:$card,profile:$profile,port:$port,sink:$sink}')"
  python3 "$SERVER" --switch-output "$request" ;;
 filters)
  info="$(python3 "$SERVER" --dsp-filter-info)"
  profiles="$(python3 "$SERVER" --dsp-profiles)"
  mapfile -t filters < <(jq -r '.[]' <<<"$profiles")
  labels=(); for filter in "${filters[@]}"; do labels+=("$(jq -r --arg f "$filter" '.[$f].label // $f' <<<"$info")"); done
  choose 'Listening filter' "${labels[@]}" || exit 0
  python3 "$SERVER" --select-filter "${filters[number]}" ;;
 services)
  choose 'Audio service action' 'Stop/start audio' 'Restart CamillaDSP' 'Restart SonoBus' 'Restart AirPlay' 'Restart VNC' || exit 0
  if ((number==0)); then python3 "$SERVER" --audio-toggle
  else python3 "$SERVER" --restart-service "$number"; fi ;;
 *) echo 'Invalid picker' >&2; exit 2 ;;
esac
