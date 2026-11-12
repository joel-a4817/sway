#!/usr/bin/env bash
set -euo pipefail
IPAD_HOST="${IPAD_HOST:-mobile@iPad4817}"
WEBREMOTE_HOST="${WEBREMOTE_HOST:-iPad4817}"
WEBREMOTE_PORT="${WEBREMOTE_PORT:-8765}"
REMOTE_DIR=/var/mobile/webremote
STOPPER="$REMOTE_DIR/stop-webremote.sh"

is_running() {
    curl --silent --fail --connect-timeout 1 --max-time 2 "http://${WEBREMOTE_HOST}:${WEBREMOTE_PORT}/" >/dev/null 2>&1
}
case "${1:-toggle}" in
  --status|status) is_running && { echo running; exit 0; }; echo stopped; exit 1 ;;
  toggle) ;;
  *) echo "Usage: $0 [--status|toggle]" >&2; exit 2 ;;
esac
if is_running; then
    echo 'Stopping iPad Web Remote...'
    ssh "$IPAD_HOST" "$STOPPER"
    echo 'iPad Web Remote stopped.'
    exit 0
fi

echo 'Starting iPad Web Remote...'
ssh "$IPAD_HOST" 'sh -s' <<'REMOTE'
set -eu
DIR=/var/mobile/webremote
"$DIR/stop-webremote.sh"
cd "$DIR"
/var/jb/usr/bin/python3 -m py_compile server.py sonobus-settingsctl.py
: > server.log
nohup /var/jb/usr/bin/python3 /var/mobile/webremote/server.py >>server.log 2>&1 </dev/null &
pid=$!
echo "$pid" > server.pid
sleep 3
if ! kill -0 "$pid" 2>/dev/null; then
    echo 'iPad Web Remote exited during startup.' >&2
    cat server.log >&2
    exit 1
fi
REMOTE
for _ in {1..20}; do
    is_running && { echo "iPad Web Remote running at http://${WEBREMOTE_HOST}:${WEBREMOTE_PORT}/"; exit 0; }
    sleep .25
done
echo 'iPad Web Remote process exists but HTTP did not respond.' >&2
ssh "$IPAD_HOST" 'cat /var/mobile/webremote/server.log 2>/dev/null || true' >&2 || true
exit 1
