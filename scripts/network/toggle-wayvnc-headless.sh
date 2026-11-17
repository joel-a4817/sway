#!/usr/bin/env bash
set -Eeuo pipefail

VNC_UNIT=sway-headless-wayvnc.service
SWAY_UNIT=sway-vnc-headless-session.service
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sway/vnc"
OUTPUT_FILE="$STATE_DIR/output"
CREATED_OUTPUT_FILE="$STATE_DIR/created-output"
OWN_SWAY_FILE="$STATE_DIR/owns-sway-session"
LOCK_FILE="$STATE_DIR/lifecycle.lock"
PORT="${WAYVNC_PORT:-5900}"
mkdir -p "$STATE_DIR"

log_failure() {
  local unit="$1"
  journalctl --user -u "$unit" -n 60 --no-pager >&2 || true
}

lock() {
  exec 9>"$LOCK_FILE"
  flock -w 15 9 || { echo 'Another VNC operation is already in progress.' >&2; exit 1; }
  if [[ -f "$OWN_SWAY_FILE" ]] && ! "$HOME/.config/sway/scripts/systemd-userctl.sh" is-active --quiet "$SWAY_UNIT"; then
    rm -f "$OWN_SWAY_FILE" "$OUTPUT_FILE" "$CREATED_OUTPUT_FILE"
  fi
}

find_sway_socket() {
  local candidate
  if [[ -n "${SWAYSOCK:-}" && -S "$SWAYSOCK" ]] && swaymsg -s "$SWAYSOCK" -r -t get_version >/dev/null 2>&1; then
    printf '%s\n' "$SWAYSOCK"
    return 0
  fi
  while IFS= read -r candidate; do
    if swaymsg -s "$candidate" -r -t get_version >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(find "$RUNTIME_DIR" -maxdepth 1 -type s -name 'sway-ipc.*.sock' -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-)
  return 1
}

start_headless_sway() {
  rm -f "$OWN_SWAY_FILE"
  "$HOME/.config/sway/scripts/systemd-userctl.sh" stop "$SWAY_UNIT" 2>/dev/null || true
  "$HOME/.config/sway/scripts/systemd-userctl.sh" reset-failed "$SWAY_UNIT" 2>/dev/null || true
  "$HOME/.config/sway/scripts/systemd-userctl.sh" --systemd-run --unit="$SWAY_UNIT" --collect --quiet \
    --setenv="XDG_RUNTIME_DIR=$RUNTIME_DIR" \
    --setenv=WLR_BACKENDS=headless --setenv=WLR_HEADLESS_OUTPUTS=1 \
    --setenv=WLR_LIBINPUT_NO_DEVICES=1 \
    --property=Restart=no --property=TimeoutStopSec=10s \
    sway --config "$HOME/.config/sway/config"
}

ensure_sway_socket() {
  local socket deadline
  if socket="$(find_sway_socket)"; then
    printf '%s\n' "$socket"
    return 0
  fi
  start_headless_sway
  deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    if socket="$(find_sway_socket)"; then
      : > "$OWN_SWAY_FILE"
      printf '%s\n' "$socket"
      return 0
    fi
    "$HOME/.config/sway/scripts/systemd-userctl.sh" is-active --quiet "$SWAY_UNIT" || break
    sleep 0.2
  done
  echo 'The dedicated headless Sway session failed to start.' >&2
  log_failure "$SWAY_UNIT"
  "$HOME/.config/sway/scripts/systemd-userctl.sh" stop "$SWAY_UNIT" 2>/dev/null || true
  return 1
}

sway_cmd() { swaymsg -s "$SWAYSOCK" "$@"; }
sway_outputs() { sway_cmd -r -t get_outputs; }

sway_pid() {
  basename "$SWAYSOCK" | sed -n 's/^sway-ipc\.[0-9][0-9]*\.\([0-9][0-9]*\)\.sock$/\1/p'
}

find_wayland_display() {
  local pid inode path value
  pid="$(sway_pid)"
  if [[ -n "$pid" && -d "/proc/$pid/fd" ]]; then
    while IFS= read -r inode; do
      path="$(awk -v inode="$inode" '$7 == inode && $8 ~ /\/wayland-[0-9]+$/ {print $8; exit}' /proc/net/unix)"
      if [[ -n "$path" && -S "$path" ]]; then
        basename "$path"
        return 0
      fi
    done < <(find "/proc/$pid/fd" -maxdepth 1 -type l -printf '%l\n' 2>/dev/null | sed -n 's/^socket:\[\([0-9][0-9]*\)\]$/\1/p')
  fi
  return 1
}

ensure_headless_output() {
  local name deadline created=0
  name="$(sway_outputs | jq -r '[.[] | select(.name | startswith("HEADLESS-")) | select(.active == true) | .name][0] // empty')"
  rm -f "$CREATED_OUTPUT_FILE"
  if [[ -z "$name" ]]; then
    sway_cmd -q create_output
    created=1
    deadline=$((SECONDS + 8))
    while (( SECONDS < deadline )); do
      name="$(sway_outputs | jq -r '[.[] | select(.name | startswith("HEADLESS-")) | select(.active == true) | .name][0] // empty')"
      [[ -n "$name" ]] && break
      sleep 0.1
    done
  fi
  [[ -n "$name" ]] || { echo 'Sway did not expose an active HEADLESS output.' >&2; return 1; }
  sway_cmd -q output "$name" mode 1920x1080@60Hz
  (( created )) && printf '%s\n' "$name" > "$CREATED_OUTPUT_FILE"
  printf '%s\n' "$name"
}

bind_address() {
  if [[ -n "${WAYVNC_ADDRESS:-}" ]]; then
    printf '%s\n' "$WAYVNC_ADDRESS"
  else
    ip -4 -o addr show dev tailscale0 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}'
  fi
}

cleanup_failed_start() {
  "$HOME/.config/sway/scripts/systemd-userctl.sh" stop "$VNC_UNIT" 2>/dev/null || true
  if [[ -f "$OWN_SWAY_FILE" ]]; then
    "$HOME/.config/sway/scripts/systemd-userctl.sh" stop "$SWAY_UNIT" 2>/dev/null || true
    rm -f "$OWN_SWAY_FILE"
  fi
  rm -f "$OUTPUT_FILE" "$CREATED_OUTPUT_FILE"
}

start_vnc() {
  local output address wayland_display deadline restarts_a restarts_b
  for command in sway swaymsg wayvnc jq ip systemctl systemd-run; do
    command -v "$command" >/dev/null || { echo "$command is not installed." >&2; return 1; }
  done
  export XDG_RUNTIME_DIR="$RUNTIME_DIR"
  SWAYSOCK="$(ensure_sway_socket)" || return 1
  export SWAYSOCK
  output="$(ensure_headless_output)" || { cleanup_failed_start; return 1; }
  wayland_display="$(find_wayland_display)" || { echo 'Could not identify the Wayland socket owned by Sway.' >&2; cleanup_failed_start; return 1; }
  address="$(bind_address)"
  [[ -n "$address" ]] || { echo 'No Tailscale IPv4 address is available.' >&2; cleanup_failed_start; return 1; }
  printf '%s\n' "$output" > "$OUTPUT_FILE"

  "$HOME/.config/sway/scripts/systemd-userctl.sh" stop "$VNC_UNIT" 2>/dev/null || true
  "$HOME/.config/sway/scripts/systemd-userctl.sh" reset-failed "$VNC_UNIT" 2>/dev/null || true
  "$HOME/.config/sway/scripts/systemd-userctl.sh" --systemd-run --unit="$VNC_UNIT" --collect --quiet \
    --setenv="XDG_RUNTIME_DIR=$RUNTIME_DIR" --setenv="WAYLAND_DISPLAY=$wayland_display" \
    --property=Restart=on-failure --property=RestartSec=1s --property=TimeoutStopSec=10s \
    wayvnc --max-fps=30 --output "$output" "$address" "$PORT"

  deadline=$((SECONDS + 10))
  while (( SECONDS < deadline )); do
    if "$HOME/.config/sway/scripts/systemd-userctl.sh" is-active --quiet "$VNC_UNIT"; then
      restarts_a="$("$HOME/.config/sway/scripts/systemd-userctl.sh" show "$VNC_UNIT" -p NRestarts --value)"
      sleep 1
      restarts_b="$("$HOME/.config/sway/scripts/systemd-userctl.sh" show "$VNC_UNIT" -p NRestarts --value)"
      if "$HOME/.config/sway/scripts/systemd-userctl.sh" is-active --quiet "$VNC_UNIT" && [[ "$restarts_a" == "$restarts_b" ]]; then
        echo "VNC started on $address:$PORT using $output."
        return 0
      fi
    fi
    "$HOME/.config/sway/scripts/systemd-userctl.sh" is-failed --quiet "$VNC_UNIT" && break
    sleep 0.2
  done
  echo 'wayvnc failed to remain running.' >&2
  log_failure "$VNC_UNIT"
  cleanup_failed_start
  return 1
}

stop_vnc() {
  local socket output
  "$HOME/.config/sway/scripts/systemd-userctl.sh" stop "$VNC_UNIT" 2>/dev/null || true
  if [[ -f "$OWN_SWAY_FILE" ]]; then
    "$HOME/.config/sway/scripts/systemd-userctl.sh" stop "$SWAY_UNIT" 2>/dev/null || true
    rm -f "$OWN_SWAY_FILE"
  elif [[ -s "$CREATED_OUTPUT_FILE" ]] && socket="$(find_sway_socket)"; then
    output="$(cat "$CREATED_OUTPUT_FILE")"
    swaymsg -s "$socket" -q output "$output" disable 2>/dev/null || true
  fi
  rm -f "$OUTPUT_FILE" "$CREATED_OUTPUT_FILE"
  echo 'VNC stopped.'
}

case "${1:-toggle}" in
  status) "$HOME/.config/sway/scripts/systemd-userctl.sh" is-active --quiet "$VNC_UNIT" ;;
  start) lock; "$HOME/.config/sway/scripts/systemd-userctl.sh" is-active --quiet "$VNC_UNIT" || start_vnc ;;
  stop) lock; stop_vnc ;;
  toggle) lock; if "$HOME/.config/sway/scripts/systemd-userctl.sh" is-active --quiet "$VNC_UNIT"; then stop_vnc; else start_vnc; fi ;;
  *) echo "Usage: ${0##*/} [toggle|start|stop|status]" >&2; exit 2 ;;
esac
