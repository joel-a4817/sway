#!/usr/bin/env python3
"""Add or remove one HpCF device in rebuild-camilla.py and Media Control."""

import ast
import os
import re
import shutil
import subprocess
import sys
import termios
import textwrap
import tty
from datetime import datetime
from pathlib import Path

PYCACHE = Path("/home/joel/.config/sway/scripts/updaters/__pycache__")
HOME = Path('/home/joel')
SCRIPT_DIR = HOME / '.config/sway/scripts/updaters'
AUDIO_FILTERS = HOME / 'Documents/prefs/audio-filters'
REBUILD_SCRIPT = SCRIPT_DIR / 'rebuild-camilla.py'
MEDIA_CONTROL = HOME / '.config/sway/scripts/media-control.sh'
SERVER_SCRIPT = HOME / '.config/sway/scripts/network/camilladsp-server-sonobus.py'
BACKUP_DIR = HOME / '.local/state/sway/audio-device-updater/backups'
TERMINALS = (
    ('foot', '-T', 'Manage Audio Device'),
    ('kitty', '--title', 'Manage Audio Device'),
    ('alacritty', '--title', 'Manage Audio Device', '-e'),
)


def print_row(number, label, status=''):
    prefix = (f'[{status}] ' if status else '') + f'[{number}] '
    width = max(1, shutil.get_terminal_size((80, 24)).columns - len(prefix))
    lines = textwrap.wrap(label, width=width, break_long_words=True,
                          break_on_hyphens=False) or ['']
    print(prefix + lines[0])
    for line in lines[1:]:
        print(' ' * len(prefix) + line)


def choose(title, choices):
    print(f'\n{title}')
    print()
    print_row(0, 'Exit without saving')
    for number, label in enumerate(choices, 1):
        print_row(number, label)
    print()
    while True:
        answer = input('Select: ').strip()
        if answer == '0':raise KeyboardInterrupt
        if answer.isdecimal() and 1 <= int(answer) <= len(choices):
            return int(answer) - 1
        print('Select an available number.')


def ask_text(label, default=''):
    suffix = f' [{default}]' if default else ''
    while True:
        value = input(f'{label}{suffix} (0 to exit): ').strip()
        if value == '0':raise KeyboardInterrupt
        value = value or default
        if value and '\n' not in value and '\r' not in value:
            return value
        print(f'{label} is required.')


def python_string(value):
    return repr(str(value))


def load_hpcfs(text):
    tree = ast.parse(text)
    node = next((item for item in tree.body if isinstance(item, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'HPCFS'
                         for target in item.targets)), None)
    if node is None:
        raise RuntimeError('HPCFS tuple was not found in rebuild-camilla.py')
    entries = ast.literal_eval(node.value)
    if not isinstance(entries, tuple) or not all(isinstance(item, dict) for item in entries):
        raise RuntimeError('HPCFS must be a tuple of dictionaries')
    return node, list(entries)


def render_hpcfs(entries):
    lines = ['HPCFS = (']
    for item in entries:
        lines.extend([
            '    {',
            f"        'file': {python_string(item['file'])},",
            f"        'label': {python_string(item['label'])},",
            f"        'kind': {python_string(item['kind'])},",
            '    },',
        ])
    lines.append(')')
    return '\n'.join(lines)


def add_hpcf(text, entry):
    node, entries = load_hpcfs(text)
    wanted_file = os.path.normcase(os.path.realpath(entry['resolved']))
    if any(str(item.get('label', '')).casefold() == entry['label'].casefold()
           for item in entries):
        raise RuntimeError('An HpCF with this device label already exists')
    for item in entries:
        raw = Path(str(item.get('file', ''))).expanduser()
        existing = raw if raw.is_absolute() else AUDIO_FILTERS / raw
        if os.path.normcase(os.path.realpath(existing)) == wanted_file:
            raise RuntimeError('This HpCF file is already configured')
    entries.append({key: entry[key] for key in ('file', 'label', 'kind')})
    lines = text.splitlines(keepends=True)
    replacement = render_hpcfs(entries) + '\n'
    lines[node.lineno - 1:node.end_lineno] = [replacement]
    result = ''.join(lines)
    ast.parse(result)
    return result


def remove_hpcf(text, index):
    node, entries = load_hpcfs(text)
    if not 0 <= index < len(entries):raise RuntimeError('Selected HpCF no longer exists')
    removed = entries.pop(index)
    lines = text.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [render_hpcfs(entries) + '\n']
    result = ''.join(lines);ast.parse(result)
    return result, removed


def remove_exclusion(text, term):
    pattern = re.compile(r'^LEFT_OUT_BLUETOOTH_DEVICES=\(([^\n]*)\)$', re.M)
    match = pattern.search(text)
    if not match:raise RuntimeError('LEFT_OUT_BLUETOOTH_DEVICES was not found in media-control.sh')
    values = [value for value in shell_words(match.group(1)) if value.casefold() != term.casefold()]
    replacement = 'LEFT_OUT_BLUETOOTH_DEVICES=(' + ' '.join(shell_quote(value) for value in values) + ')'
    return text[:match.start()] + replacement + text[match.end():]


def shell_words(body):
    return re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', body)


def shell_quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`') + '"'


def add_exclusion(text, term):
    pattern = re.compile(r'^LEFT_OUT_BLUETOOTH_DEVICES=\(([^\n]*)\)$', re.M)
    match = pattern.search(text)
    if not match:
        raise RuntimeError('LEFT_OUT_BLUETOOTH_DEVICES was not found in media-control.sh')
    values = shell_words(match.group(1))
    if term.casefold() not in {value.casefold() for value in values}:
        values.append(term)
    replacement = 'LEFT_OUT_BLUETOOTH_DEVICES=(' + ' '.join(shell_quote(value) for value in values) + ')'
    return text[:match.start()] + replacement + text[match.end():]


def validate(rebuild_text, media_text, directory):
    rebuild = directory / 'rebuild-camilla.py'
    media = directory / 'media-control.sh'
    rebuild.write_text(rebuild_text)
    media.write_text(media_text)
    subprocess.run([sys.executable, '-m', 'py_compile', str(rebuild)], check=True)
    subprocess.run(['bash', '-n', str(media)], check=True)
    if SERVER_SCRIPT.is_file():
        subprocess.run([sys.executable, '-m', 'py_compile', str(SERVER_SCRIPT)], check=True)
        required = ('--dsp-profiles', '--dsp-filter-info', '--selected-filter', '--select-filter',
                    '--laptop-laptop-boundary')
        server_text = SERVER_SCRIPT.read_text()
        missing = [flag for flag in required if flag not in server_text]
        if missing:raise RuntimeError('Paired server is missing required interface(s): '+', '.join(missing))


def atomic_write(path, text):
    temporary = path.with_name('.' + path.name + '.new')
    temporary.write_text(text)
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)


def backup(paths):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = BACKUP_DIR / stamp
    target.mkdir(parents=True, exist_ok=False)
    for path in paths:
        shutil.copy2(path, target / path.name)
    return target


def relaunch_in_terminal():
    for command in TERMINALS:
        executable = shutil.which(command[0])
        if executable:
            subprocess.Popen([executable, *command[1:], sys.executable, str(Path(__file__).resolve())])
            return
    raise RuntimeError('No supported terminal found: foot, kitty or alacritty')


def press_any_key():
    print('\nPress any key to close...', end='', flush=True)
    if sys.stdin.isatty():
        descriptor = sys.stdin.fileno()
        settings = termios.tcgetattr(descriptor)
        try:
            tty.setraw(descriptor)
            os.read(descriptor, 1)
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, settings)
    print()


def save_changes(rebuild_before, rebuild_after, media_before, media_after):
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        validate(rebuild_after, media_after, Path(temporary))
    changed = [REBUILD_SCRIPT]
    if media_after != media_before:changed.append(MEDIA_CONTROL)
    backup_path = backup(changed)
    try:
        atomic_write(REBUILD_SCRIPT, rebuild_after)
        if media_after != media_before:atomic_write(MEDIA_CONTROL, media_after)
        subprocess.run([sys.executable, '-m', 'py_compile', str(REBUILD_SCRIPT)], check=True)
        subprocess.run(['bash', '-n', str(MEDIA_CONTROL)], check=True)
    except Exception:
        for item in changed:shutil.copy2(backup_path / item.name, item)
        raise
    return backup_path


def offer_rebuild():
    if choose('Regenerate CamillaDSP profiles now?', ('No', 'Yes')) == 1:
        result = subprocess.run([sys.executable, str(REBUILD_SCRIPT)])
        if result.returncode:raise RuntimeError(f'Profile rebuild failed with status {result.returncode}; device configuration was kept')
        print('CamillaDSP profiles regenerated.')
    else:print('Profile regeneration skipped.')


def configured_path(item):
    raw = Path(str(item.get('file', ''))).expanduser()
    return (raw if raw.is_absolute() else AUDIO_FILTERS / raw).resolve()


def bluetooth_terms(media_text, label):
    match = re.search(r'^LEFT_OUT_BLUETOOTH_DEVICES=\(([^\n]*)\)$', media_text, re.M)
    if match is None:raise RuntimeError('LEFT_OUT_BLUETOOTH_DEVICES was not found in media-control.sh')
    return [value for value in shell_words(match.group(1)) if value.casefold() in label.casefold()]


def ask_edit(field, current):
    print(f'\n{field}')
    print(f'Current: {current}')
    return choose('Edit this value?', ('No', 'Yes')) == 1


def replace_hpcf(text, index, entry):
    node, entries = load_hpcfs(text)
    if not 0 <= index < len(entries):raise RuntimeError('Selected HpCF no longer exists')
    wanted_file = os.path.normcase(os.path.realpath(entry['resolved']))
    for position, item in enumerate(entries):
        if position == index:continue
        if str(item.get('label', '')).casefold() == entry['label'].casefold():raise RuntimeError('An HpCF with this device label already exists')
        if os.path.normcase(os.path.realpath(configured_path(item))) == wanted_file:raise RuntimeError('This HpCF file is already configured')
    entries[index] = {key: entry[key] for key in ('file', 'label', 'kind')}
    lines = text.splitlines(keepends=True);lines[node.lineno - 1:node.end_lineno] = [render_hpcfs(entries) + '\n']
    result = ''.join(lines);ast.parse(result);return result


def edit_device(rebuild_before, media_before):
    _, entries = load_hpcfs(rebuild_before)
    if not entries:raise RuntimeError('No configured audio devices to edit')
    labels = [f"{item.get('label','Unnamed')} ({item.get('kind','?')}) | {item.get('file','')}" for item in entries]
    index = choose('Edit audio device', labels);original = entries[index]
    kind = str(original.get('kind', '')).upper();label = str(original.get('label', '')).strip();path = configured_path(original)
    terms = bluetooth_terms(media_before, label);bluetooth = bool(terms);exclusion = terms[0] if terms else label
    if ask_edit('IE or OE', kind):kind = ('IE', 'OE')[choose('Audio device | Type', ('In ear (IE)', 'Over ear (OE)'))]
    if ask_edit('Bluetooth', 'Yes' if bluetooth else 'No'):bluetooth = bool(choose('Audio device | Connection', ('Not Bluetooth', 'Bluetooth')))
    if ask_edit('Device name used in CamillaDSP profiles', label):label = ask_text('Device name used in CamillaDSP profiles', label)
    if ask_edit('HpCF filename or absolute path', str(path)):
        raw_path = ask_text('HpCF filename or absolute path', str(path));candidate = Path(raw_path).expanduser();path = (candidate if candidate.is_absolute() else AUDIO_FILTERS / candidate).resolve()
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold() != '.wav':raise RuntimeError('HpCF must be a WAV file')
    if bluetooth and ask_edit('Bluetooth picker exclusion substring', exclusion):exclusion = ask_text('Bluetooth picker exclusion substring', exclusion)
    print('\nAudio device | Edit review');print_row(1, f'Type: {kind}');print_row(2, f'Bluetooth: {"Yes" if bluetooth else "No"}');print_row(3, f'Device name: {label}');print_row(4, f'HpCF: {path}')
    if bluetooth:print_row(5, f'Hidden picker substring: {exclusion}')
    if choose('Save these edits?', ('No', 'Yes')) == 0:print('No files changed.');return
    stored_path = path.name if path.parent == AUDIO_FILTERS.resolve() else str(path)
    rebuild_after = replace_hpcf(rebuild_before, index, {'file': stored_path, 'resolved': str(path), 'label': label, 'kind': kind})
    media_after = media_before
    for term in terms:media_after = remove_exclusion(media_after, term)
    if bluetooth:media_after = add_exclusion(media_after, exclusion)
    backup_path = save_changes(rebuild_before, rebuild_after, media_before, media_after)
    print(f'\nEdited {label} ({kind}).\nBackup: {backup_path}');offer_rebuild()


def add_device(rebuild_before, media_before):
    kind = ('IE', 'OE')[choose('Audio device | Type', ('In ear (IE)', 'Over ear (OE)'))]
    bluetooth = bool(choose('Audio device | Connection', ('Not Bluetooth', 'Bluetooth')))
    label = ask_text('Device name used in CamillaDSP profiles')
    raw_path = ask_text('HpCF filename or absolute path')
    path = Path(raw_path).expanduser()
    if not path.is_absolute():path = AUDIO_FILTERS / path
    path = path.resolve()
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold() != '.wav':raise RuntimeError('HpCF must be a WAV file')
    stored_path = path.name if path.parent == AUDIO_FILTERS.resolve() else str(path)
    exclusion = ask_text('Bluetooth picker exclusion substring', label) if bluetooth else ''
    print('\nAudio device | Review')
    for number,value in enumerate((f'Type: {kind}',f'Bluetooth: {"Yes" if bluetooth else "No"}',f'Device name: {label}',f'HpCF: {path}'),1):print_row(number,value)
    if bluetooth:print_row(5,f'Hidden picker substring: {exclusion}')
    if choose('Save this device?', ('No', 'Yes')) == 0:print('No files changed.');return
    rebuild_after = add_hpcf(rebuild_before, {'file': stored_path, 'resolved': str(path), 'label': label, 'kind': kind})
    media_after = add_exclusion(media_before, exclusion) if bluetooth else media_before
    backup_path=save_changes(rebuild_before,rebuild_after,media_before,media_after)
    print(f'\nAdded {label} ({kind}).\nBackup: {backup_path}')
    offer_rebuild()


def remove_device(rebuild_before, media_before):
    _,entries=load_hpcfs(rebuild_before)
    if not entries:raise RuntimeError('No configured audio devices to remove')
    labels=[f"{item.get('label','Unnamed')} ({item.get('kind','?')}) | {item.get('file','')}" for item in entries]
    index=choose('Remove audio device',labels);selected=entries[index]
    label=str(selected.get('label') or '')
    exclusion_match=re.search(r'^LEFT_OUT_BLUETOOTH_DEVICES=\(([^\n]*)\)$',media_before,re.M)
    if exclusion_match is None:raise RuntimeError('LEFT_OUT_BLUETOOTH_DEVICES was not found in media-control.sh')
    matching_exclusions=bluetooth_terms(media_before,label)
    print('\nAudio device | Remove review');print_row(1,f"Device: {label}");print_row(2,f"Type: {selected.get('kind','')}");print_row(3,f"HpCF: {selected.get('file','')}")
    if matching_exclusions:print_row(4,'Bluetooth exclusion(s) also removed: '+', '.join(matching_exclusions))
    if choose('Remove this device?', ('No', 'Yes')) == 0:print('No files changed.');return
    rebuild_after,removed=remove_hpcf(rebuild_before,index)
    media_after=media_before
    for exclusion in matching_exclusions:media_after=remove_exclusion(media_after,exclusion)
    backup_path=save_changes(rebuild_before,rebuild_after,media_before,media_after)
    print(f"\nRemoved {removed.get('label','device')}.\nBackup: {backup_path}")
    offer_rebuild()


def main():
    if not REBUILD_SCRIPT.is_file():raise RuntimeError(f'Rebuild script not found: {REBUILD_SCRIPT}')
    if not MEDIA_CONTROL.is_file():raise RuntimeError(f'Media Control not found: {MEDIA_CONTROL}')
    action=choose('Audio device manager',('Add device','Edit device','Remove device'))
    rebuild_before=REBUILD_SCRIPT.read_text();media_before=MEDIA_CONTROL.read_text()
    (add_device,edit_device,remove_device)[action](rebuild_before,media_before)


if __name__ == '__main__':
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        try:
            relaunch_in_terminal()
        except Exception as error:
            print(f'Error: {error}', file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)
    status = 0
    try:
        main()
    except KeyboardInterrupt:
        print('\nCancelled.')
        status = 130
    except Exception as error:
        print(f'\nError: {error}', file=sys.stderr)
        status = 1
    finally:
        shutil.rmtree(PYCACHE, ignore_errors=True)
        press_any_key()
    raise SystemExit(status)
