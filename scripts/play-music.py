#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import termios
import tty
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MUSIC = Path.home() / 'Downloads' / 'Music'
API = 'http://127.0.0.1:8766'
EXTENSIONS = {'.m4a', '.aac', '.mp3', '.flac', '.wav', '.ogg', '.opus'}
TERMINALS = (
    ('foot', '-T', 'Play Music'),
    ('kitty', '--title', 'Play Music'),
    ('alacritty', '--title', 'Play Music', '-e'),
)

def row(number, label):
    print(f'[{number}] {label}')

def choose(title, choices):
    print(f'\n{title}')
    row(0, 'Exit')
    for number, label in enumerate(choices, 1):
        row(number, label)
    while True:
        value = input('Select:').strip()
        if value == '0':
            raise KeyboardInterrupt
        if value.isdecimal() and 1 <= int(value) <= len(choices):
            return int(value) - 1
        print('Select an available number.')

def post(path, payload):
    request = Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get('error')
        except Exception:
            detail = None
        raise RuntimeError(detail or f'Webremote returned HTTP {error.code}') from error
    except URLError as error:
        raise RuntimeError('CamillaDSP webremote is unavailable') from error
    if not result.get('ok'):
        raise RuntimeError(result.get('error') or 'Playback request failed')
    return result

def playlists():
    return sorted(
        (path for path in MUSIC.glob('*.m3u') if not path.stem.endswith('-shuffled')),
        key=lambda path: path.stem.casefold(),
    )

def resolve_song(value):
    value = value.strip()
    if not value or value == '0':
        raise KeyboardInterrupt
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = MUSIC / candidate
    candidate = candidate.resolve()
    if candidate.is_file() and MUSIC.resolve() in candidate.parents and candidate.suffix.casefold() in EXTENSIONS:
        return candidate
    matches = [
        path for path in MUSIC.rglob('*')
        if path.is_file() and path.suffix.casefold() in EXTENSIONS
        and (path.name.casefold() == value.casefold() or path.stem.casefold() == value.casefold())
    ]
    if len(matches) == 1:
        return matches[0].resolve()
    if not matches:
        raise RuntimeError('Song was not found inside ~/Downloads/Music')
    raise RuntimeError('Song name is ambiguous; enter its relative path')

def run_menu():
    if not MUSIC.is_dir():
        raise RuntimeError('Music directory not found: ' + str(MUSIC))
    action = choose('Play music', ('Playlist', 'Individual song'))
    if action == 0:
        available = playlists()
        if not available:
            raise RuntimeError('No .m3u playlists found in ~/Downloads/Music')
        selected = available[choose('Playlist', [path.stem for path in available])]
        mode = choose('Playlist action', ('Play', 'Shuffle'))
        post('/api/play/playlist', {'name': selected.stem, 'shuffle': mode == 1})
        print(f"\nPlaying {'shuffled ' if mode == 1 else ''}playlist: {selected.stem}")
    else:
        print('\nIndividual song')
        print('[0] Exit')
        value = input('Path or name:').strip()
        selected = resolve_song(value)
        post('/api/play/song', {'path': str(selected)})
        print(f'\nPlaying song: {selected.relative_to(MUSIC)}')

def relaunch():
    for command in TERMINALS:
        executable = shutil.which(command[0])
        if executable:
            subprocess.Popen([executable, *command[1:], sys.executable, str(Path(__file__).resolve())])
            return
    raise RuntimeError('No supported terminal found: foot, kitty or alacritty')

def pause():
    if not sys.stdin.isatty():
        return
    print('\nPress any key to close...', end='', flush=True)
    descriptor = sys.stdin.fileno()
    settings = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        os.read(descriptor, 1)
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, settings)
    print()

def main():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        relaunch()
        return
    status = 0
    try:
        run_menu()
    except KeyboardInterrupt:
        print('\nCancelled.')
        status = 130
    except Exception as error:
        print(f'\nError: {error}', file=sys.stderr)
        status = 1
    finally:
        pause()
    raise SystemExit(status)

if __name__ == '__main__':
    main()
