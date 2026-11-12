#!/usr/bin/env bash
set -u

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SELF_PID=$$

pause_all_playback() {
    local socket
    # First pause all MPRIS players. mpv is included when mpv-mpris is active.
    if command -v playerctl >/dev/null 2>&1; then
        playerctl --all-players pause >/dev/null 2>&1 || true
    fi
    # Also pause plain mpv instances that expose their native IPC socket but
    # are not registered over MPRIS. Never signal-stop mpv: that would freeze
    # IPC and make clean recovery impossible.
    command -v python3 >/dev/null 2>&1 || return 0
    python3 - <<'PYPAUSE' >/dev/null 2>&1 || true
import glob,json,os,socket
paths={'/tmp/mpv-socket','/tmp/mpvsocket','/tmp/mpv.sock'}
runtime=os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
paths.update(glob.glob(runtime+'/mpv*'));paths.update(glob.glob(runtime+'/media-control-mpv/*.sock'))
try:
    for line in open('/proc/net/unix',encoding='utf-8',errors='ignore'):
        fields=line.split()
        if len(fields)>=8 and 'mpv' in fields[-1].lower() and fields[-1].startswith('/'):
            paths.add(fields[-1])
except OSError:
    pass
payload=(json.dumps({'command':['set_property','pause',True]})+'\n').encode()
for path in paths:
    try:
        client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);client.settimeout(.25)
        client.connect(path);client.sendall(payload);client.close()
    except OSError:
        pass
PYPAUSE
}
# Media Control is a paused configuration surface from the moment it opens.
pause_all_playback

is_previous_media_control() {
    local pid="$1" comm arg
    [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$SELF_PID" && -r "/proc/$pid/cmdline" ]] || return 1
    [[ "$(stat -c %u "/proc/$pid" 2>/dev/null)" == "$(id -u)" ]] || return 1
    comm="$(cat "/proc/$pid/comm" 2>/dev/null)"
    [[ "$comm" == bash ]] || return 1
    mapfile -d '' -t argv <"/proc/$pid/cmdline" 2>/dev/null || return 1
    arg="${argv[1]:-}"
    [[ -n "$arg" ]] || return 1
    [[ "$(readlink -f -- "$arg" 2>/dev/null)" == "$SCRIPT_PATH" ]]
}

replace_previous_media_control() {
    local entry pid deadline
    local -a targets=()
    for entry in /proc/[0-9]*; do
        pid="${entry##*/}"
        is_previous_media_control "$pid" && targets+=("$pid")
    done
    for pid in "${targets[@]}"; do
        # USR2 lets updated copies run their cleanup trap; TERM supports older copies.
        kill -USR2 "$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    done
    deadline=$((SECONDS+10))
    while ((${#targets[@]})) && ((SECONDS<deadline)); do
        local -a alive=()
        for pid in "${targets[@]}"; do is_previous_media_control "$pid" && alive+=("$pid"); done
        targets=("${alive[@]}")
        ((${#targets[@]})) && sleep 0.05
    done
    for pid in "${targets[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done
    deadline=$((SECONDS+2))
    while ((${#targets[@]})) && ((SECONDS<deadline)); do
        local -a alive=()
        for pid in "${targets[@]}"; do is_previous_media_control "$pid" && alive+=("$pid"); done
        targets=("${alive[@]}")
        ((${#targets[@]})) && sleep 0.05
    done
    ((${#targets[@]}==0)) || { echo "Previous Media Control did not exit: ${targets[*]}" >&2; exit 1; }
}

# Replace an existing picker before sourcing a second session/lock owner.
replace_previous_media_control

BACKEND_DIR="${HOME:?HOME is required}/.config/sway/scripts/audio-backends"
for part in 10_session.sh 20_menu_helpers.sh 30_camera.sh 40_main_menu.sh; do
    [[ -r "$BACKEND_DIR/$part" ]] || { echo "Missing audio backend: $BACKEND_DIR/$part" >&2; exit 1; }
    source "$BACKEND_DIR/$part"
done
