#!/usr/bin/env bash
set -u
BACKEND_DIR="${HOME:?HOME is required}/.config/sway/scripts/audio-backends"
for part in 10_session.sh 20_menu_helpers.sh 30_camera.sh 40_main_menu.sh; do
    [[ -r "$BACKEND_DIR/$part" ]] || { echo "Missing audio backend: $BACKEND_DIR/$part" >&2; exit 1; }
    source "$BACKEND_DIR/$part"
done
