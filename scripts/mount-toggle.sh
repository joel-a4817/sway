#!/usr/bin/env bash

DEVICE="/dev/sda"
MOUNT_POINT="$HOME/mount"

if [[ ! -b "$DEVICE" ]]; then
    echo "Error: block device does not exist: $DEVICE"
    exit 1
fi

if mountpoint -q -- "$MOUNT_POINT"; then
    echo "Unmounting $MOUNT_POINT..."

    if sudo umount -- "$MOUNT_POINT"; then
        echo "Successfully unmounted $DEVICE from $MOUNT_POINT."
    else
        echo "Error: failed to unmount $MOUNT_POINT."
        exit 1
    fi
else
    if findmnt -rn -S "$DEVICE" >/dev/null; then
        EXISTING_MOUNT="$(findmnt -rn -S "$DEVICE" -o TARGET | head -n 1)"
        echo "Error: $DEVICE is already mounted at $EXISTING_MOUNT."
        exit 1
    fi

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
