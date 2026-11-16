#!/usr/bin/env bash
set -Eeuo pipefail
exec "$HOME/.config/sway/scripts/windows/float-state-daemon.py" --toggle
