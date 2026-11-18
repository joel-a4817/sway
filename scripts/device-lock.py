#!/usr/bin/env python3
from pathlib import Path
import fcntl, json, os, select, signal, subprocess, sys, time

HOME = Path.home()
STATE = HOME / '.local/state/sway/audio-runtime'
DISPLAY_FILE = STATE / 'display-output.json'
AWAY_FILE = STATE / 'away-state.json'
COMBINED_FILE = STATE / 'away-display.lock'
IMAGE = HOME / '.config/sway/mcqueen.jpeg'


def env():
    result = os.environ.copy()
    runtime = f'/run/user/{os.getuid()}'
    result.setdefault('XDG_RUNTIME_DIR', runtime)
    result.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')
    return result


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    os.replace(temporary, path)


def command(args, timeout=8):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, env=env())


def sway_outputs():
    result = command(['swaymsg', '-r', '-t', 'get_outputs'], 5)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Sway outputs unavailable')
    outputs = json.loads(result.stdout)
    if not isinstance(outputs, list):
        raise RuntimeError('Invalid Sway output list')
    return outputs


def reported_power(output):
    """Return True/False when Sway reports power state, otherwise None."""
    power = output.get('power')
    if isinstance(power, bool):
        return power
    dpms = output.get('dpms')
    if isinstance(dpms, bool):
        return dpms
    if isinstance(dpms, str):
        value = dpms.lower()
        if value in ('on', 'off'):
            return value == 'on'
    return None


def saved_display_outputs():
    saved = read_json(DISPLAY_FILE, {})
    if not isinstance(saved, dict):
        saved = {}
    outputs = saved.get('outputs')
    if not isinstance(outputs, list):
        legacy = saved.get('output')
        outputs = [legacy] if isinstance(legacy, str) and legacy else []
    return [name for name in outputs if isinstance(name, str) and name]


def display_state():
    current = sway_outputs()
    connected = {
        row.get('name'): row for row in current
        if isinstance(row.get('name'), str) and row.get('name')
    }
    saved = [name for name in saved_display_outputs() if name in connected]
    detected_off = [
        name for name, row in connected.items()
        if row.get('active') and reported_power(row) is False
    ]
    # Live compositor state is authoritative where available. Saved state is
    # used only for outputs whose Sway payload has no power/DPMS field, so a
    # stale file cannot force an unnecessary first toggle.
    fallback_saved = [
        name for name in saved if reported_power(connected[name]) is None
    ]
    outputs = list(dict.fromkeys(detected_off + fallback_saved))
    return {'off': bool(outputs), 'outputs': outputs,
            'detectedOutputs': detected_off, 'savedOutputs': saved}


def set_output_power(name, on):
    result = command(['swaymsg', '-r', 'output ' + json.dumps(name) + ' power ' + ('on' if on else 'off')], 5)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f'Could not turn {name} ' + ('on' if on else 'off'))
    replies = json.loads(result.stdout)
    if not isinstance(replies, list) or not replies or not all(row.get('success') for row in replies):
        raise RuntimeError(f'Sway rejected power change for {name}: {replies}')


def display_power(on):
    saved = display_state()
    if on:
        names = saved['outputs']
        if not names:
            DISPLAY_FILE.unlink(missing_ok=True)
            return display_state()
        failures = []
        for name in names:
            try:
                set_output_power(name, True)
            except Exception as error:
                failures.append(f'{name}: {error}')
        if failures:
            raise RuntimeError('Could not restore display power: ' + '; '.join(failures))
        DISPLAY_FILE.unlink(missing_ok=True)
    else:
        # Re-read live output state immediately before changing it. Only outputs
        # that are active and not already reported off are part of this toggle.
        rows = sway_outputs()
        names = [
            row['name'] for row in rows
            if row.get('active') and row.get('name')
            and reported_power(row) is not False
        ]
        if not names:
            raise RuntimeError('No powered-on active Sway outputs')
        powered_off = []
        try:
            for name in names:
                set_output_power(name, False)
                powered_off.append(name)
        except Exception:
            for name in reversed(powered_off):
                try:
                    set_output_power(name, True)
                except Exception:
                    pass
            raise
        write_json(DISPLAY_FILE, {'outputs': powered_off})
    return display_state()


def identity(pid):
    try:
        rest = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
        if rest[0] == 'Z':
            return None
        return int(rest[1]), int(rest[19])
    except (OSError, ValueError, IndexError):
        return None


def away_record():
    data = read_json(AWAY_FILE, {})
    return data if isinstance(data, dict) else {}


def locker_pid():
    data = away_record()
    try:
        pid = int(data.get('lockerPid'))
        start = int(data.get('lockerStart'))
        found = identity(pid)
        if found and found[1] == start and Path(f'/proc/{pid}/comm').read_text().strip() == 'swaylock':
            return pid
    except (OSError, ValueError, TypeError):
        pass
    return None


def thaw_legacy():
    for record in reversed(away_record().get('processes', [])):
        try:
            pid, start = record
            found = identity(pid)
            if found and found[1] == int(start):
                os.kill(pid, signal.SIGCONT)
        except (OSError, ValueError, TypeError, PermissionError):
            pass


def start_lock():
    if locker_pid():
        return
    if away_record().get('lockerPid'):
        AWAY_FILE.unlink(missing_ok=True)
    thaw_legacy()
    if not IMAGE.is_file():
        raise RuntimeError('Swaylock image not found: ' + str(IMAGE))
    read_fd, write_fd = os.pipe()
    try:
        process = subprocess.Popen(
            ['swaylock', '-F', '-i', str(IMAGE), '-R', str(write_fd)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(write_fd,), start_new_session=True, env=env())
        os.close(write_fd)
        write_fd = -1
        ready, _, _ = select.select([read_fd], [], [], 8)
        if not ready or os.read(read_fd, 1) != b'\n':
            detail = f'swaylock exit status {process.poll()}' if process.poll() is not None else 'readiness timeout'
            if process.poll() is None:
                os.kill(process.pid, signal.SIGUSR1)
            raise RuntimeError('Swaylock did not confirm session lock: ' + detail)
        found = identity(process.pid)
        if not found:
            raise RuntimeError('Swaylock exited before display off')
        write_json(AWAY_FILE, {'active': True, 'lockerPid': process.pid, 'lockerStart': found[1]})
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def stop_lock():
    pid = locker_pid()
    if pid:
        os.kill(pid, signal.SIGUSR1)
        deadline = time.monotonic() + 5
        while locker_pid() and time.monotonic() < deadline:
            time.sleep(.05)
        if locker_pid():
            raise RuntimeError('Swaylock did not unlock; displays are on for recovery')
    thaw_legacy()
    AWAY_FILE.unlink(missing_ok=True)


def state():
    display = display_state()
    locked = locker_pid() is not None
    return {
        'active': locked or display['off'],
        'away': locked,
        'displayOff': display['off'],
        'displayOutputs': display['outputs'],
        'pausedWindows': 0,
    }


def toggle():
    STATE.mkdir(parents=True, exist_ok=True)
    with COMBINED_FILE.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = state()
        if before['active']:
            # Toggle ON as one operation: restore every display that this script
            # powered off, then dismiss the lock. This works for either the
            # laptop panel alone or the laptop panel plus external outputs.
            if before['displayOff']:
                display_power(True)
            if before['away']:
                stop_lock()
        else:
            # Toggle OFF as one operation: lock first, then power off every
            # currently active output (internal and external).
            start_lock()
            try:
                display_power(False)
            except Exception:
                stop_lock()
                raise
        return state()


def main():
    try:
        result = state() if '--status' in sys.argv[1:] else toggle()
        print(json.dumps(result, separators=(',', ':')))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
