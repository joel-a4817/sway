#!/usr/bin/env bash
set -Eeuo pipefail

mode=systemctl
if [[ ${1:-} == --systemd-run ]]; then
  mode=systemd-run
  shift
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

reload_manager() {
  systemctl --user daemon-reload >/dev/null
  systemctl --user reset-failed >/dev/null 2>&1 || true
}

if [[ $mode == systemd-run ]]; then
  reload_manager
  tmp="${TMPDIR:-/tmp}/systemd-userctl.$$.err"
  trap 'rm -f "$tmp"' EXIT
  set +e
  systemd-run --user "$@" 2> >(tee "$tmp" >&2)
  status=$?
  set -e
  (( status == 0 )) && exit 0
  if grep -Eqi 'failed to connect to bus|transport endpoint|connection reset|connection refused|org\.freedesktop\.systemd1' "$tmp"; then
    reload_manager
    systemd-run --user "$@"
    exit $?
  fi
  exit "$status"
fi

((${#@})) || { echo "Usage: ${0##*/} [--systemd-run] COMMAND [ARGS...]" >&2; exit 2; }
action=$1
case "$action" in
  start|restart|try-restart|reload-or-restart|reload-or-try-restart)
    reload_manager
    ;;
esac

tmp="${TMPDIR:-/tmp}/systemd-userctl.$$.err"
trap 'rm -f "$tmp"' EXIT
set +e
systemctl --user "$@" 2> >(tee "$tmp" >&2)
status=$?
set -e
(( status == 0 )) && exit 0
# One bounded recovery only for manager/unit-cache failures. Never loop, unmask,
# enable, or substitute a different unit.
if grep -Eqi 'unit .* (not found|could not be found)|failed to connect to bus|transport endpoint|connection reset|connection refused|org\.freedesktop\.systemd1' "$tmp"; then
  reload_manager
  systemctl --user "$@"
  exit $?
fi
exit "$status"
