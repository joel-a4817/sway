#!/usr/bin/env bash
set -euo pipefail

IPAD_HOST="${IPAD_HOST:-mobile@iPad4817}"
WEBREMOTE_HOST="${WEBREMOTE_HOST:-iPad4817}"
WEBREMOTE_PORT="${WEBREMOTE_PORT:-8765}"
REMOTE_DIR="/var/mobile/webremote"
REMOTE_SERVER="$REMOTE_DIR/server.py"

is_running() {
    curl --silent --fail \
        --connect-timeout 1 --max-time 2 \
        "http://${WEBREMOTE_HOST}:${WEBREMOTE_PORT}/" \
        >/dev/null 2>&1
}

case "${1:-toggle}" in
    --status|status)
        if is_running; then
            printf '%s\n' running
            exit 0
        fi
        printf '%s\n' stopped
        exit 1
        ;;
    toggle) ;;
    *)
        printf 'Usage: %s [--status|toggle]\n' "$0" >&2
        exit 2
        ;;
esac

if is_running; then
    printf '%s\n' 'Stopping iPad Web Remote...'
    ssh "$IPAD_HOST" '
        ps ax -o pid= -o command= |
        while read -r pid command; do
            case "$command" in
                "/var/jb/usr/bin/python3 server.py"*|"/var/jb/usr/bin/python3 /var/mobile/webremote/server.py"*)
                    kill "$pid" 2>/dev/null || true
                    ;;
            esac
        done
    '

    for _ in {1..20}; do
        is_running || {
            printf '%s\n' 'iPad Web Remote stopped.'
            exit 0
        }
        sleep 0.25
    done

    printf '%s\n' 'iPad Web Remote did not stop.' >&2
    exit 1
fi

printf '%s\n' 'Starting iPad Web Remote...'
# iPad uses zsh. &! starts and disowns the process, so closing SSH cannot kill it.
# Startup failure and the server log are returned in this same SSH session.
ssh "$IPAD_HOST" '
    set -e
    cd /var/mobile/webremote

    ps ax -o pid= -o command= |
    while read -r pid command; do
        case "$command" in
            "/var/jb/usr/bin/python3 server.py"*|"/var/jb/usr/bin/python3 /var/mobile/webremote/server.py"*)
                kill "$pid" 2>/dev/null || true
                ;;
        esac
    done

    rm -f /var/mobile/webremote/server.pid
    : > /var/mobile/webremote/server.log

    nohup /var/jb/usr/bin/python3 /var/mobile/webremote/server.py \
        > /var/mobile/webremote/server.log 2>&1 \
        < /dev/null &!

    pid=$!
    echo "$pid" > /var/mobile/webremote/server.pid
    sleep 2

    if ! kill -0 "$pid" 2>/dev/null; then
        echo "iPad Web Remote exited during startup." >&2
        tail -80 /var/mobile/webremote/server.log >&2 || true
        exit 1
    fi
'

for _ in {1..40}; do
    if is_running; then
        printf 'iPad Web Remote started: http://%s:%s\n' \
            "$WEBREMOTE_HOST" "$WEBREMOTE_PORT"
        exit 0
    fi
    sleep 0.25
done

# Do not open a second SSH password prompt. The process survived startup, but
# the HTTP endpoint was not reachable from the laptop.
printf 'iPad Web Remote process started, but http://%s:%s did not respond.\n' \
    "$WEBREMOTE_HOST" "$WEBREMOTE_PORT" >&2
exit 1
