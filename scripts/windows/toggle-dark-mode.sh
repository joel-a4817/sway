#!/usr/bin/env bash
# Toggle global dark/light mode.
# GTK3/GTK4 read org.gnome.desktop.interface; xdg-desktop-portal-gtk forwards
# color-scheme to libadwaita, Firefox, Chromium/Electron and Qt 6.5+.
#   toggle-dark-mode.sh            toggle
#   toggle-dark-mode.sh dark|light force
set -euo pipefail

key=/org/gnome/desktop/interface
mode=${1:-toggle}
if [[ $mode == toggle ]]; then
    if [[ $(dconf read "$key/color-scheme") == "'prefer-dark'" ]]; then mode=light; else mode=dark; fi
fi
case $mode in
    dark)  dconf write "$key/color-scheme" "'prefer-dark'"; dconf write "$key/gtk-theme" "'Adwaita-dark'" ;;
    light) dconf write "$key/color-scheme" "'default'";     dconf write "$key/gtk-theme" "'Adwaita'" ;;
    *) echo "usage: ${0##*/} [dark|light]" >&2; exit 2 ;;
esac
