#!/usr/bin/env bash

clear

mapfile -t profiles < <(
    nmcli -t -f NAME,TYPE connection show \
    | awk -F: '$2=="802-11-wireless"{print $1}'
)

echo "Saved Wi-Fi Profiles"
echo

echo "[0] Exit"

for i in "${!profiles[@]}"; do
    printf '[%d] %s\n' "$((i+1))" "${profiles[$i]}"
done

echo
#echo "Examples:"
#echo "  1"
#echo "  1 3 5"
#echo "  all"
#echo

read -rp "Delete: " selection

[[ "$selection" == "0" ]] && exit 0

if [[ "$selection" == "all" ]]; then

    for profile in "${profiles[@]}"; do
        echo "Deleting: $profile"
        nmcli connection delete "$profile"
    done

    exit 0
fi

for num in $selection; do

    index=$((num-1))

    if (( index >= 0 && index < ${#profiles[@]} )); then

        profile="${profiles[$index]}"

        echo
        echo "Deleting: $profile"

        nmcli connection delete "$profile"

    fi

done

echo
read -n 1 -s -r -p "Press any key to close..."
echo
