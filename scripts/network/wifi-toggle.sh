#!/usr/bin/env bash
set -euo pipefail
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sway/network"
RESULT_FILE="$STATE_DIR/result.$$"
ACTION_LOG="$STATE_DIR/action.$$"
HOTSPOT='AirPlay Direct'
mkdir -p "$STATE_DIR"
rm -f "$RESULT_FILE" "$ACTION_LOG"
export STATE_DIR RESULT_FILE ACTION_LOG HOTSPOT

swaynag -t warning -y overlay -m 'Network' \
  -z 'Offline' 'for i in $(nmcli -t -f DEVICE device | sed "/^$/d"); do nmcli dev disconnect "$i" >>"$ACTION_LOG" 2>&1 || true; done; nmcli connection down "$HOTSPOT" >>"$ACTION_LOG" 2>&1 || true; touch "$RESULT_FILE"' \
  -z 'Ethernet' 'for row in $(nmcli -t -f DEVICE,TYPE device); do i=${row%%:*}; t=${row#*:}; [[ $t == ethernet ]] && nmcli dev connect "$i" >>"$ACTION_LOG" 2>&1 || [[ $t != ethernet ]] && nmcli dev disconnect "$i" >>"$ACTION_LOG" 2>&1 || true; done; nmcli connection down "$HOTSPOT" >>"$ACTION_LOG" 2>&1 || true; touch "$RESULT_FILE"' \
  -z 'USB Wi-Fi' 'for i in /sys/class/net/*; do n=${i##*/}; t=$(nmcli -t -f DEVICE,TYPE device | awk -F: -v d="$n" '\''$1==d{print $2}'\''); p=$(readlink -f "$i"); if [[ $t == wifi && $p == *"/usb"* ]]; then nmcli dev connect "$n" >>"$ACTION_LOG" 2>&1; else nmcli dev disconnect "$n" >>"$ACTION_LOG" 2>&1 || true; fi; done; nmcli connection down "$HOTSPOT" >>"$ACTION_LOG" 2>&1 || true; touch "$RESULT_FILE"' \
  -z 'Wi-Fi' 'for i in /sys/class/net/*; do n=${i##*/}; t=$(nmcli -t -f DEVICE,TYPE device | awk -F: -v d="$n" '\''$1==d{print $2}'\''); p=$(readlink -f "$i"); if [[ $t == wifi && $p != *"/usb"* ]]; then nmcli dev connect "$n" >>"$ACTION_LOG" 2>&1; else nmcli dev disconnect "$n" >>"$ACTION_LOG" 2>&1 || true; fi; done; nmcli connection down "$HOTSPOT" >>"$ACTION_LOG" 2>&1 || true; touch "$RESULT_FILE"' \
  -z 'AirPlay' 'nmcli -g connection.id connection show "$HOTSPOT" >/dev/null; if nmcli -t -f NAME connection show --active | grep -Fqx -- "$HOTSPOT"; then nmcli connection down "$HOTSPOT" >>"$ACTION_LOG" 2>&1; else nmcli connection up "$HOTSPOT" >>"$ACTION_LOG" 2>&1; fi; touch "$RESULT_FILE"' &
while [[ ! -e "$RESULT_FILE" ]]; do sleep .1; done
[[ ! -s "$ACTION_LOG" ]] || cat "$ACTION_LOG"
rm -f "$RESULT_FILE" "$ACTION_LOG"
printf '\n'; read -n1 -rs -p 'Press any key to close...'
