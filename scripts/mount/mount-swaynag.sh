#!/usr/bin/env bash
set -Eeuo pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sway/mount"
MOUNT_SCRIPT="$HOME/.config/sway/scripts/mount/mount-script.sh"
FORMAT_SCRIPT="$HOME/.config/sway/scripts/mount/usb-format-menu.sh"
LOG="$STATE_DIR/mount-menu.log"
RESULT_FILE="$STATE_DIR/selection.$$"
ROWS_FILE="$STATE_DIR/devices.$$"
SWAYNAG_PID=''
mkdir -p -m 700 "$STATE_DIR"
: >"$LOG"

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
cleanup() {
  if [[ $SWAYNAG_PID =~ ^[0-9]+$ ]]; then
    kill "$SWAYNAG_PID" 2>/dev/null || true
    wait "$SWAYNAG_PID" 2>/dev/null || true
  fi
  rm -f -- "$RESULT_FILE" "$ROWS_FILE"
}
fatal() {
  local message="$1" status="${2:-1}"
  echo "Mount menu error: $message" | tee -a "$LOG" >&2
  echo "Log: $LOG" >&2
  pause_terminal
  exit "$status"
}
trap cleanup EXIT INT TERM HUP

for command in swaymsg swaynag jq lsblk findmnt readlink base64 numfmt mktemp; do
  command -v "$command" >/dev/null 2>&1 || fatal "missing command: $command" 127
done
[[ -x $MOUNT_SCRIPT ]] || fatal "missing executable: $MOUNT_SCRIPT" 126

if ! lsblk -J -b -p -o PATH,TYPE,TRAN,RM,SIZE,FSTYPE,LABEL,MODEL,MOUNTPOINTS 2>>"$LOG" | jq -r '
  def descend($parent_tran; $parent_rm; $parent_model):
    (.tran // $parent_tran) as $tran
    | (.rm // $parent_rm) as $rm
    | (.model // $parent_model // "") as $model
    | (if ((.fstype // "") != "" and ($tran == "usb" or $rm == true or $rm == 1 or .type == "rom"))
       then [[.path, (.size|tostring), .fstype, (.label // ""), $model,
              ((.mountpoints // []) | map(select(. != null)) | join(", "))]]
       else [] end)
      + ((.children // []) | map(descend($tran; $rm; $model)) | add // []);
  [.blockdevices[] | descend(null; false; "")] | add // [] | .[] | @base64
' >"$ROWS_FILE"; then
  fatal 'device discovery failed'
fi

OUTPUT="$(swaymsg -t get_outputs -r 2>>"$LOG" | jq -r '.[] | select(.focused == true) | .name' | head -n1)"
[[ -n $OUTPUT ]] || fatal 'Sway reported no focused output'

SWAYNAG_ARGS=(-t warning -y overlay -o "$OUTPUT" -m 'Removable Media')
VISIBLE=0
while IFS= read -r encoded; do
  [[ -n $encoded ]] || continue
  decoded="$(printf '%s' "$encoded" | base64 -d)" || fatal 'could not decode a discovered device'
  path="$(jq -r '.[0]' <<<"$decoded")"
  bytes="$(jq -r '.[1]' <<<"$decoded")"
  fstype="$(jq -r '.[2]' <<<"$decoded")"
  label="$(jq -r '.[3]' <<<"$decoded")"
  model="$(jq -r '.[4]' <<<"$decoded")"
  mounts="$(jq -r '.[5]' <<<"$decoded")"
  [[ -b $path ]] || continue
  token="$(printf '%s' "$path" | base64 -w0)"
  size="$(numfmt --to=iec-i --suffix=B "$bytes" 2>/dev/null || printf '%s bytes' "$bytes")"
  display="${label:-$(basename -- "$path")}: $fstype, $size"
  [[ -n $model ]] && display="$display, $model"
  if [[ -n $mounts ]]; then
    button="Unmount $display at $mounts"
  else
    button="Mount $display"
  fi
  SWAYNAG_ARGS+=(-z "$button" "printf '%s\\n' '$token' > '$RESULT_FILE'")
  VISIBLE=$((VISIBLE+1))
done <"$ROWS_FILE"

[[ -x $FORMAT_SCRIPT ]] && SWAYNAG_ARGS+=(-z 'Erase / reformat USB' "printf '%s\\n' format > '$RESULT_FILE'")
if ((VISIBLE == 0)) && [[ ! -x $FORMAT_SCRIPT ]]; then
  fatal 'no removable filesystem is visible and the format menu is missing'
fi

printf '[%(%F %T)T] showing %d filesystem(s) on %s\n' -1 "$VISIBLE" "$OUTPUT" >>"$LOG"
swaynag "${SWAYNAG_ARGS[@]}" 2>>"$LOG" &
SWAYNAG_PID=$!
while [[ ! -e $RESULT_FILE ]]; do
  if ! kill -0 "$SWAYNAG_PID" 2>/dev/null; then
    wait "$SWAYNAG_PID" || true
    SWAYNAG_PID=''
    exit 0
  fi
  sleep 0.1
done
kill "$SWAYNAG_PID" 2>/dev/null || true
wait "$SWAYNAG_PID" 2>/dev/null || true
SWAYNAG_PID=''
selection="$(cat "$RESULT_FILE")"

if [[ $selection == format ]]; then
  rm -f -- "$RESULT_FILE" "$ROWS_FILE"
  trap - EXIT INT TERM HUP
  exec "$FORMAT_SCRIPT"
fi

source_device="$(printf '%s' "$selection" | base64 -d 2>/dev/null || true)"
[[ -b $source_device ]] || fatal 'the selected device disappeared'
printf '[%(%F %T)T] selected %s\n' -1 "$source_device" >>"$LOG"
if "$MOUNT_SCRIPT" "$source_device" 2>&1 | tee -a "$LOG"; then
  status=0
  echo
  echo 'Mount toggle completed successfully.'
else
  status=${PIPESTATUS[0]}
  echo
  echo "Mount toggle failed with exit status $status."
fi
printf '[%(%F %T)T] exit status %s\n' -1 "$status" >>"$LOG"
pause_terminal
exit "$status"
