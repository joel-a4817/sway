#!/usr/bin/env bash
set -u

HOME_DIR="/home/joel"
AUDIO_DIR="$HOME_DIR/.local/state/sway/audio"
DISPLAY_OFF_STATE_FILE="$AUDIO_DIR/displays-powered-off"
LOCK_DIR="$AUDIO_DIR/suspend-display-toggle-lock.d"
LOG_FILE="$AUDIO_DIR/suspend-display-toggle.log"
COOLDOWN_FILE="$AUDIO_DIR/suspend-display-toggle-last-run-ms"
COOLDOWN_MILLISECONDS=2000

mkdir -p "$AUDIO_DIR"
chmod 700 "$AUDIO_DIR" 2>/dev/null || true
exec >>"$LOG_FILE" 2>&1

printf '\n[%s] invoked pid=%s\n' \
    "$(date -Iseconds 2>/dev/null || date)" \
    "$$"

acquire_lock() {
    local old_pid=""

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" >"$LOCK_DIR/pid"
        return 0
    fi

    if [[ -r "$LOCK_DIR/pid" ]]; then
        read -r old_pid <"$LOCK_DIR/pid" || old_pid=""
    fi

    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
        echo "ignored: already running as PID $old_pid"
        return 1
    fi

    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || {
        echo "ERROR: unable to acquire lock"
        return 1
    }

    printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
    rm -rf "$LOCK_DIR"
}

acquire_lock || exit 0
trap release_lock EXIT INT TERM HUP

now_ms="$(date +%s%3N)"
last_ms=0

if [[ -r "$COOLDOWN_FILE" ]]; then
    read -r last_ms <"$COOLDOWN_FILE" || last_ms=0
fi

[[ "$now_ms" =~ ^[0-9]+$ ]] || {
    echo "ERROR: unable to obtain current time in milliseconds"
    exit 1
}
[[ "$last_ms" =~ ^[0-9]+$ ]] || last_ms=0

if (( now_ms - last_ms < COOLDOWN_MILLISECONDS )); then
    echo "ignored: suspend-display cooldown"
    exit 0
fi

temporary_cooldown="${COOLDOWN_FILE}.tmp.$$"
printf '%s\n' "$now_ms" >"$temporary_cooldown"
mv -f "$temporary_cooldown" "$COOLDOWN_FILE"

mark_displays_off() {
    local temporary="${DISPLAY_OFF_STATE_FILE}.tmp.$$"
    printf '%s\n' "$(date -Iseconds 2>/dev/null || date)" >"$temporary"
    mv -f "$temporary" "$DISPLAY_OFF_STATE_FILE"
}

power_displays_off() {
    swaymsg "output * power off" >/dev/null || {
        echo "ERROR: Sway rejected display power-off"
        return 1
    }
    mark_displays_off
}

power_displays_on() {
    swaymsg "output * power on" >/dev/null || {
        echo "ERROR: Sway rejected display power-on"
        return 1
    }
    rm -f "$DISPLAY_OFF_STATE_FILE"
}

# First press: turn all displays off.
if [[ ! -f "$DISPLAY_OFF_STATE_FILE" ]]; then
    echo "state=display-on"
    echo "action=display-off"
    power_displays_off || exit 1
    echo "result=display-off"
    exit 0
fi

# Second press: turn displays on, clear the marker, and suspend.
echo "state=display-off"
echo "action=display-on-suspend"
power_displays_on || exit 1
sync

echo "suspending"
release_lock
trap - EXIT INT TERM HUP
systemctl suspend
