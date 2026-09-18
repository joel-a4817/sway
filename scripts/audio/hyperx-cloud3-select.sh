#!/usr/bin/env bash
set -u

PATH="/run/wrappers/bin:$HOME/.nix-profile/bin:/etc/profiles/per-user/$USER/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin"
export PATH

AUDIO_DIR="$HOME/.local/state/sway/audio"
LOCK_DIR="$AUDIO_DIR/ash-output-selector-lock.d"
COOLDOWN_FILE="$AUDIO_DIR/ash-output-selector-last-run-ms"
LOG_FILE="$AUDIO_DIR/hyperx-cloud3.log"

COOLDOWN_MILLISECONDS=2000
TARGET_SINK="cloud3_ash_96khz"

mkdir -p "$AUDIO_DIR"
chmod 700 "$AUDIO_DIR" 2>/dev/null || true

exec >>"$LOG_FILE" 2>&1

printf '\n[%s] control=cloud3-ash-select pid=%s\n' \
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

    if [[ "$old_pid" =~ ^[0-9]+$ ]] &&
       kill -0 "$old_pid" 2>/dev/null; then
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
    echo "ignored: shared ASH output-selector cooldown"
    exit 0
fi

temporary_cooldown="${COOLDOWN_FILE}.tmp.$$"
printf '%s\n' "$now_ms" >"$temporary_cooldown"
mv -f "$temporary_cooldown" "$COOLDOWN_FILE"

if ! pactl list short sinks 2>/dev/null |
     awk '{ print $2 }' |
     grep -Fqx "$TARGET_SINK"; then
    echo "ERROR: configured ASH sink is unavailable: $TARGET_SINK"
    exit 1
fi

echo "action=select-cloud3-ash"
echo "sink=$TARGET_SINK"

swaymsg workspace 10

pactl set-default-sink "$TARGET_SINK" || {
    echo "ERROR: unable to set default sink"
    exit 1
}

pactl set-sink-mute "$TARGET_SINK" 0 || true
pactl set-sink-volume "$TARGET_SINK" 100% || true

while read -r input_id; do
    [[ -n "$input_id" ]] || continue

    pactl move-sink-input "$input_id" "$TARGET_SINK" || {
        echo "WARNING: unable to move sink input $input_id"
    }
done < <(
    pactl list short sink-inputs 2>/dev/null |
        awk '{ print $1 }'
)

echo "result=cloud3-ash-selected"
echo "sink=$TARGET_SINK"
