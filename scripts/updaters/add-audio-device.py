#!/usr/bin/env python3
"""Manage diffuse-field HpCF devices and rebuild H5 CamillaDSP profiles."""
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
            exec(compile(ast.Module(body=[statement], type_ignores=[]), str(CONTEXT_FILE), 'exec'), namespace, namespace)
        except Exception:
            continue
    return {name: Path(namespace.get(name, default)) for name, default in defaults.items()}

CONTEXT_PATHS = load_context_paths()
AUDIO_ROOT = HOME / 'Documents/prefs/audio'
AUDIO_FILTERS = AUDIO_ROOT / 'filters'
SCRIPT_DIR = CONTEXT_PATHS['UPDATERS']
CONFIG_SCRIPT = Path(__file__).resolve()
BACKUP_DIR = CONTEXT_PATHS['AUDIO_DEVICE_BACKUPS']
HPCF_DIFFUSE = AUDIO_ROOT / 'hpcfs-diffuse'
HPCF_ANECHOIC = AUDIO_ROOT / 'hpcfs-anechoic'
DIFFUSE_FIELD_EQ = AUDIO_ROOT / 'diffuse_field_eq_for_in_ear_headphones.wav'
OE_ADDITIONAL_COMP = AUDIO_ROOT / 'additional_comp_for_over_&_on_ear_headphones.wav'
H5_WEIGHTED = AUDIO_ROOT / 'H5/2945-weighted-average/H5_2945_Weighted_Power_Average_UNNORMALIZED.wav'

HPCFS = (
    {
        'file': 'Apple_EarPods_Ahastyle_Covers_Custom_Average_A+B.wav',
        'label': 'Apple Earpods Silicone Covers',
        'kind': 'IE',
    },
    {
        'file': 'HyperX_Cloud_III_Average.wav',
        'label': 'HyperX Cloud III',
        'kind': 'OE',
    },
    {
        'file': 'CMF_by_Nothing_Buds_Pro_2_Sample_A.wav',
        'label': 'CMF Buds Pro 2',
        'kind': 'IE',
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

def next_pow2(value):
    return 1 << (int(value) - 1).bit_length()

def minimum_phase_from_magnitude(magnitude, nfft, taps):
    if np.any(~np.isfinite(magnitude)) or np.any(magnitude <= 0.0):
        raise RuntimeError('A requested anechoic magnitude is non-finite or non-positive')
    cepstrum = np.fft.irfft(np.log(magnitude), nfft)
    minimum = np.zeros(nfft, dtype=np.float64)
    minimum[0] = cepstrum[0]
    minimum[1:nfft // 2] = 2.0 * cepstrum[1:nfft // 2]
    minimum[nfft // 2] = cepstrum[nfft // 2]
    return np.fft.irfft(np.exp(np.fft.rfft(minimum)), nfft)[:taps]

def response_magnitude_for_grid(response, response_rate, rate, nfft):
    source_nfft = next_pow2(max(16384, len(response) * 16))
    spectra = np.fft.rfft(response, source_nfft, axis=0)
    magnitude = np.sqrt(np.mean(np.abs(spectra) ** 2, axis=1))
    source_frequency = np.fft.rfftfreq(source_nfft, 1.0 / response_rate)
    target_frequency = np.fft.rfftfreq(nfft, 1.0 / rate)
    return np.interp(target_frequency, source_frequency, magnitude,
                     left=magnitude[0], right=magnitude[-1])

def make_anechoic_hpcf(diffuse_hpcf, destination, diffuse_eq, diffuse_rate,
                        kind, oe_comp=None, oe_comp_rate=None):
    audio, rate = sf.read(diffuse_hpcf, always_2d=True, dtype='float64')
    if rate != SAMPLE_RATE:
        raise RuntimeError(f'Expected {SAMPLE_RATE} Hz HpCF, got {rate}: {diffuse_hpcf}')
    taps = len(audio)
    nfft = next_pow2(max(16384, taps * 16, len(diffuse_eq) * 16))
    diffuse_magnitude = response_magnitude_for_grid(diffuse_eq, diffuse_rate, rate, nfft)
    if np.any(diffuse_magnitude == 0.0):
        raise RuntimeError(f'Diffuse-field EQ contains an exact zero and cannot be subtracted: {DIFFUSE_FIELD_EQ}')
    oe_magnitude = None
    if kind == 'OE':
        if oe_comp is None or oe_comp_rate is None:
            raise RuntimeError(f'Missing over/on-ear additional compensation: {OE_ADDITIONAL_COMP}')
        oe_magnitude = response_magnitude_for_grid(oe_comp, oe_comp_rate, rate, nfft)
    output = np.zeros_like(audio)
    for channel in range(audio.shape[1]):
        input_magnitude = np.abs(np.fft.rfft(audio[:, channel], nfft))
        target_magnitude = input_magnitude / diffuse_magnitude
        if oe_magnitude is not None:
            target_magnitude *= oe_magnitude
        output[:, channel] = minimum_phase_from_magnitude(target_magnitude, nfft, taps)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, output, rate, subtype='FLOAT')
    return destination

def cascade_gain(h5_path, hpcf_path):
    h5, h5_rate = sf.read(h5_path, always_2d=True, dtype='float64')
    hpcf, hpcf_rate = sf.read(hpcf_path, always_2d=True, dtype='float64')
    if h5_rate != SAMPLE_RATE or hpcf_rate != SAMPLE_RATE:
        raise RuntimeError('H5 and HpCF must both be 96000 Hz')
    if h5.shape[1] != 2 or hpcf.shape[1] != 2:
        raise RuntimeError('H5 and HpCF must both be stereo')
    nfft = next_pow2(max(N_FFT, len(h5) + len(hpcf) - 1))
    frequency = np.fft.rfftfreq(nfft, 1.0 / SAMPLE_RATE)
    audible = (frequency >= 10.0) & (frequency <= 19000.0)
    peaks = []
    for channel in range(2):
        cascade = np.abs(np.fft.rfft(h5[:, channel], nfft) * np.fft.rfft(hpcf[:, channel], nfft))
        peaks.append(float(np.max(20.0 * np.log10(np.maximum(cascade[audible], np.finfo(float).tiny)))))
    preamp_db = -round(max(peaks), 1)
    return preamp_db

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

def make_profile(index, item, hpcf_path, preamp_db):
    kind = str(item['kind']).lower()
    label = str(item['label']).strip()
    filename = f'{index:02d}-{kind}-{slugify(label)}-0000ms-h5-average.yml'
    title = yaml_string(f'{label} - H5 weighted average')
    h5 = yaml_string(H5_WEIGHTED)
    hpcf = yaml_string(hpcf_path)
    config = f'''---
title: {title}
description: "H5 2945-direction weighted power average with anechoic headphone correction"
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
      gain: {preamp_db:.1f}
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

def configured_path(item):
    raw = Path(str(item.get('file', ''))).expanduser()
    return (raw if raw.is_absolute() else HPCF_DIFFUSE / raw).resolve()

def profile_wav_name(label):
    name = str(label).strip().replace('/', '_').replace('\\', '_')
    if not name or name in ('.', '..'):
        raise RuntimeError('Profile name cannot be used as an anechoic WAV filename')
    return name + '.wav'

def anechoic_path(item):
    return (HPCF_ANECHOIC / profile_wav_name(item.get('label', ''))).resolve()

def rebuild_all_filters():
    if not H5_WEIGHTED.is_file():
        raise SystemExit(f'Missing H5 weighted average: {H5_WEIGHTED}')
    if not DIFFUSE_FIELD_EQ.is_file():
        raise SystemExit(f'Missing diffuse-field EQ: {DIFFUSE_FIELD_EQ}')
    if not HPCF_DIFFUSE.is_dir():
        raise SystemExit(f'Missing diffuse HpCF folder: {HPCF_DIFFUSE}')
    devices = []
    labels = set()
    sources = set()
    for item in HPCFS:
        kind = str(item.get('kind', '')).upper()
        label = str(item.get('label', '')).strip()
        source = configured_path(item)
        if kind not in ('IE', 'OE') or not label:
            raise SystemExit(f'Invalid HPCFS entry: {item!r}')
        if not source.is_file() or source.suffix.casefold() != '.wav':
            raise SystemExit(f'Missing diffuse HpCF WAV: {source}')
        if label.casefold() in labels or os.path.normcase(str(source)) in sources:
            raise SystemExit(f'Duplicate HpCF device: {label} / {source}')
        labels.add(label.casefold()); sources.add(os.path.normcase(str(source)))
        devices.append((item, source, anechoic_path(item)))

    diffuse_eq, diffuse_rate = sf.read(DIFFUSE_FIELD_EQ, always_2d=True, dtype='float64')
    needs_oe = any(str(item.get('kind', '')).upper() == 'OE' for item, _, _ in devices)
    if needs_oe and not OE_ADDITIONAL_COMP.is_file():
        raise SystemExit(f'Missing over/on-ear additional compensation: {OE_ADDITIONAL_COMP}')
    oe_comp = oe_comp_rate = None
    if needs_oe:
        oe_comp, oe_comp_rate = sf.read(OE_ADDITIONAL_COMP, always_2d=True, dtype='float64')
    HPCF_ANECHOIC.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=HPCF_ANECHOIC) as temporary_name:
        temporary = Path(temporary_name)
        staged_hpcfs = []
        for item, source, destination in devices:
            staged = temporary / destination.name
            make_anechoic_hpcf(source, staged, diffuse_eq, diffuse_rate,
                                str(item.get('kind', '')).upper(),
                                oe_comp, oe_comp_rate)
            staged_hpcfs.append((item, staged, destination))

        generated = []
        validation_generated = []
        for index, (item, staged, destination) in enumerate(staged_hpcfs, 1):
            gain = cascade_gain(H5_WEIGHTED, staged)
            generated.append(make_profile(index, item, destination, gain))
            validation_generated.append(make_profile(index, item, staged, gain))
        candidates = [(CAMILLA_ROOT / '00-filterless.yml', filterless_text()), *generated]
        validation_candidates = [(CAMILLA_ROOT / '00-filterless.yml', filterless_text()), *validation_generated]

        camilladsp = Path('/run/current-system/sw/bin/camilladsp')
        if not camilladsp.is_file():
            raise SystemExit(f'Missing CamillaDSP executable: {camilladsp}')
        with tempfile.TemporaryDirectory() as validation_name:
            validation = Path(validation_name)
            for number, (path, text) in enumerate(validation_candidates):
                candidate = validation / f'{number:04d}-{path.name}'
                candidate.write_text(text, encoding='utf-8')
                check = subprocess.run([str(camilladsp), '--check', str(candidate)], text=True, capture_output=True)
                if check.returncode:
                    raise SystemExit(f'Invalid generated profile {path.name}: ' + (check.stderr.strip() or check.stdout.strip()))

        # Commit regenerated anechoic HpCFs only after every profile validates.
        for old in HPCF_ANECHOIC.glob('*.wav'):
            old.unlink()
        for _, staged, destination in staged_hpcfs:
            staged.replace(destination)

    # This directory is fully generator-owned for YAML profiles.
    for pattern in ('*.yml', '*.yaml'):
        for old in CAMILLA_ROOT.rglob(pattern):
            if old.is_file():
                old.unlink()
    for path, text in candidates:
        atomic_write(path, text)
    print(f'Regenerated {len(devices)} anechoic HpCFs in {HPCF_ANECHOIC}')
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
                  f"        'kind': {str(item['kind'])!r},",
                  '    },']
    return '\n'.join([*lines,')'])

def replace_block(text,node,entries):
    lines=text.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=[render(entries)+'\n']
    result=''.join(lines);ast.parse(result);return result

def new_entry():
    raw=ask('HpCF WAV filename or absolute path')
    candidate=Path(raw).expanduser()
    path=(candidate if candidate.is_absolute() else HPCF_DIFFUSE/candidate).resolve()
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    label=ask('Profile name')
    kind=('IE','OE')[choose('Choose type',('In ear (IE)','Over ear / on ear (OE)'))]
    stored=path.name if path.parent==HPCF_DIFFUSE.resolve() else str(path)
    return {'file':stored,'label':label,'kind':kind,'resolved':path}
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
    row(2, f'Generated anechoic HpCF folder: {HPCF_ANECHOIC}')
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
    trial=[*entries,{key:entry[key] for key in ('file','label','kind')}];validate_unique(trial)
    print('\nAdd review')
    row(1,f"HpCF: {entry['resolved']}")
    row(2,f"Profile name: {entry['label']}")
    row(3,f"Type: {entry['kind']}")
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
    if ask_edit('HpCF',str(path)):
        raw=ask('HpCF WAV filename or absolute path',str(path))
        candidate=Path(raw).expanduser()
        path=(candidate if candidate.is_absolute() else HPCF_DIFFUSE/candidate).resolve()
    if ask_edit('Profile name',label):
        label=ask('Profile name',label)
    if ask_edit('Type',kind):
        kind=('IE','OE')[choose('Choose type',('In ear (IE)','Over ear / on ear (OE)'))]
    if not path.is_file():raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav':raise RuntimeError('HpCF must be a WAV file')
    stored=path.name if path.parent==HPCF_DIFFUSE.resolve() else str(path)
    trial=list(entries);trial[index]={'file':stored,'label':label,'kind':kind}
    validate_unique(trial)
    print('\nEdit review')
    row(1,f'HpCF: {path}')
    row(2,f'Profile name: {label}')
    row(3,f'Type: {kind}')
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
