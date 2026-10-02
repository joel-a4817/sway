#!/usr/bin/env bash
set -euo pipefail

# Standalone NetworkManager toggle; does not start or stop the audio server.
connection='AirPlay Direct'
state_dir="${XDG_RUNTIME_DIR:-${HOME:?}/.local/state}/airplay-hotspot-toggle"
mkdir -p "$state_dir"
exec 9>"$state_dir/lock"
flock -x 9

command -v nmcli >/dev/null || { echo 'nmcli is not installed.' >&2; exit 1; }
# Fail explicitly if the named profile is missing rather than reporting success.
nmcli -g connection.id connection show "$connection" >/dev/null
if nmcli -t -f NAME connection show --active | grep -Fqx -- "$connection"; then
    nmcli connection down "$connection"
    echo 'AirPlay hotspot stopped.'
else
    nmcli connection up "$connection"
    echo 'AirPlay hotspot started.'
fi
