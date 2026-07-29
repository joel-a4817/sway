#!/usr/bin/env bash

mapfile -t networks < <(
    nmcli -t -f SSID device wifi list --rescan yes \
    | awk -F: 'length($1) > 0 && !seen[$1]++'
)

echo "Available Wi-Fi Networks"
echo

echo "[0] Keep current connection"

count=${#networks[@]}
rows=$(((count + 1) / 2))

for ((i=0; i<rows; i++)); do
    left_idx=$i
    right_idx=$((i + rows))

    left="[$((left_idx + 1))] ${networks[$left_idx]}"

    if (( right_idx < count )); then
        right="[$((right_idx + 1))] ${networks[$right_idx]}"
        printf '%-30s %s\n' "$left" "$right"
    else
        printf '%s\n' "$left"
    fi
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
