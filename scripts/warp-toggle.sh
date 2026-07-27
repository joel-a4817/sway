#!/usr/bin/env bash

clear

echo "Cloudflare WARP Mode"
echo

modes=(
    $(warp-cli mode --help \
        | awk '/Possible values:/,/Options:/' \
        | grep '^ *-' \
        | sed 's/^ *- //' \
        | awk -F: '{print $1}')
)

echo "[0] Off"

for i in "${!modes[@]}"; do
    printf '[%d] %s\n' "$((i+1))" "${modes[$i]}"
done

echo
read -rp "Select mode: " choice

if [[ "$choice" == "0" ]]; then
    warp-cli disconnect
    echo
    echo "WARP disabled"
    read -n 1 -s -r -p "Press any key to close..."
    exit 0
fi

index=$((choice-1))

if (( index < 0 || index >= ${#modes[@]} )); then
    echo
    echo "Invalid option"
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

selected="${modes[$index]}"

echo
echo "Switching to: $selected"

warp-cli disconnect 2>/dev/null
warp-cli mode "$selected"
warp-cli connect

echo
echo "Enabled: $selected"
read -n 1 -s -r -p "Press any key to close..."
