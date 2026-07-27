#!/usr/bin/env bash

clear

mapfile -t networks < <(
    nmcli -t -f SSID device wifi list --rescan yes \
    | awk -F: 'length($1) > 0 && !seen[$1]++'
)

echo "Available Wi-Fi Networks"
echo

echo "[0] Keep current connection"

for i in "${!networks[@]}"; do
    printf '[%d] %s\n' "$((i+1))" "${networks[$i]}"
done

echo
read -rp "Select network: " choice

if [[ "$choice" == "0" ]]; then
    echo "Keeping current network."
    exit 0
fi

index=$((choice-1))

if (( index < 0 || index >= ${#networks[@]} )); then
    echo "Invalid selection."
    exit 1
fi

ssid="${networks[$index]}"

echo
echo "Connecting to: $ssid"
echo

nmcli device wifi connect "$ssid" --ask

echo
read -n 1 -s -r -p "Press any key to close..."
echo
