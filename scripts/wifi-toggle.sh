#!/usr/bin/env bash

set +e

STATE_FILE="/tmp/network-toggle.state"

builtin_wifi=""
usb_wifi=()
ethernet=()

for iface in /sys/class/net/*; do
    iface=$(basename "$iface")

    type=$(nmcli -t -f DEVICE,TYPE device | awk -F: -v d="$iface" '$1==d{print $2}')

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

if [[ ! -f "$STATE_FILE" ]] || [[ "$(cat "$STATE_FILE")" == "builtin" ]]; then

    echo "usb" > "$STATE_FILE"

    echo "Enabling built-in Wi‑Fi"
    [[ -n "$builtin_wifi" ]] && nmcli dev connect "$builtin_wifi"

    echo "Disabling USB Wi‑Fi"
    for i in "${usb_wifi[@]}"; do
        nmcli dev disconnect "$i"
    done

    echo "Disabling Ethernet"
    for i in "${ethernet[@]}"; do
        nmcli dev disconnect "$i"
    done

else

    echo "builtin" > "$STATE_FILE"

    echo "Disabling built-in Wi‑Fi"
    [[ -n "$builtin_wifi" ]] && nmcli dev disconnect "$builtin_wifi"

    echo "Enabling USB Wi‑Fi"
    for i in "${usb_wifi[@]}"; do
        nmcli dev connect "$i"
    done

    echo "Enabling Ethernet"
    for i in "${ethernet[@]}"; do
        nmcli dev connect "$i"
    done

fi
