#!/usr/bin/env bash
set -Eeuo pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sway/mount"
LOCK_FILE="$STATE_DIR/usb-format.lock"
mkdir -p -m 700 "$STATE_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo 'Another USB erase/reformat menu is already open.'; exit 0; }

pause_terminal() {
  echo
  if [[ -r /dev/tty && -w /dev/tty ]]; then
    printf '%s' 'Press any key to close...' >/dev/tty
    IFS= read -n 1 -r -s </dev/tty || true
    printf '\n' >/dev/tty
  else
    echo 'Press any key to close...'
    sleep 3
  fi
}
finish() {
  local status=$?
  trap - EXIT
  if ((status != 0 && status != 130)); then
    echo
    echo "USB operation stopped with exit status $status."
  fi
  pause_terminal
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT TERM HUP

need() {
  local missing=() command
  for command in "$@"; do command -v "$command" >/dev/null 2>&1 || missing+=("$command"); done
  ((${#missing[@]} == 0)) || { echo "Unavailable; missing: ${missing[*]}"; return 1; }
}
read_value() { IFS= read -r "$1" || exit 0; }
header() { printf '\n%s\n\n[E] Exit\n' "$1"; }

disk_identity() {
  local device="$1" serial size model
  serial="$(udevadm info --query=property --name="$device" 2>/dev/null | sed -n 's/^ID_SERIAL=//p' | head -n1)"
  size="$(sudo blockdev --getsize64 "$device")"
  model="$(lsblk -dnro MODEL "$device" | sed 's/[[:space:]]*$//')"
  printf '%s|%s|%s\n' "$serial" "$size" "$model"
}
root_disk() {
  local source
  source="$(findmnt -rn -o SOURCE / 2>/dev/null || true)"
  [[ -b $source ]] || return 0
  lsblk -srnpo NAME,TYPE "$source" | awk '$2=="disk" {print $1; exit}'
}
list_usb() {
  lsblk -J -b -d -o PATH,TYPE,TRAN,RM,SIZE,VENDOR,MODEL,SERIAL | jq -r '
    .blockdevices[]
    | select(.type == "disk" and (.tran == "usb" or .rm == true or .rm == 1))
    | [.path, (.size|tostring), (.vendor // ""), (.model // ""), (.serial // "")]
    | @base64'
}
select_device() {
  local answer index decoded system_disk
  while :; do
    mapfile -t DEVICE_ROWS < <(list_usb)
    header 'USB erase and reformat | Device'
    ((${#DEVICE_ROWS[@]})) || { echo 'No removable USB disk was found.'; return 1; }
    for index in "${!DEVICE_ROWS[@]}"; do
      decoded="$(printf '%s' "${DEVICE_ROWS[$index]}" | base64 -d)"
      printf '[%d] %s  %s  %s %s  %s\n' "$((index+1))" \
        "$(jq -r '.[0]' <<<"$decoded")" \
        "$(numfmt --to=iec-i --suffix=B "$(jq -r '.[1]' <<<"$decoded")")" \
        "$(jq -r '.[2]' <<<"$decoded")" "$(jq -r '.[3]' <<<"$decoded")" "$(jq -r '.[4]' <<<"$decoded")"
    done
    printf '\nSelect: '; read_value answer
    [[ $answer =~ ^[Ee]$ ]] && exit 0
    [[ $answer =~ ^[0-9]+$ ]] && ((10#$answer >= 1 && 10#$answer <= ${#DEVICE_ROWS[@]})) || { echo 'Enter E or a listed number.'; continue; }
    decoded="$(printf '%s' "${DEVICE_ROWS[$((10#$answer-1))]}" | base64 -d)"
    DEVICE="$(jq -r '.[0]' <<<"$decoded")"
    [[ -b $DEVICE && $(lsblk -dnro TYPE "$DEVICE") == disk ]] || { echo 'That disk disappeared.'; continue; }
    system_disk="$(root_disk)"
    [[ -z $system_disk || $DEVICE != "$system_disk" ]] || { echo 'Refusing to erase the disk containing /.'; continue; }
    DEVICE_IDENTITY="$(disk_identity "$DEVICE")"
    return 0
  done
}
choose_action() {
  local answer
  while :; do
    header 'USB erase and reformat | Purpose'
    echo '[B] Back'
    echo '[1] exFAT data drive (Windows, macOS and Linux; files over 4 GiB)'
    echo '[2] FAT32 data/boot drive (widest compatibility; 4 GiB per-file limit)'
    echo '[3] NTFS data drive (Windows-focused; macOS normally read-only)'
    echo '[4] ext4 data drive (Linux-focused)'
    echo '[5] Write an ISO image to the whole USB'
    echo '[6] Blank GPT disk'
    echo '[7] Full zero overwrite, then exFAT (slow; adds flash wear)'
    printf '\nSelect: '; read_value answer
    case "$answer" in [Ee]) exit 0;; [Bb]) return 2;; 1|2|3|4|5|6|7) ACTION="$answer"; return 0;; *) echo 'Enter E, B, or a listed number.';; esac
  done
}
ask_label() {
  local limit="$1" answer
  while :; do
    printf 'Volume label [USB] (B=Back, E=Exit; maximum %s characters): ' "$limit"; read_value answer
    case "${answer,,}" in e) exit 0;; b) return 2;; esac
    LABEL="${answer:-USB}"
    if [[ ! $LABEL =~ ^[A-Za-z0-9._\ -]+$ ]] || ((${#LABEL} > limit)); then
      echo "Use 1-$limit ASCII letters, numbers, spaces, dots, underscores or hyphens."
      continue
    fi
    return 0
  done
}
ask_iso() {
  local answer expanded iso_size usb_size
  while :; do
    printf 'ISO file path (B=Back, E=Exit): '; read_value answer
    case "${answer,,}" in e) exit 0;; b) return 2;; esac
    [[ $answer == '~/'* ]] && expanded="$HOME/${answer:2}" || expanded="$answer"
    [[ -f $expanded && -r $expanded ]] || { echo 'The ISO does not exist or is unreadable.'; continue; }
    [[ ${expanded,,} == *.iso ]] || { echo 'The file must end in .iso.'; continue; }
    ISO_PATH="$(readlink -f -- "$expanded")"
    iso_size="$(stat -c %s -- "$ISO_PATH")"; usb_size="$(sudo blockdev --getsize64 "$DEVICE")"
    ((iso_size <= usb_size)) || { echo 'The ISO is larger than the USB.'; continue; }
    return 0
  done
}
mounted_targets() {
  local node
  while IFS= read -r node; do findmnt -rn -S "$node" -o TARGET 2>/dev/null || true; done < <(lsblk -rnpo NAME "$DEVICE")
}
unmount_all() {
  local target
  mapfile -t targets < <(mounted_targets | awk 'NF && !seen[$0]++')
  for ((index=${#targets[@]}-1; index>=0; index--)); do target="${targets[$index]}"; echo "Unmounting $target..."; sudo umount -- "$target"; done
  [[ -z $(mounted_targets) ]] || { echo 'A filesystem on the USB is still mounted.' >&2; return 1; }
}
confirm_destroy() {
  local typed
  [[ -b $DEVICE && $(disk_identity "$DEVICE") == "$DEVICE_IDENTITY" ]] || { echo 'The selected USB identity changed.' >&2; return 1; }
  echo; echo 'FINAL REVIEW'; echo "Device: $DEVICE"; lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS "$DEVICE"
  echo "Action: $DESCRIPTION"; [[ -z ${ISO_PATH:-} ]] || echo "ISO: $ISO_PATH"
  echo; echo 'ALL DATA ON THIS USB WILL BE DESTROYED.'
  printf 'Type the exact device path (%s): ' "$DEVICE"; read_value typed
  [[ $typed == "$DEVICE" ]] || { echo 'Cancelled. Nothing was changed.'; return 2; }
  printf 'Type ERASE: '; read_value typed
  [[ $typed == ERASE ]] || { echo 'Cancelled. Nothing was changed.'; return 2; }
}
wipe_existing() {
  local node
  mapfile -t nodes < <(lsblk -rnpo NAME,TYPE "$DEVICE" | awk '$2=="part" {print $1}')
  for node in "${nodes[@]}"; do sudo wipefs --all --force "$node"; done
  sudo wipefs --all --force "$DEVICE"
  sudo blockdev --rereadpt "$DEVICE" 2>/dev/null || true
  sudo udevadm settle
}
create_partition() {
  local deadline partition
  sudo parted --script --align optimal "$DEVICE" mklabel gpt mkpart primary 1MiB 100%
  sudo partprobe "$DEVICE"; sudo udevadm settle
  deadline=$((SECONDS+10))
  while ((SECONDS < deadline)); do
    partition="$(lsblk -rnpo NAME,TYPE "$DEVICE" | awk '$2=="part" {print $1; exit}')"
    [[ -n $partition && -b $partition ]] && { printf '%s\n' "$partition"; return 0; }
    sleep 0.1
  done
  echo 'The new partition did not appear.' >&2; return 1
}
format_filesystem() {
  local kind="$1" partition expected actual
  wipe_existing
  partition="$(create_partition)"
  sudo wipefs --all --force "$partition"
  case "$kind" in
    exfat) sudo mkfs.exfat -F -n "$LABEL" "$partition"; expected=exfat;;
    fat32) sudo mkfs.vfat -F 32 -n "$LABEL" "$partition"; expected=vfat;;
    ntfs) sudo mkfs.ntfs -F -L "$LABEL" "$partition"; expected=ntfs;;
    ext4) sudo mkfs.ext4 -F -L "$LABEL" "$partition"; expected=ext4;;
  esac
  sync; sudo udevadm settle
  actual="$(sudo blkid -p -s TYPE -o value "$partition" 2>/dev/null || true)"
  [[ $actual == "$expected" ]] || { echo "Verification failed: expected $expected, found ${actual:-nothing}." >&2; return 1; }
}
verify_iso_write() {
  local expected actual compare_bytes iso_bytes
  iso_bytes="$(stat -c %s -- "$ISO_PATH")"
  compare_bytes=$((iso_bytes < 1048576 ? iso_bytes : 1048576))
  expected="$(head -c "$compare_bytes" -- "$ISO_PATH" | sha256sum | awk '{print $1}')"
  actual="$(sudo head -c "$compare_bytes" -- "$DEVICE" | sha256sum | awk '{print $1}')"
  [[ $actual == "$expected" ]] || { echo "ISO verification failed in the first $compare_bytes bytes." >&2; return 1; }
}

need lsblk findmnt jq numfmt base64 flock udevadm sudo readlink stat blockdev blkid awk sed sha256sum head || exit 1
while :; do
  select_device || exit 0
  action_status=0; choose_action || action_status=$?
  ((action_status == 2)) && continue
  ((action_status == 0)) || exit "$action_status"
  unset ISO_PATH LABEL
  case "$ACTION" in
    1) need wipefs parted partprobe mkfs.exfat || continue; ask_label 15 || continue; DESCRIPTION="Create GPT exFAT labelled $LABEL";;
    2) need wipefs parted partprobe mkfs.vfat || continue; ask_label 11 || continue; DESCRIPTION="Create GPT FAT32 labelled $LABEL";;
    3) need wipefs parted partprobe mkfs.ntfs || continue; ask_label 32 || continue; DESCRIPTION="Create GPT NTFS labelled $LABEL";;
    4) need wipefs parted partprobe mkfs.ext4 || continue; ask_label 16 || continue; DESCRIPTION="Create GPT ext4 labelled $LABEL";;
    5) need wipefs dd || continue; ask_iso || continue; DESCRIPTION='Write the ISO to the whole USB';;
    6) need wipefs parted partprobe sfdisk || continue; DESCRIPTION='Create an empty GPT disk';;
    7) need wipefs parted partprobe mkfs.exfat dd || continue; ask_label 15 || continue; DESCRIPTION="Zero the USB, then create GPT exFAT labelled $LABEL";;
  esac
  confirm_status=0; confirm_destroy || confirm_status=$?
  ((confirm_status == 2)) && continue
  ((confirm_status == 0)) || exit "$confirm_status"
  unmount_all
  case "$ACTION" in
    1) format_filesystem exfat;;
    2) format_filesystem fat32;;
    3) format_filesystem ntfs;;
    4) format_filesystem ext4;;
    5) wipe_existing; sudo dd if="$ISO_PATH" of="$DEVICE" bs=16M status=progress conv=fsync; sudo partprobe "$DEVICE" || true; sudo udevadm settle; verify_iso_write;;
    6)
      wipe_existing
      sudo parted --script "$DEVICE" mklabel gpt
      sudo partprobe "$DEVICE"
      sudo udevadm settle
      sudo sfdisk --json "$DEVICE" | jq -e '.partitiontable.label == "gpt" and (.partitiontable.partitions | length == 0)' >/dev/null
      ;;
    7) sudo dd if=/dev/zero of="$DEVICE" bs=16M status=progress conv=fsync; format_filesystem exfat;;
  esac
  echo; echo 'Completed successfully. Current device layout:'; lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS "$DEVICE"
  exit 0
done
