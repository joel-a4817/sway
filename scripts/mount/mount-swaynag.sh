#!/usr/bin/env bash

set -euo pipefail

RESULT_FILE="/tmp/mount-toggle-complete.$$"
ACTION_LOG="/tmp/mount-toggle-action.$$"
MOUNT_SCRIPT="/home/joel/.config/sway/scripts/mount/mount-script.sh"

rm -f "$RESULT_FILE" "$ACTION_LOG"
touch "$ACTION_LOG"

export RESULT_FILE ACTION_LOG MOUNT_SCRIPT

OUT="$(swaymsg -t get_outputs -r | jq -r '.[] | select(.focused) | .name')"

get_device_label() {
    local device_name="$1"
    local device="/dev/$device_name"

    if [[ ! -b "$device" ]]; then
        printf '%s not found' "$device_name"
    elif findmnt -rn -S "$device" >/dev/null 2>&1; then
        printf 'Unmount %s' "$device_name"
    else
        printf 'Mount %s' "$device_name"
    fi
}

SDA_LABEL="$(get_device_label sda)"
SDB_LABEL="$(get_device_label sdb)"
SR0_LABEL="$(get_device_label sr0)"

swaynag \
    -t warning \
    -y overlay \
    -o "$OUT" \
    -m "Mount Options" \
    -z "$SDA_LABEL" \
'
if "$MOUNT_SCRIPT" sda >>"$ACTION_LOG" 2>&1; then
    echo "0" >"$RESULT_FILE"
else
    status=$?
    echo "$status" >"$RESULT_FILE"
fi
' \
    -z "$SDB_LABEL" \
'
if "$MOUNT_SCRIPT" sdb >>"$ACTION_LOG" 2>&1; then
    echo "0" >"$RESULT_FILE"
else
    status=$?
    echo "$status" >"$RESULT_FILE"
fi
' \
    -z "$SR0_LABEL" \
'
if "$MOUNT_SCRIPT" sr0 >>"$ACTION_LOG" 2>&1; then
    echo "0" >"$RESULT_FILE"
else
    status=$?
    echo "$status" >"$RESULT_FILE"
fi
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

if [[ -s "$ACTION_LOG" ]]; then
    cat "$ACTION_LOG"
fi

STATUS="$(cat "$RESULT_FILE")"

if [[ "$STATUS" != "0" ]]; then
    echo "Mount toggle failed with exit status $STATUS."
fi

rm -f "$RESULT_FILE" "$ACTION_LOG"

echo
read -n 1 -rsp "Press any key to close..."
