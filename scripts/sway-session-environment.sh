#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-start}"
case "$action" in
  start)
    : "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
    export XDG_RUNTIME_DIR
    export XDG_CURRENT_DESKTOP=sway XDG_SESSION_DESKTOP=sway XDG_SESSION_TYPE=wayland
    dbus-update-activation-environment --systemd \
      HOME USER LOGNAME PATH XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS \
      DISPLAY WAYLAND_DISPLAY SWAYSOCK \
      XDG_CURRENT_DESKTOP XDG_SESSION_DESKTOP XDG_SESSION_TYPE \
      XDG_CONFIG_DIRS XDG_DATA_DIRS GTK_PATH GIO_EXTRA_MODULES
    systemctl --user import-environment \
      HOME USER LOGNAME PATH XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS \
      DISPLAY WAYLAND_DISPLAY SWAYSOCK \
      XDG_CURRENT_DESKTOP XDG_SESSION_DESKTOP XDG_SESSION_TYPE \
      XDG_CONFIG_DIRS XDG_DATA_DIRS GTK_PATH GIO_EXTRA_MODULES
    systemctl --user reset-failed graphical-session-pre.target graphical-session.target \
      xdg-desktop-portal.service xdg-desktop-portal-gtk.service \
      xdg-desktop-portal-wlr.service 2>/dev/null || true
    systemctl --user start graphical-session.target
    # GTK4/libadwaita clients obtain the dark preference from
    # org.freedesktop.portal.Settings. Restart stale portal instances only after
    # the live Sway environment and graphical target are established.
    systemctl --user try-restart xdg-desktop-portal-gtk.service \
      xdg-desktop-portal-wlr.service xdg-desktop-portal.service 2>/dev/null || true
    ;;
  stop)
    systemctl --user stop graphical-session.target || true
    systemctl --user unset-environment SWAYSOCK WAYLAND_DISPLAY DISPLAY || true
    ;;
  watch)
    : "${SWAYSOCK:?SWAYSOCK is required}"
    while [[ -S "$SWAYSOCK" ]]; do sleep 1; done
    "$0" stop
    ;;
  *) echo "Usage: ${0##*/} start|stop|watch" >&2; exit 2 ;;
esac
