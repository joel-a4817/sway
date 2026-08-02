#!/usr/bin/env bash

set -u

DEVICE_NAME="${1:-}"

case "$DEVICE_NAME" in
    sda|sdb|sr0)
        ;;
    *)
        echo "Usage: $0 {sda|sdb|sr0}"
        exit 2
        ;;
esac

DEVICE="/dev/$DEVICE_NAME"
MOUNT_POINT="$HOME/mount/$DEVICE_NAME"

if [[ ! -b "$DEVICE" ]]; then
    echo "Error: block device does not exist: $DEVICE"
    exit 1
fi

EXISTING_MOUNT="$(
    findmnt -rn -S "$DEVICE" -o TARGET |
        head -n 1
)"

if [[ -n "$EXISTING_MOUNT" ]]; then
    echo "Unmounting $DEVICE from $EXISTING_MOUNT..."

    if sudo umount -- "$EXISTING_MOUNT"; then
        echo "Successfully unmounted $DEVICE from $EXISTING_MOUNT."
    else
        echo "Error: failed to unmount $DEVICE from $EXISTING_MOUNT."
        exit 1
    fi
else
    if ! mkdir -p -- "$MOUNT_POINT"; then
        echo "Error: failed to create mount point: $MOUNT_POINT"
        exit 1
    fi

    echo "Mounting $DEVICE at $MOUNT_POINT..."

    if sudo mount -- "$DEVICE" "$MOUNT_POINT"; then
        echo "Successfully mounted $DEVICE at $MOUNT_POINT."
    else
        echo "Error: failed to mount $DEVICE at $MOUNT_POINT."
        exit 1
    fi
fi
