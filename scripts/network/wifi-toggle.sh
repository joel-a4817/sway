#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/network-toggle-complete.$$"
rm -f "$RESULT_FILE"

echo "Current status:"
nmcli dev status
echo

export RESULT_FILE

swaynag \
    -t warning \
    -y overlay \
    -m "Network Selector" \
    -z "Built-in Wi‑Fi" \
'
builtin_wifi=""
usb_wifi=()
ethernet=()

for iface in /sys/class/net/*; do
    iface=$(basename "$iface")
    type=$(nmcli -t -f DEVICE,TYPE device | awk -F: -v d="$iface" '"'"'$1==d{print $2}'"'"')

    case "$type" in
        wifi)
            path=$(readlink -f "/sys/class/net/$iface")
            if [[ "$path" == *"/usb"* ]]; then
                usb_wifi+=("$iface")
            else
                builtin_wifi="$iface"
            fi
            ;;
        ethernet)
            ethernet+=("$iface")
            ;;
    esac
done

[[ -n "$builtin_wifi" ]] && nmcli dev connect "$builtin_wifi" >/dev/null 2>&1

for i in "${usb_wifi[@]}"; do
    nmcli dev disconnect "$i" >/dev/null 2>&1 || true
done

for i in "${ethernet[@]}"; do
    nmcli dev disconnect "$i" >/dev/null 2>&1 || true
done

touch "$RESULT_FILE"
' \
    -z "USB Wi‑Fi" \
'
builtin_wifi=""
usb_wifi=()
ethernet=()

for iface in /sys/class/net/*; do
    iface=$(basename "$iface")
    type=$(nmcli -t -f DEVICE,TYPE device | awk -F: -v d="$iface" '"'"'$1==d{print $2}'"'"')

    case "$type" in
        wifi)
            path=$(readlink -f "/sys/class/net/$iface")
            if [[ "$path" == *"/usb"* ]]; then
                usb_wifi+=("$iface")
            else
                builtin_wifi="$iface"
            fi
            ;;
        ethernet)
            ethernet+=("$iface")
            ;;
    esac
done

[[ -n "$builtin_wifi" ]] && nmcli dev disconnect "$builtin_wifi" >/dev/null 2>&1 || true

for i in "${usb_wifi[@]}"; do
    nmcli dev connect "$i" >/dev/null 2>&1
done

for i in "${ethernet[@]}"; do
    nmcli dev disconnect "$i" >/dev/null 2>&1 || true
done

touch "$RESULT_FILE"
' \
    -z "Ethernet" \
'
builtin_wifi=""
usb_wifi=()
ethernet=()

for iface in /sys/class/net/*; do
    iface=$(basename "$iface")
    type=$(nmcli -t -f DEVICE,TYPE device | awk -F: -v d="$iface" '"'"'$1==d{print $2}'"'"')

    case "$type" in
        wifi)
            path=$(readlink -f "/sys/class/net/$iface")
            if [[ "$path" == *"/usb"* ]]; then
                usb_wifi+=("$iface")
            else
                builtin_wifi="$iface"
            fi
            ;;
        ethernet)
            ethernet+=("$iface")
            ;;
    esac
done

[[ -n "$builtin_wifi" ]] && nmcli dev disconnect "$builtin_wifi" >/dev/null 2>&1 || true

for i in "${usb_wifi[@]}"; do
    nmcli dev disconnect "$i" >/dev/null 2>&1 || true
done

for i in "${ethernet[@]}"; do
    nmcli dev connect "$i" >/dev/null 2>&1
done

touch "$RESULT_FILE"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

rm -f "$RESULT_FILE"

echo
echo "Updated status:"
nmcli dev status

echo
read -n 1 -rsp "Press any key to close..."
echo
