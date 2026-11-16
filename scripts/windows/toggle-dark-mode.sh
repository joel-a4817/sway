#!/usr/bin/env bash
# Toggle the persisted desktop color preference. No startup mode is forced.
set -Eeuo pipefail

key=/org/gnome/desktop/interface
mode="${1:-toggle}"

case "$mode" in
  toggle)
    current="$(dconf read "$key/color-scheme" 2>/dev/null || true)"
    if [[ "$current" == "'prefer-dark'" ]]; then
      mode=light
    else
      mode=dark
    fi
    ;;
  dark|light) ;;
  *)
    printf 'Usage: %s [dark|light|toggle]\n' "${0##*/}" >&2
    exit 2
    ;;
esac

case "$mode" in
  dark)
    dconf write "$key/color-scheme" "'prefer-dark'"
    dconf write "$key/gtk-theme" "'Adwaita-dark'"
    ;;
  light)
    dconf write "$key/color-scheme" "'default'"
    dconf write "$key/gtk-theme" "'Adwaita'"
    ;;
esac

# Ensure D-Bus/systemd-launched applications inherit the live Sway session only.
if [[ -n "${WAYLAND_DISPLAY:-}" && -n "${SWAYSOCK:-}" ]]; then
  dbus-update-activation-environment --systemd \
    WAYLAND_DISPLAY SWAYSOCK \
    XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_SESSION_DESKTOP
fi

printf '%s\n' "$mode"
