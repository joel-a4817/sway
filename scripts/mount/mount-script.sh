#!/usr/bin/env bash
set -Eeuo pipefail

for command in readlink lsblk findmnt awk basename tr sed id sudo mount umount grep; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing command: $command" >&2; exit 127; }
done

SOURCE_INPUT="${1:-}"
[[ -n $SOURCE_INPUT ]] || { echo "Usage: $0 /dev/<filesystem-device>" >&2; exit 2; }
SOURCE="$(readlink -f -- "$SOURCE_INPUT")"
[[ -b $SOURCE ]] || { echo "Not a block device: $SOURCE" >&2; exit 1; }

FSTYPE="$(lsblk -dnro FSTYPE "$SOURCE" | head -n1)"
[[ -n $FSTYPE ]] || { echo "No filesystem found on $SOURCE" >&2; exit 1; }

ROOT_SOURCE="$(findmnt -rn -o SOURCE / 2>/dev/null || true)"
ROOT_SOURCE="$(readlink -f -- "$ROOT_SOURCE" 2>/dev/null || printf '%s' "$ROOT_SOURCE")"
[[ $SOURCE != "$ROOT_SOURCE" ]] || { echo "Refusing to toggle the root filesystem: $SOURCE" >&2; exit 1; }

mapfile -t TARGETS < <(findmnt -rn -S "$SOURCE" -o TARGET 2>/dev/null | awk 'NF && !seen[$0]++')
if ((${#TARGETS[@]})); then
  for ((index=${#TARGETS[@]}-1; index>=0; index--)); do
    echo "Unmounting ${TARGETS[$index]}..."
    sudo umount -- "${TARGETS[$index]}"
  done
  echo "Unmounted $SOURCE."
  exit 0
fi

DEVICE_NAME="$(basename -- "$SOURCE")"
LABEL="$(lsblk -dnro LABEL "$SOURCE" | head -n1)"
SAFE_LABEL="$(printf '%s' "$LABEL" | tr -cs '[:alnum:]._-' '_' | sed 's/^_*//;s/_*$//')"
MOUNT_POINT="$HOME/mount/${SAFE_LABEL:+$SAFE_LABEL-}$DEVICE_NAME"
mkdir -p -- "$MOUNT_POINT"

MOUNT_OPTIONS=()
case "$FSTYPE" in
  exfat|vfat|fat|msdos|ntfs|ntfs3)
    MOUNT_OPTIONS=(-o "uid=$(id -u),gid=$(id -g),umask=022")
    ;;
esac

echo "Mounting $SOURCE ($FSTYPE) at $MOUNT_POINT..."
if sudo mount "${MOUNT_OPTIONS[@]}" -- "$SOURCE" "$MOUNT_POINT"; then
  if findmnt -rn -S "$SOURCE" -o TARGET | grep -Fx -- "$MOUNT_POINT" >/dev/null; then
    echo "Mounted $SOURCE at $MOUNT_POINT."
  else
    echo "Mount returned success but verification failed for $SOURCE." >&2
    exit 1
  fi
else
  rmdir -- "$MOUNT_POINT" 2>/dev/null || true
  echo "Failed to mount $SOURCE." >&2
  exit 1
fi
