#!/usr/bin/env python3
"""Add, edit, or remove HpCF devices used by rebuild-camilla.py."""
import ast
import re
import numpy as np
import soundfile as sf
import os
import shutil
import subprocess
import sys
import tempfile
import termios
import textwrap
import tty
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CONTEXT_FILE = HOME / '.config/sway/scripts/audio-backends/00_context.py'

def load_context_paths():
    defaults = {
        'PROFILES': HOME / 'Documents/prefs/audio-filters',
        'UPDATERS': HOME / '.config/sway/scripts/updaters',
        'AUDIO_DEVICE_BACKUPS': HOME / '.local/state/sway/audio-device-updater/backups',
    }
    if not CONTEXT_FILE.is_file():
        return defaults
    namespace = {'Path': Path}
    wanted = set(defaults) | {'HOME', 'SCRIPTS'}
    tree = ast.parse(CONTEXT_FILE.read_text(encoding='utf-8'), str(CONTEXT_FILE))
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
        if not names or not any(name in wanted for name in names):
            continue
        try:
            code = compile(ast.Module(body=[statement], type_ignores=[]), str(CONTEXT_FILE), 'exec')
            exec(code, namespace, namespace)
        except Exception:
            continue
    return {name: Path(namespace.get(name, default)) for name, default in defaults.items()}

CONTEXT_PATHS = load_context_paths()
AUDIO_FILTERS = CONTEXT_PATHS['PROFILES']
SCRIPT_DIR = CONTEXT_PATHS['UPDATERS']
CONFIG_SCRIPT = Path(__file__).resolve()
BACKUP_DIR = CONTEXT_PATHS['AUDIO_DEVICE_BACKUPS']

HPCFS = (
    {
        'file': 'Apple_EarPods_Ahastyle_Covers_Custom_Average_A+B.wav',
        'label': 'Apple EarPods',
        'kind': 'IE',
        'bluetooth': False,
    },
    {
        'file': 'HyperX_Cloud_III_Average.wav',
        'label': 'HyperX Cloud III',
        'kind': 'OE',
        'bluetooth': False,
    },
    {
        'file': 'CMF_by_Nothing_Buds_Pro_2_Sample_A.wav',
        'label': 'CMF Buds Pro 2',
        'kind': 'IE',
        'bluetooth': True,
    },
)

SAMPLE_RATE = 96000
N_FFT = 65536
CAMILLA_PLAYBACK_DEVICE = "hw:Loopback,0,1"
CAMILLA_PLAYBACK_FORMAT = "S32_LE"
CAMILLA_ROOT = AUDIO_FILTERS
BRIR_ROOT = CAMILLA_ROOT
FOLDER_PATTERN = re.compile(r'^\((\d+)ms-(IE|OE)\)(?:\s+(.+))?$', re.I)
GENERATED_PROFILE_PATTERN = re.compile(r'^\d{2,}-(?:ie|oe)-.+-\d+ms-.+\.ya?ml$', re.I)

def parse_profile_folder(path):
    match = FOLDER_PATTERN.match(path.name)
    if not match:
        return None
    milliseconds, kind, room = match.groups()
    return {
        'path': path, 'folder': path.name, 'milliseconds': milliseconds,
        'kind': kind.upper(), 'room': (room or '').strip(),
    }

def profile_sort_key(profile):
    return (int(profile['milliseconds']), profile['room'].casefold(), profile['kind'])

PROFILES = sorted(
    (profile for path in BRIR_ROOT.iterdir() if path.is_dir()
     if (profile := parse_profile_folder(path)) is not None),
    key=profile_sort_key,
) if BRIR_ROOT.is_dir() else []

# IE and OE versions of the same room share one number. 00 remains filterless.
ROOM_KEYS = sorted(
    {(int(profile['milliseconds']), profile['room'].casefold()) for profile in PROFILES}
)
PROFILE_NUMBERS = {key: index for index, key in enumerate(ROOM_KEYS, 1)}

def slugify(value):
    value = value.lower().replace("'", '')
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-')


def load_required_ash_helpers():
    """
    Self-contained numerical subset previously loaded from ASH Toolset.

    Only the behaviour required by calculate_gain() is implemented.
    level_spectrum_ends() is used with smooth_win=0 in this script.
    """

    class Helpers:
        @staticmethod
        def pad_or_truncate_1d(signal, target_length):
            signal = np.asarray(
                signal,
                dtype=np.float64,
            ).reshape(-1)

            result = np.zeros(
                target_length,
                dtype=np.float64,
            )

            copy_length = min(
                signal.size,
                target_length,
            )

            result[:copy_length] = (
                signal[:copy_length]
            )

            return result

        @staticmethod
        def mag2db(magnitude):
            magnitude = np.asarray(
                magnitude,
                dtype=np.float64,
            )

            floor = np.finfo(
                np.float64
            ).tiny

            return 20.0 * np.log10(
                np.maximum(
                    np.abs(magnitude),
                    floor,
                )
            )

        @staticmethod
        def level_spectrum_ends(
            magnitude,
            low_frequency,
            high_frequency,
            smooth_win=0,
        ):
            magnitude = np.asarray(
                magnitude,
                dtype=np.float64,
            ).copy()

            if magnitude.ndim != 1:
                raise ValueError(
                    "Expected a one-dimensional magnitude spectrum"
                )

            fft_size = (
                magnitude.size - 1
            ) * 2

            frequencies = np.fft.rfftfreq(
                fft_size,
                d=1.0 / SAMPLE_RATE,
            )

            low_index = int(
                np.searchsorted(
                    frequencies,
                    low_frequency,
                    side="left",
                )
            )

            high_index = int(
                np.searchsorted(
                    frequencies,
                    high_frequency,
                    side="right",
                )
            ) - 1

            low_index = max(
                0,
                min(
                    low_index,
                    magnitude.size - 1,
                ),
            )

            high_index = max(
                low_index,
                min(
                    high_index,
                    magnitude.size - 1,
                ),
            )

            # Level the spectrum outside the analysis range to the
            # nearest boundary value. The current generator always
            # calls this function with smooth_win=0.
            magnitude[:low_index] = (
                magnitude[low_index]
            )

            magnitude[high_index + 1:] = (
                magnitude[high_index]
            )

            return magnitude

    return Helpers()


def calculate_gain(brir_file, hpcf_file, hf):
    brir, brir_rate = sf.read(brir_file, always_2d=True, dtype='float64')
    hpcf, hpcf_rate = sf.read(hpcf_file, always_2d=True, dtype='float64')

    if brir_rate != SAMPLE_RATE or hpcf_rate != SAMPLE_RATE:
        raise SystemExit(
            f'Expected both files at {SAMPLE_RATE} Hz:\n'
            f'  BRIR {brir_rate}: {brir_file}\n'
            f'  HpCF {hpcf_rate}: {hpcf_file}'
        )
    if brir.shape[1] != 4:
        raise SystemExit(f'Expected four-channel BRIR, got {brir.shape}: {brir_file}')
    if hpcf.shape[1] != 2:
        raise SystemExit(f'Expected stereo HpCF, got {hpcf.shape}: {hpcf_file}')
    if not np.array_equal(hpcf[:, 0], hpcf[:, 1]):
        difference = float(np.max(np.abs(hpcf[:, 0] - hpcf[:, 1])))
        raise SystemExit(f'HpCF channels differ by up to {difference}: {hpcf_file}')

    def brir_db(signal):
        padded = hf.pad_or_truncate_1d(signal, N_FFT)
        magnitude = np.abs(np.fft.rfft(padded))
        magnitude = hf.level_spectrum_ends(magnitude, 10, 19000, smooth_win=0)
        return hf.mag2db(magnitude)

    left_db = brir_db(brir[:, 0] + brir[:, 2])
    right_db = brir_db(brir[:, 1] + brir[:, 3])
    hpcf_padded = hf.pad_or_truncate_1d(hpcf[:, 0], N_FFT)
    hpcf_db = hf.mag2db(np.abs(np.fft.rfft(hpcf_padded)))

    left_peak = float(np.max(left_db + hpcf_db))
    right_peak = float(np.max(right_db + hpcf_db))
    rounded_peak = round(max(left_peak, right_peak), 1)
    preamp_db = -rounded_peak
    gain = 10.0 ** (preamp_db / 20.0)
    return left_peak, right_peak, rounded_peak, preamp_db, gain


def yaml_string(value):
    """Return a safely quoted YAML string without requiring PyYAML."""
    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    return f'"{value}"'

def make_camilladsp_profile(index, profile, gain, brir, hpcf, device_label):
    room_slug = slugify(profile['room']) or 'room'
    device_slug = slugify(device_label)
    kind = profile['kind'].lower()
    filename = f"{index:02d}-{kind}-{device_slug}-{profile['milliseconds']}ms-{room_slug}.yml"
    output_path = profile['path'] / filename

    gain_text = f"{gain:.17f}"
    brir_text = yaml_string(brir)
    hpcf_text = yaml_string(hpcf)

    title = yaml_string(
        f"{device_label} ASH - {profile['folder']}"
    )

    description = yaml_string(
        "CamillaDSP translation of ASH 2.0 Stereo: "
        "true-stereo BRIR matrix, ASH auto-gain, "
        "then stereo HpCF"
    )

    config = f"""---
title: {title}
description: {description}

devices:
  samplerate: {SAMPLE_RATE}
  chunksize: 1024

  capture:
    type: Alsa
    channels: 2
    device: "hw:Loopback,1,0"
    format: S32_LE

  playback:
    type: Alsa
    channels: 2
    device: "{CAMILLA_PLAYBACK_DEVICE}"
    format: {CAMILLA_PLAYBACK_FORMAT}

mixers:
  split_true_stereo:
    channels:
      in: 2
      out: 4

    labels:
      - "FL-left-ear"
      - "FL-right-ear"
      - "FR-left-ear"
      - "FR-right-ear"

    mapping:
      - dest: 0
        sources:
          - channel: 0
            gain: 1.0
            scale: linear

      - dest: 1
        sources:
          - channel: 0
            gain: 1.0
            scale: linear

      - dest: 2
        sources:
          - channel: 1
            gain: 1.0
            scale: linear

      - dest: 3
        sources:
          - channel: 1
            gain: 1.0
            scale: linear

  sum_to_ears:
    channels:
      in: 4
      out: 2

    labels:
      - "Left ear"
      - "Right ear"

    mapping:
      - dest: 0
        sources:
          - channel: 0
            gain: {gain_text}
            scale: linear

          - channel: 2
            gain: {gain_text}
            scale: linear

      - dest: 1
        sources:
          - channel: 1
            gain: {gain_text}
            scale: linear

          - channel: 3
            gain: {gain_text}
            scale: linear

filters:
  brir_FL_left:
    type: Conv
    parameters:
      type: Wav
      filename: {brir_text}
      channel: 0

  brir_FL_right:
    type: Conv
    parameters:
      type: Wav
      filename: {brir_text}
      channel: 1

  brir_FR_left:
    type: Conv
    parameters:
      type: Wav
      filename: {brir_text}
      channel: 2

  brir_FR_right:
    type: Conv
    parameters:
      type: Wav
      filename: {brir_text}
      channel: 3

  hpcf_left:
    type: Conv
    parameters:
      type: Wav
      filename: {hpcf_text}
      channel: 0

  hpcf_right:
    type: Conv
    parameters:
      type: Wav
      filename: {hpcf_text}
      channel: 1

pipeline:
  - type: Mixer
    name: split_true_stereo

  - type: Filter
    channels: [0]
    names: [brir_FL_left]

  - type: Filter
    channels: [1]
    names: [brir_FL_right]

  - type: Filter
    channels: [2]
    names: [brir_FR_left]

  - type: Filter
    channels: [3]
    names: [brir_FR_right]

  - type: Mixer
    name: sum_to_ears

  - type: Filter
    channels: [0]
    names: [hpcf_left]

  - type: Filter
    channels: [1]
    names: [hpcf_right]
"""

    return output_path, config

def write_filterless_camilladsp_profile():
    path = CAMILLA_ROOT / '00-filterless.yml'
    atomic_write(path, filterless_text())
    return path
def atomic_write(path, text):
    temporary = path.with_name('.' + path.name + '.new')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)

def filterless_text():
    return f'''---
title: "Filterless / Speakers"
description: "Pass-through CamillaDSP profile with no filters"
devices:
  samplerate: {SAMPLE_RATE}
  chunksize: 1024
  capture:
    type: Alsa
    channels: 2
    device: "hw:Loopback,1,0"
    format: S32_LE
  playback:
    type: Alsa
    channels: 2
    device: "{CAMILLA_PLAYBACK_DEVICE}"
    format: {CAMILLA_PLAYBACK_FORMAT}
pipeline: []
'''

def rebuild_all_filters():
    profiles = sorted(
        (profile for path in BRIR_ROOT.iterdir() if path.is_dir()
         if (profile := parse_profile_folder(path)) is not None),
        key=profile_sort_key,
    ) if BRIR_ROOT.is_dir() else []
    room_keys = sorted(
        {(int(profile['milliseconds']), profile['room'].casefold()) for profile in profiles}
    )
    profile_numbers = {key: index for index, key in enumerate(room_keys, 1)}
    if not CAMILLA_ROOT.is_dir() or not profiles:
        raise SystemExit(f'No (NNNNms-IE) or (NNNNms-OE) BRIR folders in {CAMILLA_ROOT}')
    devices = []
    for item in HPCFS:
        kind = str(item.get('kind', '')).upper()
        label = str(item.get('label', '')).strip()
        hpcf = CAMILLA_ROOT / str(item.get('file', ''))
        if kind not in ('IE', 'OE') or not label:
            raise SystemExit(f'Invalid HPCFS entry: {item!r}')
        if not hpcf.is_file():
            raise SystemExit(f'Missing HpCF: {hpcf}')
        devices.append({'kind': kind, 'label': label, 'path': hpcf})
    for profile in profiles:
        brir = profile['path'] / 'BRIR_True_Stereo.wav'
        if not brir.is_file():
            raise SystemExit(f'Missing BRIR: {brir}')
        if not any(device['kind'] == profile['kind'] for device in devices):
            raise SystemExit(f"No {profile['kind']} HpCF configured for {profile['folder']}")
    hf = load_required_ash_helpers()
    generated = []
    for profile in profiles:
        brir = profile['path'] / 'BRIR_True_Stereo.wav'
        room_key = (int(profile['milliseconds']), profile['room'].casefold())
        for device in devices:
            if device['kind'] != profile['kind']:
                continue
            *_, gain = calculate_gain(brir, device['path'], hf)
            generated.append(make_camilladsp_profile(
                profile_numbers[room_key], profile, gain, brir,
                device['path'], device['label'],
            ))
    paths=[path for path,_ in generated]
    if len(paths)!=len(set(paths)):
        raise SystemExit('HpCF labels create duplicate output filenames')
    # Validate every generated profile with the installed CamillaDSP before
    # deleting the currently working profile set.
    camilladsp = Path('/run/current-system/sw/bin/camilladsp')
    if not camilladsp.is_file():
        raise SystemExit(f'Missing CamillaDSP executable: {camilladsp}')
    candidates = [(CAMILLA_ROOT / '00-filterless.yml', filterless_text()), *generated]
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        for number, (path, text) in enumerate(candidates):
            candidate = temporary / f'{number:04d}-{path.name}'
            candidate.write_text(text, encoding='utf-8')
            check = subprocess.run([str(camilladsp), '--check', str(candidate)], text=True,
                                   capture_output=True)
            if check.returncode:
                raise SystemExit(f'Invalid generated profile {path.name}: '+
                                 (check.stderr.strip() or check.stdout.strip()))
    # Remove only files owned by this generator. Preserve unrelated hand-written YAML.
    for path in CAMILLA_ROOT.rglob('*'):
        if not path.is_file():
            continue
        if path.parent == CAMILLA_ROOT and path.name.casefold() == '00-filterless.yml':
            path.unlink()
        elif GENERATED_PROFILE_PATTERN.match(path.name):
            path.unlink()
    output = [write_filterless_camilladsp_profile()]
    for path, text in generated:
        atomic_write(path, text)
        output.append(path)
    assert len(output) == 1 + len(generated)
    print(f'Rebuilt {len(output)} CamillaDSP YAML profiles in {CAMILLA_ROOT}')


TERMINALS = (
    ('foot', '-T', 'Manage Audio Device'),
    ('kitty', '--title', 'Manage Audio Device'),
    ('alacritty', '--title', 'Manage Audio Device', '-e'),
)

def row(number, label):
    prefix=f'[{number}] '
    width=max(1,shutil.get_terminal_size((80,24)).columns-len(prefix))
    lines=textwrap.wrap(str(label),width=width,break_long_words=True,break_on_hyphens=False) or ['']
    print(prefix+lines[0])
    for line in lines[1:]:print(' '*len(prefix)+line)

def choose(title, choices):
    print(f'\n{title}')
    row(0,'Exit without saving')
    for number,label in enumerate(choices,1):row(number,label)
    while True:
        answer=input('Select:').strip()
        if answer=='0':raise KeyboardInterrupt
        if answer.isdecimal() and 1<=int(answer)<=len(choices):return int(answer)-1
        print('Select an available number.')

def ask(label, default=''):
    suffix=f' [{default}]' if default else ''
    while True:
        value=input(f'{label}{suffix} (0 to exit): ').strip()
        if value=='0':raise KeyboardInterrupt
        value=value or default
        if value and '\n' not in value and '\r' not in value:return value
        print(f'{label} is required.')

def ask_edit(field, current):
    return choose(f'{field} current: {current}. Edit?', ('No', 'Yes')) == 1

def load(text):
    tree=ast.parse(text)
    node=next((item for item in tree.body if isinstance(item,ast.Assign) and
               any(isinstance(target,ast.Name) and target.id=='HPCFS' for target in item.targets)),None)
    if node is None:raise RuntimeError('HPCFS tuple was not found in add-audio-device.py')
    entries=ast.literal_eval(node.value)
    if not isinstance(entries,tuple) or not all(isinstance(item,dict) for item in entries):
        raise RuntimeError('HPCFS must be a tuple of dictionaries')
    return node,list(entries)

def render(entries):
    lines=['HPCFS = (']
    for item in entries:
        lines += ['    {',f"        'file': {str(item['file'])!r},",
                  f"        'label': {str(item['label'])!r},",
                  f"        'kind': {str(item['kind'])!r},",
                  f"        'bluetooth': {bool(item.get('bluetooth', False))!r},",'    },']
    return '\n'.join([*lines,')'])

def replace_block(text,node,entries):
    lines=text.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=[render(entries)+'\n']
    result=''.join(lines);ast.parse(result);return result

def configured_path(item):
    raw=Path(str(item.get('file',''))).expanduser()
    return (raw if raw.is_absolute() else AUDIO_FILTERS/raw).resolve()

def new_entry():
    raw=ask('HpCF WAV filename or absolute path')
    candidate=Path(raw).expanduser()
    path=(candidate if candidate.is_absolute() else AUDIO_FILTERS/candidate).resolve()
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    label=ask('Profile name')
    kind=('IE','OE')[choose('Choose type',('In ear (IE)','Over ear (OE)'))]
    bluetooth=choose('Bluetooth?',('No','Yes'))==1
    stored=path.name if path.parent==AUDIO_FILTERS.resolve() else str(path)
    return {'file':stored,'label':label,'kind':kind,'bluetooth':bluetooth,'resolved':path}
def validate_unique(entries,skip=None):
    labels=set();paths=set()
    for index,item in enumerate(entries):
        if index==skip:continue
        label=str(item.get('label','')).strip().casefold()
        path=os.path.normcase(os.path.realpath(configured_path(item)))
        if label in labels:raise RuntimeError(f'Duplicate device label: {item.get("label")}')
        if path in paths:raise RuntimeError(f'Duplicate HpCF file: {configured_path(item)}')
        labels.add(label);paths.add(path)

def save(before,after):
    with tempfile.TemporaryDirectory() as directory:
        candidate=Path(directory)/CONFIG_SCRIPT.name;candidate.write_text(after,encoding='utf-8')
        subprocess.run([sys.executable,'-m','py_compile',str(candidate)],check=True)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup=BACKUP_DIR/stamp;backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(CONFIG_SCRIPT,backup/CONFIG_SCRIPT.name)
    temporary=CONFIG_SCRIPT.with_name('.'+CONFIG_SCRIPT.name+'.new')
    try:
        temporary.write_text(after,encoding='utf-8');os.chmod(temporary,CONFIG_SCRIPT.stat().st_mode)
        os.replace(temporary,CONFIG_SCRIPT)
        subprocess.run([sys.executable,'-m','py_compile',str(CONFIG_SCRIPT)],check=True)
    except Exception:
        temporary.unlink(missing_ok=True);shutil.copy2(backup/CONFIG_SCRIPT.name,CONFIG_SCRIPT);raise
    return backup

def rebuild_after_change(entries, backup):
    global HPCFS
    previous=HPCFS
    HPCFS=tuple(dict(item) for item in entries)
    try:
        rebuild_all_filters()
    except BaseException:
        HPCFS=previous
        shutil.copy2(backup/CONFIG_SCRIPT.name,CONFIG_SCRIPT)
        subprocess.run([sys.executable,'-m','py_compile',str(CONFIG_SCRIPT)],check=True)
        print('Filter update failed. Device configuration restored from backup.',file=sys.stderr)
        raise
    print('All CamillaDSP filters updated.')

def update_filters(_text=None):
    print('\nUpdating all CamillaDSP filters...')
    rebuild_all_filters()
    print('All CamillaDSP filters updated.')

def add(text):
    node,entries=load(text);entry=new_entry()
    trial=[*entries,{key:entry[key] for key in ('file','label','kind','bluetooth')}];validate_unique(trial)
    print('\nAdd review')
    row(1,f"HpCF: {entry['resolved']}")
    row(2,f"Profile name: {entry['label']}")
    row(3,f"Type: {entry['kind']}")
    row(4,f"Bluetooth: {'Yes' if entry['bluetooth'] else 'No'}")
    if choose('Save this device?',('No','Yes'))==0:return
    backup=save(text,replace_block(text,node,trial))
    print(f"\nAdded {entry['label']}.\nBackup: {backup}")
    rebuild_after_change(trial,backup)
def edit(text):
    node,entries=load(text)
    if not entries:raise RuntimeError('No configured audio devices to edit')
    index=choose('Edit audio device',[f"{x.get('label')} ({x.get('kind')}) | {x.get('file')}" for x in entries])
    current=entries[index]
    path=configured_path(current)
    label=str(current.get('label','')).strip()
    kind=str(current.get('kind','')).upper()
    bluetooth=bool(current.get('bluetooth',False))
    if ask_edit('HpCF',str(path)):
        raw=ask('HpCF WAV filename or absolute path',str(path))
        candidate=Path(raw).expanduser()
        path=(candidate if candidate.is_absolute() else AUDIO_FILTERS/candidate).resolve()
    if ask_edit('Profile name',label):
        label=ask('Profile name',label)
    if ask_edit('Type',kind):
        kind=('IE','OE')[choose('Choose type',('In ear (IE)','Over ear (OE)'))]
    if ask_edit('Bluetooth','Yes' if bluetooth else 'No'):
        bluetooth=choose('Choose Bluetooth setting',('No','Yes'))==1
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    stored=path.name if path.parent==AUDIO_FILTERS.resolve() else str(path)
    trial=list(entries);trial[index]={'file':stored,'label':label,'kind':kind,'bluetooth':bluetooth}
    validate_unique(trial)
    print('\nEdit review')
    row(1,f'HpCF: {path}')
    row(2,f'Profile name: {label}')
    row(3,f'Type: {kind}')
    row(4,f"Bluetooth: {'Yes' if bluetooth else 'No'}")
    if choose('Save these edits?',('No','Yes'))==0:
        print('No files changed.');return
    backup=save(text,replace_block(text,node,trial))
    print(f'\nEdited {label}.\nBackup: {backup}')
    rebuild_after_change(trial,backup)
def remove(text):
    node,entries=load(text)
    if not entries:raise RuntimeError('No configured audio devices to remove')
    index=choose('Remove audio device',[f"{x.get('label')} ({x.get('kind')}) | {x.get('file')}" for x in entries])
    removed=entries.pop(index)
    print('\nRemove review')
    row(1,f"HpCF: {configured_path(removed)}")
    row(2,f"Profile name: {removed.get('label')}")
    row(3,f"Type: {removed.get('kind')}")
    row(4,f"Bluetooth: {'Yes' if removed.get('bluetooth',False) else 'No'}")
    if choose('Remove this device?',('No','Yes'))==0:return
    backup=save(text,replace_block(text,node,entries))
    print(f"\nRemoved {removed.get('label')}.\nBackup: {backup}")
    rebuild_after_change(entries,backup)
def relaunch():
    for command in TERMINALS:
        executable=shutil.which(command[0])
        if executable:subprocess.Popen([executable,*command[1:],sys.executable,str(Path(__file__).resolve())]);return
    raise RuntimeError('No supported terminal found: foot, kitty or alacritty')

def pause():
    print('\nPress any key to close...',end='',flush=True)
    if sys.stdin.isatty():
        fd=sys.stdin.fileno();settings=termios.tcgetattr(fd)
        try:tty.setraw(fd);os.read(fd,1)
        finally:termios.tcsetattr(fd,termios.TCSADRAIN,settings)
    print()

def manager_main():
    text=CONFIG_SCRIPT.read_text(encoding='utf-8')
    actions=(add,edit,remove,update_filters)
    choice=choose('Audio device manager',('Add device','Edit device','Remove device','Update all filters'))
    actions[choice](text)

if __name__=='__main__':
    if not (sys.stdin.isatty() and sys.stdout.isatty()):relaunch();raise SystemExit(0)
    status=0
    try:manager_main()
    except KeyboardInterrupt:print('\nCancelled.');status=130
    except Exception as error:print(f'\nError: {error}',file=sys.stderr);status=1
    finally:shutil.rmtree(SCRIPT_DIR/'__pycache__',ignore_errors=True);pause()
    raise SystemExit(status)
