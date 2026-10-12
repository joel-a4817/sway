#!/usr/bin/env python3
"""Manage anechoic HpCF devices and sample-safe H5 profiles."""
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
    defaults = {'UPDATERS': HOME / '.config/sway/scripts/updaters',
                'AUDIO_DEVICE_BACKUPS': HOME / '.local/state/sway/audio-device-updater/backups'}
    if not CONTEXT_FILE.is_file(): return defaults
    namespace = {'Path': Path, 'HOME': HOME}
    wanted = set(defaults) | {'HOME', 'SCRIPTS'}
    for statement in ast.parse(CONTEXT_FILE.read_text(encoding='utf-8'), str(CONTEXT_FILE)).body:
        if not isinstance(statement, ast.Assign): continue
        names = [t.id for t in statement.targets if isinstance(t, ast.Name)]
        if not any(name in wanted for name in names): continue
        try: exec(compile(ast.Module(body=[statement], type_ignores=[]), str(CONTEXT_FILE), 'exec'), namespace, namespace)
        except Exception: continue
    return {name: Path(namespace.get(name, default)) for name, default in defaults.items()}
CONTEXT_PATHS = load_context_paths()
AUDIO_ROOT = HOME / 'Documents/prefs/audio'
AUDIO_FILTERS = AUDIO_ROOT / 'filters'
SCRIPT_DIR = CONTEXT_PATHS['UPDATERS']
CONFIG_SCRIPT = Path(__file__).resolve()
BACKUP_DIR = CONTEXT_PATHS['AUDIO_DEVICE_BACKUPS']
HPCF_ANECHOIC = AUDIO_ROOT / 'hpcf anechoic'
MEASUREMENTS_DIR = AUDIO_ROOT / 'measurements'
H5_WEIGHTED = AUDIO_ROOT / 'sofa average/H5_2945_Weighted_Power_Average_UNNORMALIZED.wav'
H5_MATRIX = AUDIO_ROOT / 'sofa average/H5_2945_Avg_Crossfeed_4ch_FLOAT.wav'
HPCFS = (
    {
        'file': 'Apple EarPods + AhaStyle - Approximate B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'earpods + silicone tips',
        'include_smoothed': True,
    },
    {
        'file': 'Apple EarPods - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'earpods',
        'include_smoothed': True,
    },
    {
        'file': 'CMF Buds Pro 2 - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'buds pro 2',
        'include_smoothed': True,
    },
    {
        'file': 'HyperX Cloud III - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'cloud iii',
        'include_smoothed': True,
    },
)
SAMPLE_RATE = 96000
CAMILLA_PLAYBACK_DEVICE = 'hw:Loopback,0,1'
CAMILLA_PLAYBACK_FORMAT = 'S32_LE'
CAMILLA_ROOT = AUDIO_FILTERS
OUTPUT_SAMPLE_CEILING = 1.0 - 2.0 ** -31
BOUND_NUMERICAL_GUARD = 1e-9

def slugify(value):
    return re.sub(r'[^a-z0-9]+', '-', value.lower().replace("'", '')).strip('-')
def yaml_string(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
def atomic_write(path, text):
    temporary = path.with_name('.' + path.name + '.new')
    temporary.write_text(text, encoding='utf-8'); temporary.replace(path)
def response_peak_preamp_db(peak_linear, description):
    peak_linear = float(peak_linear)
    if not np.isfinite(peak_linear) or peak_linear <= 0.0: raise RuntimeError(f'{description} has an invalid sample bound')
    guarded = np.nextafter(peak_linear * (1.0 + BOUND_NUMERICAL_GUARD), np.inf)
    exact = min(0.0, -20.0 * np.log10(guarded))
    gain = math.floor(exact * 1_000_000_000_000.0) / 1_000_000_000_000.0
    if guarded * 10.0 ** (gain / 20.0) > 1.0: raise RuntimeError(f'{description}: rounded preamp fails the sample bound')
    return gain
def dense_response_nfft(*lengths):
    minimum = max(65536, max(int(length) for length in lengths) * 16)
    return 1 << (minimum - 1).bit_length()
def convolve_fir(left, right):
    length = len(left) + len(right) - 1
    nfft = 1 << (length - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(left, n=nfft) * np.fft.rfft(right, n=nfft), n=nfft)[:length]
def read_fir(path, channels):
    data, rate = sf.read(path, always_2d=True, dtype='float64')
    if rate != SAMPLE_RATE or data.shape[1] != channels or not len(data): raise RuntimeError(f'{path}: expected nonempty {channels}-channel {SAMPLE_RATE} Hz FIR')
    if not np.isfinite(data).all(): raise RuntimeError(f'{path}: non-finite FIR samples')
    return data
def row_sample_extrema(kernels):
    positive_sums = []; negative_sums = []
    for kernel in kernels:
        if not np.isfinite(kernel).all(): raise RuntimeError('Final cascade has non-finite samples')
        positive_sums.append(math.fsum(float(v) for v in kernel if v > 0.0))
        negative_sums.append(math.fsum(float(-v) for v in kernel if v < 0.0))
    positive = math.fsum(positive_sums); negative = math.fsum(negative_sums)
    return math.fsum((OUTPUT_SAMPLE_CEILING * positive, negative)), math.fsum((positive, OUTPUT_SAMPLE_CEILING * negative))
def sample_safe_preamp(rows, description):
    extrema = [row_sample_extrema(kernels) for kernels in rows]
    peak = max(maximum / OUTPUT_SAMPLE_CEILING for maximum, minimum_abs in extrema)
    peak = max(peak, max(minimum_abs for maximum, minimum_abs in extrema))
    return response_peak_preamp_db(peak, description)
def report_cascade_headroom(rows, description, gain):
    nfft = dense_response_nfft(*(len(k) for row in rows for k in row))
    frequencies = np.fft.rfftfreq(nfft, 1.0 / SAMPLE_RATE)
    audible = (frequencies >= 20.0) & (frequencies <= 20000.0)
    full_peak = band_peak = 0.0
    for row in rows:
        response = sum(np.abs(np.fft.rfft(k, n=nfft)) for k in row)
        full_peak = max(full_peak, float(np.max(response)))
        band_peak = max(band_peak, float(np.max(response[audible])))
    print(f'{description}: 20-20000 Hz sine/row peak {20*np.log10(band_peak):.6f} dB; full-band sine/row peak {20*np.log10(full_peak):.6f} dB; sample-safe gain {gain:.12f} dB')
def stereo_cascade_gain(h5_path, hpcf_path):
    h5 = read_fir(h5_path, 2); hp = read_fir(hpcf_path, 2)
    rows = [[convolve_fir(h5[:, c], hp[:, c])] for c in range(2)]
    gain = sample_safe_preamp(rows, 'Final stereo PCM sample bound')
    report_cascade_headroom(rows, str(hpcf_path) + ' stereo', gain)
    return gain
def matrix_cascade_gain(matrix_path, hpcf_path):
    matrix = read_fir(matrix_path, 4); hp = read_fir(hpcf_path, 2)
    kernels = [convolve_fir(matrix[:, c], hp[:, 0 if c < 2 else 1]) for c in range(4)]
    rows = [kernels[:2], kernels[2:]]
    gain = sample_safe_preamp(rows, 'Final matrix PCM sample bound')
    report_cascade_headroom(rows, str(hpcf_path) + ' crossfeed', gain)
    return gain

def devices_text():
    return f'''devices:
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
'''
def filterless_text():
    return '---\ntitle: "Filterless / Speakers"\ndescription: "Pass-through CamillaDSP profile with no filters"\n' + devices_text() + 'pipeline: []\n'
def profile_label(item):
    label = str(item['label']).strip()
    if item.get('_smoothed') and label.endswith(' - Smoothed'): label = label[:-11]
    return label
def conv_text(name, path, channel):
    return f'''  {name}:
    type: Conv
    parameters:
      type: Wav
      filename: {yaml_string(path)}
      channel: {channel}
'''
def gain_text(gain):
    return f'''  auto_gain:
    type: Gain
    parameters:
      gain: {gain:.12f}
      scale: dB
'''
def make_stereo_profile(index, item, hpcf_path, field_path, preamp_db):
    label = profile_label(item); suffix = '-smoothed' if item.get('_smoothed') else ''
    title = label + (' - Smoothed' if item.get('_smoothed') else '')
    text = '---\ntitle: ' + yaml_string(title) + '\ndescription: "H5 measured-grid weighted stereo average with anechoic headphone correction; no crossfeed"\n' + devices_text() + 'filters:\n'
    for name, path, ch in [('h5_left',field_path,0),('h5_right',field_path,1),('hpcf_left',hpcf_path,0),('hpcf_right',hpcf_path,1)]: text += conv_text(name,path,ch)
    text += gain_text(preamp_db) + '''pipeline:
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
    return CAMILLA_ROOT / (slugify(label) + suffix + '.yml'), text
def make_crossfeed_profile(index, item, hpcf_path, matrix_path, preamp_db):
    label = profile_label(item); suffix = '-smoothed' if item.get('_smoothed') else ''
    title = label + (' - Smoothed' if item.get('_smoothed') else '') + ' - Crossfeed'
    text = '---\ntitle: ' + yaml_string(title) + '\ndescription: "H5 full-sphere hemisphere ear-power 2x2 crossfeed matrix with anechoic headphone correction"\n' + devices_text() + '''mixers:
  matrix_branches:
    channels:
      in: 2
      out: 4
    mapping:
'''
    for dest,ch in enumerate([0,1,0,1]): text += f'''      - dest: {dest}
        sources:
          - channel: {ch}
            gain: 0
            inverted: false
'''
    text += '''  matrix_sum:
    channels:
      in: 4
      out: 2
    mapping:
'''
    for dest,channels in enumerate([[0,1],[2,3]]):
        text += f'      - dest: {dest}\n        sources:\n'
        for ch in channels: text += f'          - channel: {ch}\n            gain: 0\n            inverted: false\n'
    text += 'filters:\n'
    for ch,name in enumerate(['matrix_ll','matrix_rl','matrix_lr','matrix_rr']): text += conv_text(name,matrix_path,ch)
    text += conv_text('hpcf_left',hpcf_path,0) + conv_text('hpcf_right',hpcf_path,1) + gain_text(preamp_db)
    text += 'pipeline:\n  - type: Mixer\n    name: matrix_branches\n'
    for ch,name in enumerate(['matrix_ll','matrix_rl','matrix_lr','matrix_rr']): text += f'  - type: Filter\n    channels: [{ch}]\n    names: [{name}]\n'
    text += '''  - type: Mixer
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
    return CAMILLA_ROOT / (slugify(label) + suffix + '-crossfeed.yml'), text

def configured_path(item):
    path = Path(str(item.get('file',''))).expanduser()
    return (path if path.is_absolute() else HPCF_ANECHOIC / path).resolve()
def smoothed_path(source):
    source = Path(source)
    return HPCF_ANECHOIC / (source.stem.removesuffix(' - Raw') + ' - Smoothed' + source.suffix)
def selected_variants(item, source):
    variants = [(source.stem.endswith(' - Smoothed'), source)]
    if item.get('include_smoothed',False) and not variants[0][0]:
        sibling = smoothed_path(source)
        if sibling.is_file(): variants.append((True,sibling))
        else: print(f'No matching Smoothed HpCF found: {sibling}; continuing with selected HpCF only.')
    return variants
def measurement_devices():
    if not MEASUREMENTS_DIR.is_dir(): raise SystemExit(f'Missing measurement folder: {MEASUREMENTS_DIR}')
    csvs = sorted((p for p in MEASUREMENTS_DIR.iterdir() if p.is_file() and p.suffix.casefold()=='.csv'),key=lambda p:(p.name.casefold(),p.name))
    if not csvs: raise SystemExit(f'No CSV files found in {MEASUREMENTS_DIR}')
    result = [dict(item) for item in HPCFS]
    def key(path): return Path(path).stem.removesuffix(' - Raw').removesuffix(' - Smoothed').casefold()
    configured = {key(configured_path(item)) for item in result}; expected = set()
    for csv_path in csvs:
        name = re.sub(r'[\\/:*?"<>|]+','_',csv_path.stem).strip()
        if not name or name in ('.','..') or any(ord(c)<32 for c in name): raise SystemExit(f'Invalid measurement filename: {csv_path.name}')
        wav_name = name + ' - H5 Approx Anechoic HpCF.wav'
        if wav_name.casefold() in expected: raise SystemExit(f'Measurement output collision: {csv_path.name}')
        expected.add(wav_name.casefold()); source = (HPCF_ANECHOIC / wav_name).resolve()
        if key(source) in configured: continue
        if not source.is_file(): raise SystemExit(f'Missing CSV-derived HpCF: {source}; run the HpCF builder first')
        result.append({'file':wav_name,'label':name,'include_smoothed':True}); configured.add(key(source))
    return result
def build_profile_candidates(all_measurements=False):
    if not H5_WEIGHTED.is_file(): raise SystemExit(f'Missing measured H5 weighted stereo average: {H5_WEIGHTED}')
    if not H5_MATRIX.is_file(): raise SystemExit(f'Missing measured H5 hemisphere matrix: {H5_MATRIX}')
    if not HPCF_ANECHOIC.is_dir(): raise SystemExit(f'Missing anechoic HpCF folder: {HPCF_ANECHOIC}')
    devices=[]; labels=set(); names={'00-filterless.yml'}; sources=set(); generated=[]
    for item in (measurement_devices() if all_measurements else HPCFS):
        label=str(item.get('label','')).strip(); source=configured_path(item)
        if not label: raise SystemExit(f'Invalid HPCFS entry: {item!r}')
        if not source.is_file() or source.suffix.casefold()!='.wav': raise SystemExit(f'Missing anechoic HpCF WAV: {source}')
        if label.casefold() in labels or os.path.normcase(str(source)) in sources: raise SystemExit(f'Duplicate HpCF device: {label} / {source}')
        labels.add(label.casefold()); sources.add(os.path.normcase(str(source)))
        variants=selected_variants(item,source)
        for smoothed,variant in variants:
            read_fir(variant,2); variant_item=dict(item,_smoothed=smoothed)
            base=slugify(profile_label(variant_item)); suffix='-smoothed' if smoothed else ''
            filenames={base+suffix+'.yml',base+suffix+'-crossfeed.yml'}
            if not base or filenames & names: raise SystemExit(f'Empty or duplicate profile filename for label: {label}')
            names.update(filenames)
        devices.append((item,source))
        for smoothed,variant in variants:
            variant_item=dict(item,_smoothed=smoothed)
            stereo_gain=stereo_cascade_gain(H5_WEIGHTED,variant); crossfeed_gain=matrix_cascade_gain(H5_MATRIX,variant)
            generated.extend((make_stereo_profile(len(devices),variant_item,variant,H5_WEIGHTED,stereo_gain),make_crossfeed_profile(len(devices),variant_item,variant,H5_MATRIX,crossfeed_gain)))
    return [(CAMILLA_ROOT/'00-filterless.yml',filterless_text()),*generated],devices

def commit_profiles(candidates):
    CAMILLA_ROOT.mkdir(parents=True,exist_ok=True); expected={}
    for path,text in candidates:
        path=Path(path)
        if path.parent!=CAMILLA_ROOT: raise RuntimeError(f'Generated profile escapes output folder: {path}')
        if path in expected: raise RuntimeError(f'Duplicate generated profile filename: {path.name}')
        expected[path]=text
    before={path:path.read_text(encoding='utf-8') for pattern in ('*.yml','*.yaml') for path in CAMILLA_ROOT.rglob(pattern) if path.is_file()}
    descriptions=('H5 2945-point weighted stereo average','H5 2945-point diffuse-field 2x2 matrix','H5 measured-grid weighted stereo average','H5 hemisphere rank-one approximate 2x2 crossfeed matrix','H5 full-sphere hemisphere ear-power 2x2 crossfeed matrix','Pass-through CamillaDSP profile with no filters')
    preserved={path for path,text in before.items() if path not in expected and not any(f'description: "{description}' in text for description in descriptions)}
    before={path:text for path,text in before.items() if path not in preserved}
    with tempfile.TemporaryDirectory(prefix='.profile-commit-',dir=CAMILLA_ROOT.parent) as transaction_name:
        backup=Path(transaction_name)/'backup'; backup.mkdir(); previous=[]
        for old,text in before.items():
            relative=old.relative_to(CAMILLA_ROOT); saved=backup/relative; saved.parent.mkdir(parents=True,exist_ok=True); saved.write_text(text,encoding='utf-8'); previous.append((relative,saved))
        try:
            for path,text in expected.items(): atomic_write(path,text)
            for old in before:
                if old not in expected: old.unlink()
            actual={path for pattern in ('*.yml','*.yaml') for path in CAMILLA_ROOT.rglob(pattern) if path.is_file()}
            if actual!=set(expected)|preserved: raise RuntimeError('Profile verification path mismatch')
            for path,text in expected.items():
                if path.read_text(encoding='utf-8')!=text: raise RuntimeError(f'Profile verification failed: {path}')
        except BaseException as error:
            restore_errors=[]
            for pattern in ('*.yml','*.yaml'):
                for partial in CAMILLA_ROOT.rglob(pattern):
                    if partial.is_file() and partial not in preserved:
                        try: partial.unlink()
                        except OSError as e: restore_errors.append(f'remove {partial}: {e}')
            for relative,saved in previous:
                destination=CAMILLA_ROOT/relative
                try: destination.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(saved,destination)
                except OSError as e: restore_errors.append(f'restore {destination}: {e}')
            if restore_errors: raise RuntimeError('Profile commit failed and rollback was incomplete: '+'; '.join(restore_errors)) from error
            raise RuntimeError('Profile commit failed; all previous YAML profiles were restored: '+str(error)) from error
    return [path for path,text in expected.items() if before.get(path)!=text],[path for path in before if path not in expected]
def rebuild_all_filters(all_measurements=False):
    candidates,devices=build_profile_candidates(all_measurements)
    camilladsp=Path('/run/current-system/sw/bin/camilladsp')
    if not camilladsp.is_file(): raise SystemExit(f'Missing CamillaDSP executable: {camilladsp}')
    with tempfile.TemporaryDirectory() as validation_name:
        for number,(path,text) in enumerate(candidates):
            candidate=Path(validation_name)/f'{number:04d}-{path.name}'; candidate.write_text(text,encoding='utf-8')
            check=subprocess.run([str(camilladsp),'--check',str(candidate)],text=True,capture_output=True)
            if check.returncode: raise SystemExit(f'Invalid generated profile {path.name}: '+(check.stderr.strip() or check.stdout.strip()))
    existed={path for path,text in candidates if path.is_file()}; changed,removed=commit_profiles(candidates)
    for path,text in candidates:
        print(f'{"Replaced existing" if path in existed else "Created"}: {path}')
        match=re.search(r'  auto_gain:\n    type: Gain\n    parameters:\n      gain: ([^\n]+)',text)
        if match: print(f'{path.name}: sample-safe preamp {match.group(1)} dB')
    print(f'Using {len(devices)} anechoic HpCF devices; verified {len(candidates)} CamillaDSP YAML profiles in {CAMILLA_ROOT}; {len(changed)} changed, {len(removed)} obsolete removed')

TERMINALS=(('foot','-T','Manage Audio Device'),('kitty','--title','Manage Audio Device'),('alacritty','--title','Manage Audio Device','-e'))
def row(number,label):
    prefix=f'[{number}] '; width=max(1,shutil.get_terminal_size((80,24)).columns-len(prefix))
    lines=textwrap.wrap(str(label),width=width,break_long_words=True,break_on_hyphens=False) or ['']
    print(prefix+lines[0])
    for line in lines[1:]: print(' '*len(prefix)+line)
def choose(title,choices):
    print(f'\n{title}'); row(0,'Exit without saving')
    for number,label in enumerate(choices,1): row(number,label)
    while True:
        answer=input('Select:').strip()
        if answer=='0': raise KeyboardInterrupt
        if answer.isdecimal() and 1<=int(answer)<=len(choices): return int(answer)-1
        print('Select an available number.')
def ask(label,default=''):
    suffix=f' [{default}]' if default else ''
    while True:
        value=input(f'{label}{suffix} (0 to exit): ').strip()
        if value=='0': raise KeyboardInterrupt
        value=value or default
        if value and '\n' not in value and '\r' not in value: return value
        print(f'{label} is required.')
def ask_edit(field,current): return choose(f'{field} current: {current}. Edit?',('No','Yes'))==1
def ask_smoothed(source):
    if Path(source).stem.endswith(' - Smoothed'):
        print('Selected file is already Smoothed; adding that variant only.'); return False
    enabled=choose('Search the main HpCF folder for the matching Smoothed file and add its filters?',('No','Yes'))==1
    if enabled:
        sibling=smoothed_path(source)
        print(f'Found Smoothed HpCF: {sibling}' if sibling.is_file() else f'No matching Smoothed HpCF found: {sibling}; continuing with selected HpCF only.')
    return enabled
def load(text):
    node=next((n for n in ast.parse(text).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HPCFS' for t in n.targets)),None)
    if node is None: raise RuntimeError('HPCFS tuple was not found in add-audio-device.py')
    entries=ast.literal_eval(node.value)
    if not isinstance(entries,tuple) or not all(isinstance(item,dict) for item in entries): raise RuntimeError('HPCFS must be a tuple of dictionaries')
    return node,list(entries)
def render(entries):
    lines=['HPCFS = (']
    for item in entries:
        lines += ['    {',f"        'file': {str(item['file'])!r},",f"        'label': {str(item['label'])!r},",f"        'include_smoothed': {bool(item.get('include_smoothed',False))!r},",'    },']
    return '\n'.join([*lines,')'])
def replace_block(text,node,entries):
    lines=text.splitlines(keepends=True); lines[node.lineno-1:node.end_lineno]=[render(entries)+'\n']; result=''.join(lines); ast.parse(result); return result
def new_entry():
    value=ask('Raw HpCF WAV filename from hpcf anechoic, or absolute path'); candidate=Path(value).expanduser()
    path=(candidate if candidate.is_absolute() else HPCF_ANECHOIC/candidate).resolve()
    if not path.is_file(): raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav': raise RuntimeError('HpCF must be a WAV file')
    label=ask('Profile name'); enabled=ask_smoothed(path)
    return {'file':path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path),'label':label,'resolved':path,'include_smoothed':enabled}
def validate_unique(entries,skip=None):
    labels=set(); paths=set()
    for index,item in enumerate(entries):
        if index==skip: continue
        label=str(item.get('label','')).strip().casefold(); path=os.path.normcase(os.path.realpath(configured_path(item)))
        if label in labels: raise RuntimeError(f'Duplicate device label: {item.get("label")}')
        if path in paths: raise RuntimeError(f'Duplicate HpCF file: {configured_path(item)}')
        labels.add(label); paths.add(path)
def save(before,after):
    with tempfile.TemporaryDirectory() as directory:
        candidate=Path(directory)/CONFIG_SCRIPT.name; candidate.write_text(after,encoding='utf-8'); subprocess.run([sys.executable,'-m','py_compile',str(candidate)],check=True)
    backup=BACKUP_DIR/datetime.now().strftime('%Y%m%d-%H%M%S-%f'); backup.mkdir(parents=True,exist_ok=False); shutil.copy2(CONFIG_SCRIPT,backup/CONFIG_SCRIPT.name)
    temporary=CONFIG_SCRIPT.with_name('.'+CONFIG_SCRIPT.name+'.new')
    try:
        temporary.write_text(after,encoding='utf-8'); os.chmod(temporary,CONFIG_SCRIPT.stat().st_mode); os.replace(temporary,CONFIG_SCRIPT)
        subprocess.run([sys.executable,'-m','py_compile',str(CONFIG_SCRIPT)],check=True)
    except Exception: temporary.unlink(missing_ok=True); shutil.copy2(backup/CONFIG_SCRIPT.name,CONFIG_SCRIPT); raise
    return backup
def rebuild_after_change(entries,backup):
    global HPCFS
    previous=HPCFS; HPCFS=tuple(dict(item) for item in entries)
    try: rebuild_all_filters()
    except BaseException as error:
        HPCFS=previous; shutil.copy2(backup/CONFIG_SCRIPT.name,CONFIG_SCRIPT); subprocess.run([sys.executable,'-m','py_compile',str(CONFIG_SCRIPT)],check=True)
        raise RuntimeError('Filter update failed; device configuration restored from backup. Cause: '+(str(error).strip() or error.__class__.__name__)) from error
    print('All CamillaDSP filters updated.')
def update_filters(_text=None):
    print('\nUpdating all CamillaDSP filters...'); rebuild_all_filters(); print('All CamillaDSP filters updated.')
def clean_devices(text):
    node,entries=load(text); print('\nClean devices review'); row(1,f'Configured devices to remove: {len(entries)}'); row(2,f'Anechoic HpCF folder: {HPCF_ANECHOIC}'); row(3,f'CamillaDSP YAML folder: {CAMILLA_ROOT}'); row(4,'Only filterless is regenerated; unrelated YAMLs are preserved')
    if choose('Remove every configured device and generated filter?',('No','Yes'))==0: print('No files changed.'); return
    backup=save(text,replace_block(text,node,[])); rebuild_after_change([],backup)
    print(f'Removed {len(entries)} configured devices. Backup: {backup}')
def add(text):
    node,entries=load(text); entry=new_entry(); trial=[*entries,{key:entry[key] for key in ('file','label','include_smoothed')}]; validate_unique(trial)
    print('\nAdd review'); row(1,f"HpCF: {entry['resolved']}"); row(2,f"Profile name: {entry['label']}"); row(3,f"Search/include matching Smoothed filters: {entry['include_smoothed']}")
    if choose('Save this device?',('No','Yes'))==0: return
    backup=save(text,replace_block(text,node,trial)); print(f"Added {entry['label']}. Backup: {backup}"); rebuild_after_change(trial,backup)
def edit(text):
    node,entries=load(text)
    if not entries: raise RuntimeError('No configured audio devices to edit')
    index=choose('Edit audio device',[f"{x.get('label')} | {x.get('file')}" for x in entries]); current=entries[index]; path=configured_path(current); label=str(current.get('label','')).strip()
    if ask_edit('HpCF',str(path)):
        candidate=Path(ask('HpCF WAV filename or absolute path',str(path))).expanduser(); path=(candidate if candidate.is_absolute() else HPCF_ANECHOIC/candidate).resolve()
    if ask_edit('Profile name',label): label=ask('Profile name',label)
    if not path.is_file(): raise RuntimeError(f'HpCF file does not exist: {path}')
    if path.suffix.casefold()!='.wav': raise RuntimeError('HpCF must be a WAV file')
    enabled=ask_smoothed(path); stored=path.name if path.parent==HPCF_ANECHOIC.resolve() else str(path)
    trial=list(entries); trial[index]={'file':stored,'label':label,'include_smoothed':enabled}; validate_unique(trial)
    print('\nEdit review'); row(1,f'HpCF: {path}'); row(2,f'Profile name: {label}'); row(3,f'Search/include matching Smoothed filters: {enabled}')
    if choose('Save these edits?',('No','Yes'))==0: print('No files changed.'); return
    backup=save(text,replace_block(text,node,trial)); print(f'Edited {label}. Backup: {backup}'); rebuild_after_change(trial,backup)
def remove(text):
    node,entries=load(text)
    if not entries: raise RuntimeError('No configured audio devices to remove')
    index=choose('Remove audio device',[f"{x.get('label')} | {x.get('file')}" for x in entries]); removed=entries.pop(index)
    print('\nRemove review'); row(1,f'HpCF: {configured_path(removed)}'); row(2,f"Profile name: {removed.get('label')}")
    if choose('Remove this device?',('No','Yes'))==0: return
    backup=save(text,replace_block(text,node,entries)); print(f"Removed {removed.get('label')}. Backup: {backup}"); rebuild_after_change(entries,backup)
def relaunch():
    for command in TERMINALS:
        executable=shutil.which(command[0])
        if executable: subprocess.Popen([executable,*command[1:],sys.executable,str(Path(__file__).resolve())]); return
    raise RuntimeError('No supported terminal found: foot, kitty or alacritty')
def pause():
    print('\nPress any key to close...',end='',flush=True)
    if sys.stdin.isatty():
        fd=sys.stdin.fileno(); settings=termios.tcgetattr(fd)
        try: tty.setraw(fd); os.read(fd,1)
        finally: termios.tcsetattr(fd,termios.TCSADRAIN,settings)
    print()
def manager_main():
    text=CONFIG_SCRIPT.read_text(encoding='utf-8'); actions=(add,edit,remove,clean_devices,update_filters)
    choice=choose('Audio device manager',('Add device','Edit device','Remove device','Clean devices','Update all filters')); actions[choice](text)
if __name__=='__main__':
    if sys.argv[1:] in (['--rebuild'],['--rebuild-all']): rebuild_all_filters(all_measurements=sys.argv[1:]==['--rebuild-all']); raise SystemExit(0)
    if sys.argv[1:]: raise SystemExit('Usage: add-audio-device.py [--rebuild | --rebuild-all]')
    if not (sys.stdin.isatty() and sys.stdout.isatty()): relaunch(); raise SystemExit(0)
    status=0
    try: manager_main()
    except KeyboardInterrupt: print('\nCancelled.'); status=130
    except Exception as error: print(f'\nError: {error}',file=sys.stderr); status=1
    finally: shutil.rmtree(SCRIPT_DIR/'__pycache__',ignore_errors=True); pause()
    raise SystemExit(status)
