#!/usr/bin/env bash
set -u

ACTION="toggle"

PATH="/run/wrappers/bin:/home/joel/.nix-profile/bin:/etc/profiles/per-user/joel/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin"
export PATH

HOME_DIR="/home/joel"
STATE_DIR="$HOME_DIR/.local/state/audio-suspend-toggle"

HOTSPOT_CONNECTION="AirPlay Direct"
PREVIOUS_WIFI_FILE="$STATE_DIR/previous-wifi-connection"

LOCK_DIR="$STATE_DIR/hotspot-toggle-lock.d"
ACTION_LOG="$STATE_DIR/audio-toggle.log"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

exec >>"$ACTION_LOG" 2>&1

printf '\n[%s] control=hotspot-toggle pid=%s\n' \
    "$(date -Iseconds 2>/dev/null || date)" \
    "$$"

acquire_lock() {
    local pid=""

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" >"$LOCK_DIR/pid"
        return 0
    fi

    if [[ -r "$LOCK_DIR/pid" ]]; then
        read -r pid <"$LOCK_DIR/pid" || pid=""
    fi

    if [[ "$pid" =~ ^[0-9]+$ ]] &&
       kill -0 "$pid" 2>/dev/null; then
        echo "ignored: already running as PID $pid"
        return 1
    fi

    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || return 1
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
    rm -rf "$LOCK_DIR"
}

acquire_lock || exit 0
trap release_lock EXIT INT TERM HUP

COOLDOWN_SECONDS=2
COOLDOWN_FILE="$STATE_DIR/audio-controls-last-run"

now="$(date +%s)"
last=0

if [[ -r "$COOLDOWN_FILE" ]]; then
    read -r last <"$COOLDOWN_FILE" || last=0
fi

[[ "$last" =~ ^[0-9]+$ ]] || last=0

if (( now - last < COOLDOWN_SECONDS )); then
    echo "ignored: shared audio-controls cooldown"
    exit 0
fi

temporary_cooldown="${COOLDOWN_FILE}.tmp.$$"

printf '%s\n' "$now" >"$temporary_cooldown"

mv -f \
    "$temporary_cooldown" \
    "$COOLDOWN_FILE"

connection_active() {
    local connection="$1"

    nmcli \
        -t \
        -f NAME \
        connection show --active \
        2>/dev/null |
    grep -Fqx "$connection"
}

active_wifi_connection() {
    nmcli \
        -t \
        -f NAME,TYPE \
        connection show --active \
        2>/dev/null |
    awk -F: -v hotspot="$HOTSPOT_CONNECTION" \
        '$2 == "802-11-wireless" && $1 != hotspot { print $1; exit }'
}

wifi_device() {
    nmcli \
        -t \
        -f DEVICE,TYPE \
        device status \
        2>/dev/null |
    awk -F: '$2 == "wifi" { print $1; exit }'
}

# Exact toggle-off decision used by the old script, except hotspot state alone
# is now authoritative because NQPTP and Shairport belong to the profile toggle.
if [[ "$ACTION" == "off" ]] ||
   { [[ "$ACTION" == "toggle" ]] &&
     connection_active "$HOTSPOT_CONNECTION"; }; then

    echo "action=disable-hotspot"

    nmcli \
        --wait 15 \
        connection down \
        "$HOTSPOT_CONNECTION" \
        >/dev/null 2>&1 ||
        true

    if connection_active "$HOTSPOT_CONNECTION"; then
        echo "ERROR: unable to stop $HOTSPOT_CONNECTION"
        exit 1
    fi

    previous_wifi=""

    if [[ -r "$PREVIOUS_WIFI_FILE" ]]; then
        read -r previous_wifi <"$PREVIOUS_WIFI_FILE" || previous_wifi=""
    fi

    if [[ -n "$previous_wifi" ]] &&
       nmcli \
           --wait 30 \
           connection up \
           "$previous_wifi"; then

        echo "result=hotspot-off"
        echo "network=$previous_wifi"
        rm -f "$PREVIOUS_WIFI_FILE"
        exit 0
    fi

    device="$(wifi_device)"

    if [[ -n "$device" ]] &&
       nmcli \
           --wait 30 \
           device connect \
           "$device"; then

        echo "result=hotspot-off"
        echo "network=automatic"
        rm -f "$PREVIOUS_WIFI_FILE"
        exit 0
    fi

    echo "WARNING: hotspot is off, but no saved Wi-Fi network connected"
    echo "result=hotspot-off"
    rm -f "$PREVIOUS_WIFI_FILE"
    exit 0
fi

if [[ "$ACTION" == "on" ]] &&
   connection_active "$HOTSPOT_CONNECTION"; then

    echo "result=already-on"
    echo "network=$HOTSPOT_CONNECTION"
    exit 0
fi

echo "action=enable-hotspot"

previous_wifi="$(active_wifi_connection)"

if [[ -n "$previous_wifi" ]]; then
    temporary_previous="${PREVIOUS_WIFI_FILE}.tmp.$$"
    printf '%s\n' "$previous_wifi" >"$temporary_previous"
    mv -f "$temporary_previous" "$PREVIOUS_WIFI_FILE"

    nmcli \
        --wait 15 \
        connection down \
        "$previous_wifi" \
        >/dev/null 2>&1 ||
        true
else
    rm -f "$PREVIOUS_WIFI_FILE"
fi

if ! connection_active "$HOTSPOT_CONNECTION"; then
    if ! nmcli \
        --wait 30 \
        connection up \
        "$HOTSPOT_CONNECTION"; then

        echo "ERROR: unable to start $HOTSPOT_CONNECTION"

        if [[ -n "$previous_wifi" ]]; then
            nmcli \
                --wait 30 \
                connection up \
                "$previous_wifi" \
                >/dev/null 2>&1 ||
                true
        fi

        exit 1
    fi
fi

echo "result=hotspot-on"
echo "network=$HOTSPOT_CONNECTION"
