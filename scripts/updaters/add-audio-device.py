#!/usr/bin/env python3
"""Manage diffuse-field HpCF devices and rebuild H5 CamillaDSP profiles."""
import ast
import math
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
            exec(compile(ast.Module(body=[statement], type_ignores=[]), str(CONTEXT_FILE), 'exec'), namespace, namespace)
        except Exception:
            continue
    return {name: Path(namespace.get(name, default)) for name, default in defaults.items()}

CONTEXT_PATHS = load_context_paths()
AUDIO_ROOT = HOME / 'Documents/prefs/audio'
# This installation stores generated CamillaDSP profiles inside the audio tree.
AUDIO_FILTERS = AUDIO_ROOT / 'filters'
SCRIPT_DIR = CONTEXT_PATHS['UPDATERS']
CONFIG_SCRIPT = Path(__file__).resolve()
BACKUP_DIR = CONTEXT_PATHS['AUDIO_DEVICE_BACKUPS']
HPCF_ANECHOIC = AUDIO_ROOT / 'hpcf anechoic'
H5_WEIGHTED = AUDIO_ROOT / 'sofa average/H5_2945_Weighted_Power_Average_UNNORMALIZED.wav'
H5_MATRIX = AUDIO_ROOT / 'sofa average/H5_2945_Diffuse_Field_Matrix_4ch_FLOAT.wav'
HPCFS = (
    {
        'file': 'Apple EarPods + AhaStyle - Approximate B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'Apple Earpods Silicone Covers',
    },
    {
        'file': 'CMF Buds Pro 2 - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'CMF Buds Pro 2',
    },
    {
        'file': 'HyperX Cloud III - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'HyperX Cloud III',
    },
    {
        'file': 'Apple EarPods - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'earpods',
    },
)
SAMPLE_RATE = 96000
N_FFT = 65536
CAMILLA_PLAYBACK_DEVICE = 'hw:Loopback,0,1'
CAMILLA_PLAYBACK_FORMAT = 'S32_LE'
CAMILLA_ROOT = AUDIO_FILTERS

def slugify(value):
    value = value.lower().replace("'", '')
    return re.sub(r'[^a-z0-9]+', '-', value).strip('-')

def yaml_string(value):
    value = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{value}"'

def atomic_write(path, text):
    temporary = path.with_name('.' + path.name + '.new')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)

def stereo_cascade_gain(h5_path, hpcf_path):
    """Maximum safe gain for independent stereo H5 and HpCF paths."""
    h5, h5_rate = sf.read(h5_path, always_2d=True, dtype='float64')
    hpcf, hpcf_rate = sf.read(hpcf_path, always_2d=True, dtype='float64')
    if h5_rate != SAMPLE_RATE or hpcf_rate != SAMPLE_RATE:
        raise RuntimeError('H5 weighted average and HpCF must both be 96000 Hz')
    if h5.shape[1] != 2 or hpcf.shape[1] != 2:
        raise RuntimeError('H5 weighted average and anechoic HpCF must be stereo')
    left = np.convolve(h5[:, 0], hpcf[:, 0])
    right = np.convolve(h5[:, 1], hpcf[:, 1])
    bound = max(float(np.sum(np.abs(left))), float(np.sum(np.abs(right))))
    if not np.isfinite(bound) or bound <= 0.0:
        raise RuntimeError('Final stereo H5 and HpCF cascade has an invalid peak bound')
    exact = -20.0 * np.log10(bound)
    return math.floor(exact * 1_000_000.0) / 1_000_000.0

def matrix_cascade_gain(matrix_path, hpcf_path):
    """Maximum safe gain with a strict bounded full-scale input guarantee."""
    matrix, matrix_rate = sf.read(matrix_path, always_2d=True, dtype='float64')
    hpcf, hpcf_rate = sf.read(hpcf_path, always_2d=True, dtype='float64')
    if matrix_rate != SAMPLE_RATE or hpcf_rate != SAMPLE_RATE:
        raise RuntimeError('H5 matrix and HpCF must both be 96000 Hz')
    if matrix.shape[1] != 4:
        raise RuntimeError('H5 matrix must have channels LL, R-to-L, L-to-R, RR')
    if hpcf.shape[1] != 2:
        raise RuntimeError('Anechoic HpCF must be stereo')
    paths = (
        np.convolve(matrix[:, 0], hpcf[:, 0]),
        np.convolve(matrix[:, 1], hpcf[:, 0]),
        np.convolve(matrix[:, 2], hpcf[:, 1]),
        np.convolve(matrix[:, 3], hpcf[:, 1]),
    )
    left = float(np.sum(np.abs(paths[0])) + np.sum(np.abs(paths[1])))
    right = float(np.sum(np.abs(paths[2])) + np.sum(np.abs(paths[3])))
    bound = max(left, right)
    if not np.isfinite(bound) or bound <= 0.0:
        raise RuntimeError('Final matrix and HpCF cascade has an invalid peak bound')
    exact = -20.0 * np.log10(bound)
    return math.floor(exact * 1_000_000.0) / 1_000_000.0

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

def make_stereo_profile(index, item, hpcf_path, field_path, preamp_db):
    """Normal stereo profile: H5 tonal average with no channel crossfeed."""
    label = str(item['label']).strip()
    filename = f'{slugify(label)}.yml'
    title = yaml_string(label)
    h5 = yaml_string(field_path)
    hpcf = yaml_string(hpcf_path)
    config = f'''---
title: {title}
description: "H5 2945-point weighted stereo average with anechoic headphone correction; no crossfeed"
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
filters:
  h5_left:
    type: Conv
    parameters:
      type: Wav
      filename: {h5}
      channel: 0
  h5_right:
    type: Conv
    parameters:
      type: Wav
      filename: {h5}
      channel: 1
  hpcf_left:
    type: Conv
    parameters:
      type: Wav
      filename: {hpcf}
      channel: 0
  hpcf_right:
    type: Conv
    parameters:
      type: Wav
      filename: {hpcf}
      channel: 1
  auto_gain:
    type: Gain
    parameters:
      gain: {preamp_db:.6f}
      scale: dB
pipeline:
  - type: Filter
    channels: [0]
    names: [h5_left, hpcf_left]
  - type: Filter
    channels: [1]
    names: [h5_right, hpcf_right]
  - type: Filter
    channels: [0, 1]
    names: [auto_gain]
'''
    return CAMILLA_ROOT / filename, config

def make_crossfeed_profile(index, item, hpcf_path, matrix_path, preamp_db):
    label = str(item['label']).strip()
    filename = f'{slugify(label)}-crossfeed.yml'
    title = yaml_string(f'{label} - Crossfeed')
    matrix = yaml_string(matrix_path)
    hpcf = yaml_string(hpcf_path)
    config = f'''---
title: {title}
description: "H5 2945-point diffuse-field 2x2 matrix with anechoic headphone correction"
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
  matrix_branches:
    channels:
      in: 2
      out: 4
    mapping:
      - dest: 0
        sources:
          - channel: 0
            gain: 0
            inverted: false
      - dest: 1
        sources:
          - channel: 1
            gain: 0
            inverted: false
      - dest: 2
        sources:
          - channel: 0
            gain: 0
            inverted: false
      - dest: 3
        sources:
          - channel: 1
            gain: 0
            inverted: false
  matrix_sum:
    channels:
      in: 4
      out: 2
    mapping:
      - dest: 0
        sources:
          - channel: 0
            gain: 0
            inverted: false
          - channel: 1
            gain: 0
            inverted: false
      - dest: 1
        sources:
          - channel: 2
            gain: 0
            inverted: false
          - channel: 3
            gain: 0
            inverted: false
filters:
  matrix_ll:
    type: Conv
    parameters:
      type: Wav
      filename: {matrix}
      channel: 0
  matrix_rl:
    type: Conv
    parameters:
      type: Wav
      filename: {matrix}
      channel: 1
  matrix_lr:
    type: Conv
    parameters:
      type: Wav
      filename: {matrix}
      channel: 2
  matrix_rr:
    type: Conv
    parameters:
      type: Wav
      filename: {matrix}
      channel: 3
  hpcf_left:
    type: Conv
    parameters:
      type: Wav
      filename: {hpcf}
      channel: 0
  hpcf_right:
    type: Conv
    parameters:
      type: Wav
      filename: {hpcf}
      channel: 1
  auto_gain:
    type: Gain
    parameters:
      gain: {preamp_db:.6f}
      scale: dB
pipeline:
  - type: Mixer
    name: matrix_branches
  - type: Filter
    channels: [0]
    names: [matrix_ll]
  - type: Filter
    channels: [1]
    names: [matrix_rl]
  - type: Filter
    channels: [2]
    names: [matrix_lr]
  - type: Filter
    channels: [3]
    names: [matrix_rr]
  - type: Mixer
    name: matrix_sum
  - type: Filter
    channels: [0]
    names: [hpcf_left]
  - type: Filter
    channels: [1]
    names: [hpcf_right]
  - type: Filter
    channels: [0, 1]
    names: [auto_gain]
'''
    return CAMILLA_ROOT / filename, config

def configured_path(item):
    raw = Path(str(item.get('file', ''))).expanduser()
    return (raw if raw.is_absolute() else HPCF_ANECHOIC / raw).resolve()

def profile_wav_name(label):
    name = str(label).strip().replace('/', '_').replace('\\', '_')
    if not name or name in ('.', '..'):
        raise RuntimeError('Profile name cannot be used as an anechoic WAV filename')
    return name + '.wav'

def anechoic_path(item):
    return (HPCF_ANECHOIC / profile_wav_name(item.get('label', ''))).resolve()

def commit_profiles(candidates):
    CAMILLA_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.profile-commit-', dir=CAMILLA_ROOT.parent) as transaction_name:
        transaction = Path(transaction_name)
        backup = transaction / 'backup'
        backup.mkdir()
        previous = []
        for pattern in ('*.yml', '*.yaml'):
            for old in CAMILLA_ROOT.rglob(pattern):
                if old.is_file():
                    relative = old.relative_to(CAMILLA_ROOT)
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(old, saved)
                    previous.append((relative, saved))
        try:
            for pattern in ('*.yml', '*.yaml'):
                for old in CAMILLA_ROOT.rglob(pattern):
                    if old.is_file():
                        old.unlink()
            for path, text in candidates:
                atomic_write(path, text)
        except BaseException as commit_error:
            restore_errors = []
            for pattern in ('*.yml', '*.yaml'):
                for partial in CAMILLA_ROOT.rglob(pattern):
                    if partial.is_file():
                        try:
                            partial.unlink()
                        except OSError as error:
                            restore_errors.append(f'remove {partial}: {error}')
            for relative, saved in previous:
                destination = CAMILLA_ROOT / relative
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, destination)
                except OSError as error:
                    restore_errors.append(f'restore {destination}: {error}')
            if restore_errors:
                raise RuntimeError(
                    'Profile commit failed and rollback was incomplete: '
                    + '; '.join(restore_errors)
                ) from commit_error
            raise RuntimeError(
                'Profile commit failed; all previous YAML profiles were restored: '
                + str(commit_error)
            ) from commit_error

def rebuild_all_filters():
    if not H5_WEIGHTED.is_file():
        raise SystemExit(f'Missing measured H5 weighted stereo average: {H5_WEIGHTED}')
    if not H5_MATRIX.is_file():
        raise SystemExit(f'Missing measured H5 diffuse matrix: {H5_MATRIX}')
    if not HPCF_ANECHOIC.is_dir():
        raise SystemExit(f'Missing anechoic HpCF folder: {HPCF_ANECHOIC}')
    devices = []
    labels = set()
    sources = set()
    for item in HPCFS:
        label = str(item.get('label', '')).strip()
        source = configured_path(item)
        if not label:
            raise SystemExit(f'Invalid HPCFS entry: {item!r}')
        if not source.is_file() or source.suffix.casefold() != '.wav':
            raise SystemExit(f'Missing anechoic HpCF WAV: {source}')
        if label.casefold() in labels or os.path.normcase(str(source)) in sources:
            raise SystemExit(f'Duplicate HpCF device: {label} / {source}')
        labels.add(label.casefold())
        sources.add(os.path.normcase(str(source)))
        devices.append((item, source))
    generated = []
    for index, (item, source) in enumerate(devices, 1):
        stereo_gain = stereo_cascade_gain(H5_WEIGHTED, source)
        crossfeed_gain = matrix_cascade_gain(H5_MATRIX, source)
        generated.extend((
            make_stereo_profile(index, item, source, H5_WEIGHTED, stereo_gain),
            make_crossfeed_profile(index, item, source, H5_MATRIX, crossfeed_gain),
        ))
    candidates = [(CAMILLA_ROOT / '00-filterless.yml', filterless_text()), *generated]
    camilladsp = Path('/run/current-system/sw/bin/camilladsp')
    if not camilladsp.is_file():
        raise SystemExit(f'Missing CamillaDSP executable: {camilladsp}')
    with tempfile.TemporaryDirectory() as validation_name:
        validation = Path(validation_name)
        for number, (path, text) in enumerate(candidates):
            candidate = validation / f'{number:04d}-{path.name}'
            candidate.write_text(text, encoding='utf-8')
            check = subprocess.run([str(camilladsp), '--check', str(candidate)], text=True, capture_output=True)
            if check.returncode:
                raise SystemExit(f'Invalid generated profile {path.name}: ' + (check.stderr.strip() or check.stdout.strip()))
    commit_profiles(candidates)
    print(f'Using {len(devices)} anechoic HpCFs from {HPCF_ANECHOIC} or absolute paths')
    print(f'Rebuilt {len(candidates)} CamillaDSP YAML profiles directly in {CAMILLA_ROOT}')
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
                  '    },']
    return '\n'.join([*lines,')'])

def replace_block(text,node,entries):
    lines=text.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=[render(entries)+'\n']
    result=''.join(lines);ast.parse(result);return result

def new_entry():
    raw=ask('HpCF WAV filename from hpcf anechoic, or absolute path')
    candidate=Path(raw).expanduser()
    path=(candidate if candidate.is_absolute() else HPCF_ANECHOIC/candidate).resolve()
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    label=ask('Profile name')
    stored=path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path)
    return {'file':stored,'label':label,'resolved':path}
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
    except BaseException as error:
        HPCFS=previous
        shutil.copy2(backup/CONFIG_SCRIPT.name,CONFIG_SCRIPT)
        subprocess.run([sys.executable,'-m','py_compile',str(CONFIG_SCRIPT)],check=True)
        detail = str(error).strip() or error.__class__.__name__
        raise RuntimeError(
            'Filter update failed; device configuration restored from backup. '
            'Cause: ' + detail
        ) from error
    print('All CamillaDSP filters updated.')

def update_filters(_text=None):
    print('\nUpdating all CamillaDSP filters...')
    rebuild_all_filters()
    print('All CamillaDSP filters updated.')

def clean_devices(text):
    node, entries = load(text)
    print('\nClean devices review')
    row(1, f'Configured devices to remove: {len(entries)}')
    row(2, f'Anechoic HpCF folder: {HPCF_ANECHOIC}')
    row(3, f'CamillaDSP YAML folder: {CAMILLA_ROOT}')
    row(4, 'Result: only 00-filterless.yml will be regenerated')
    if choose('Remove every configured device and generated filter?', ('No', 'Yes')) == 0:
        print('No files changed.')
        return
    after = replace_block(text, node, [])
    backup = save(text, after)
    global HPCFS
    previous = HPCFS
    HPCFS = ()
    try:
        rebuild_all_filters()
    except BaseException as error:
        HPCFS = previous
        shutil.copy2(backup / CONFIG_SCRIPT.name, CONFIG_SCRIPT)
        subprocess.run([sys.executable, '-m', 'py_compile', str(CONFIG_SCRIPT)], check=True)
        detail = str(error).strip() or error.__class__.__name__
        raise RuntimeError(
            'Clean devices failed; device configuration restored from backup. '
            'Cause: ' + detail
        ) from error
    print(f'\nRemoved {len(entries)} configured devices.')
    print(f'Backup: {backup}')
    print('Only 00-filterless.yml remains generated.')

def add(text):
    node,entries=load(text);entry=new_entry()
    trial=[*entries,{key:entry[key] for key in ('file','label')}];validate_unique(trial)
    print('\nAdd review')
    row(1,f"HpCF: {entry['resolved']}")
    row(2,f"Profile name: {entry['label']}")
    if choose('Save this device?',('No','Yes'))==0:return
    backup=save(text,replace_block(text,node,trial))
    print(f"\nAdded {entry['label']}.\nBackup: {backup}")
    rebuild_after_change(trial,backup)
def edit(text):
    node,entries=load(text)
    if not entries:raise RuntimeError('No configured audio devices to edit')
    index=choose('Edit audio device',[f"{x.get('label')} | {x.get('file')}" for x in entries])
    current=entries[index]
    path=configured_path(current)
    label=str(current.get('label','')).strip()
    if ask_edit('HpCF',str(path)):
        raw=ask('HpCF WAV filename or absolute path',str(path))
        candidate=Path(raw).expanduser()
        path=(candidate if candidate.is_absolute() else HPCF_ANECHOIC/candidate).resolve()
    if ask_edit('Profile name',label):
        label=ask('Profile name',label)
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    stored=path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path)
    trial=list(entries);trial[index]={'file':stored,'label':label}
    validate_unique(trial)
    print('\nEdit review')
    row(1,f'HpCF: {path}')
    row(2,f'Profile name: {label}')
    if choose('Save these edits?',('No','Yes'))==0:
        print('No files changed.');return
    backup=save(text,replace_block(text,node,trial))
    print(f'\nEdited {label}.\nBackup: {backup}')
    rebuild_after_change(trial,backup)
def remove(text):
    node,entries=load(text)
    if not entries:raise RuntimeError('No configured audio devices to remove')
    index=choose('Remove audio device',[f"{x.get('label')} | {x.get('file')}" for x in entries])
    removed=entries.pop(index)
    print('\nRemove review')
    row(1,f"HpCF: {configured_path(removed)}")
    row(2,f"Profile name: {removed.get('label')}")
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
    actions=(add,edit,remove,clean_devices,update_filters)
    choice=choose('Audio device manager',('Add device','Edit device','Remove device','Clean devices','Update all filters'))
    actions[choice](text)

if __name__=='__main__':
    if not (sys.stdin.isatty() and sys.stdout.isatty()):relaunch();raise SystemExit(0)
    status=0
    try:manager_main()
    except KeyboardInterrupt:print('\nCancelled.');status=130
    except Exception as error:print(f'\nError: {error}',file=sys.stderr);status=1
    finally:shutil.rmtree(SCRIPT_DIR/'__pycache__',ignore_errors=True);pause()
    raise SystemExit(status)
