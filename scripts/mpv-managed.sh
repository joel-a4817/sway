#!/usr/bin/env bash
set -euo pipefail
runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$runtime/media-control-mpv"
socket="$runtime/media-control-mpv/mpv-$$.sock"
exec mpv --input-ipc-server="$socket" "$@"
