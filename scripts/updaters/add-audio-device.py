#!/usr/bin/env python3
"""Manage anechoic HpCF devices and rebuild sample-safe H5 CamillaDSP profiles."""
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
    namespace = {'Path': Path, 'HOME': HOME}
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
MEASUREMENTS_DIR = AUDIO_ROOT / 'measurements'
H5_WEIGHTED = AUDIO_ROOT / 'sofa average/H5_2945_Weighted_Power_Average_UNNORMALIZED.wav'
H5_MATRIX = AUDIO_ROOT / 'sofa average/H5_2945_Avg_Crossfeed_4ch_FLOAT.wav'
HPCFS = (
    {
        'file': 'Apple EarPods + AhaStyle - Approximate B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'earpods + silicone tips',
    },
    {
        'file': 'Apple EarPods - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'earpods',
    },
    {
        'file': 'CMF Buds Pro 2 - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'cmf buds pro 2',
    },
    {
        'file': 'HyperX Cloud III - B&K 5128 - H5 Approx Anechoic HpCF.wav',
        'label': 'cloud iii',
    },
)
SAMPLE_RATE = 96000
CAMILLA_PLAYBACK_DEVICE = 'hw:Loopback,0,1'
CAMILLA_PLAYBACK_FORMAT = 'S32_LE'
CAMILLA_ROOT = AUDIO_FILTERS
# Positive S32_LE maximum; reserve one output LSB instead of aiming above it.
OUTPUT_SAMPLE_CEILING = 1.0 - 2.0 ** -31
# Tiny numerical guard for float64 FFT/summation, not an arbitrary dB margin.
BOUND_NUMERICAL_GUARD = 1e-9

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

def response_peak_preamp_db(peak_linear, description):
    """Highest nonpositive gain for a sample bound normalized to PCM limits.

    Default float64 processing, no extra digital boost, and the generated S32_LE
    capture/playback formats are assumed. Rounding is always toward safety.
    """
    peak_linear = float(peak_linear)
    if not np.isfinite(peak_linear) or peak_linear <= 0.0:
        raise RuntimeError(f'{description} has an invalid sample bound')
    guarded = np.nextafter(peak_linear * (1.0 + BOUND_NUMERICAL_GUARD), np.inf)
    exact = min(0.0, -20.0 * np.log10(guarded))
    gain = math.floor(exact * 1_000_000_000_000.0) / 1_000_000_000_000.0
    if guarded * 10.0 ** (gain / 20.0) > 1.0:
        raise RuntimeError(f'{description}: rounded preamp fails the sample bound')
    return gain


def dense_response_nfft(*lengths):
    """Use a dense zero-padded FFT grid for practical response peaks."""
    longest = max(int(length) for length in lengths)
    minimum = max(65536, longest * 16)
    return 1 << (minimum - 1).bit_length()


def convolve_fir(left, right):
    """Full linear convolution using NumPy FFTs; no extra runtime dependency."""
    length = len(left) + len(right) - 1
    nfft = 1 << (length - 1).bit_length()
    return np.fft.irfft(
        np.fft.rfft(left, n=nfft) * np.fft.rfft(right, n=nfft), n=nfft
    )[:length]

def read_fir(path, channels):
    data, rate = sf.read(path, always_2d=True, dtype='float64')
    if rate != SAMPLE_RATE or data.shape[1] != channels or not len(data):
        raise RuntimeError(f'{path}: expected nonempty {channels}-channel {SAMPLE_RATE} Hz FIR')
    if not np.isfinite(data).all():
        raise RuntimeError(f'{path}: non-finite FIR samples')
    return data


def row_sample_extrema(kernels):
    """Exact signed row extrema for S32_LE input samples in [-1, 1-2^-31].

    Each input channel can independently attain its coefficient-sign-aligned
    worst case. Use the FINAL cascade, not a product of separate filter bounds.
    """
    positive_sums = []
    negative_sums = []
    for kernel in kernels:
        if not np.isfinite(kernel).all():
            raise RuntimeError('Final cascade has non-finite samples')
        positive_sums.append(math.fsum(float(v) for v in kernel if v > 0.0))
        negative_sums.append(math.fsum(float(-v) for v in kernel if v < 0.0))
    positive = math.fsum(positive_sums)
    negative = math.fsum(negative_sums)
    maximum = math.fsum((OUTPUT_SAMPLE_CEILING * positive, negative))
    minimum_abs = math.fsum((positive, OUTPUT_SAMPLE_CEILING * negative))
    return maximum, minimum_abs


def sample_safe_preamp(rows, description):
    """Tight static sample protection, with only numerical/serialization guard."""
    extrema = [row_sample_extrema(kernels) for kernels in rows]
    peak = max(maximum / OUTPUT_SAMPLE_CEILING
               for maximum, minimum_abs in extrema)
    peak = max(peak, max(minimum_abs for maximum, minimum_abs in extrema))
    return response_peak_preamp_db(peak, description)


def report_cascade_headroom(rows, description, gain):
    """Distinguish sinusoidal graph peaks from all-input sample protection."""
    nfft = dense_response_nfft(*(len(k) for row in rows for k in row))
    frequencies = np.fft.rfftfreq(nfft, 1.0 / SAMPLE_RATE)
    audible = (frequencies >= 20.0) & (frequencies <= 20000.0)
    full_peak = band_peak = 0.0
    for row in rows:
        # Independent input channels can align phases at each frequency.
        response = sum(np.abs(np.fft.rfft(k, n=nfft)) for k in row)
        full_peak = max(full_peak, float(np.max(response)))
        band_peak = max(band_peak, float(np.max(response[audible])))
    print(f'{description}: 20-20000 Hz sine/row peak '
          f'{20*np.log10(band_peak):.6f} dB; full-band sine/row peak '
          f'{20*np.log10(full_peak):.6f} dB; sample-safe gain {gain:.12f} dB')

def stereo_cascade_gain(h5_path, hpcf_path):
    h5 = read_fir(h5_path, 2)
    hp = read_fir(hpcf_path, 2)
    rows = [[convolve_fir(h5[:, c], hp[:, c])] for c in range(2)]
    gain = sample_safe_preamp(rows, 'Final stereo PCM sample bound')
    report_cascade_headroom(rows, str(hpcf_path) + ' stereo', gain)
    return gain


def matrix_cascade_gain(matrix_path, hpcf_path):
    matrix = read_fir(matrix_path, 4)
    hp = read_fir(hpcf_path, 2)
    kernels = [convolve_fir(matrix[:, c], hp[:, 0 if c < 2 else 1]) for c in range(4)]
    rows = [kernels[:2], kernels[2:]]
    gain = sample_safe_preamp(rows, 'Final matrix PCM sample bound')
    report_cascade_headroom(rows, str(hpcf_path) + ' crossfeed', gain)
    return gain


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
    filename = f"{slugify(label)}{'-raw' if item.get('_raw') else ''}.yml"
    title = yaml_string(label + (' - Raw' if item.get('_raw') else ''))
    h5 = yaml_string(field_path)
    hpcf = yaml_string(hpcf_path)
    config = f'''---
title: {title}
description: "H5 measured-grid weighted stereo average with anechoic headphone correction; no crossfeed"
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
      gain: {preamp_db:.12f}
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
    filename = f"{slugify(label)}{'-raw' if item.get('_raw') else ''}-crossfeed.yml"
    title = yaml_string(label + (' - Raw' if item.get('_raw') else '') + ' - Crossfeed')
    matrix = yaml_string(matrix_path)
    hpcf = yaml_string(hpcf_path)
    config = f'''---
title: {title}
description: "H5 full-sphere hemisphere ear-power 2x2 crossfeed matrix with anechoic headphone correction"
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
      gain: {preamp_db:.12f}
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
    """Atomically replace generated profiles and verify every final byte."""
    CAMILLA_ROOT.mkdir(parents=True, exist_ok=True)
    expected = {}
    for path, text in candidates:
        path = Path(path)
        if path.parent != CAMILLA_ROOT:
            raise RuntimeError(f'Generated profile escapes output folder: {path}')
        if path in expected:
            raise RuntimeError(f'Duplicate generated profile filename: {path.name}')
        expected[path] = text
    before = {
        path: path.read_text(encoding='utf-8')
        for pattern in ('*.yml', '*.yaml')
        for path in CAMILLA_ROOT.rglob(pattern)
        if path.is_file()
    }
    # Preserve YAMLs not produced by this manager, including nested room profiles.
    generated_descriptions = (
        'H5 2945-point weighted stereo average',
        'H5 2945-point diffuse-field 2x2 matrix',
        'H5 measured-grid weighted stereo average',
        'H5 hemisphere rank-one approximate 2x2 crossfeed matrix',
        'H5 full-sphere hemisphere ear-power 2x2 crossfeed matrix',
        'Pass-through CamillaDSP profile with no filters',
    )
    preserved = {
        path for path, text in before.items()
        if path not in expected and not any(
            f'description: "{description}' in text
            for description in generated_descriptions
        )
    }
    before = {path: text for path, text in before.items() if path not in preserved}
    with tempfile.TemporaryDirectory(
        prefix='.profile-commit-', dir=CAMILLA_ROOT.parent
    ) as transaction_name:
        transaction = Path(transaction_name)
        backup = transaction / 'backup'
        backup.mkdir()
        previous = []
        for old, old_text in before.items():
            relative = old.relative_to(CAMILLA_ROOT)
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(old_text, encoding='utf-8')
            previous.append((relative, saved))
        try:
            # Existing targets are replaced atomically in place, never skipped.
            # Remove only obsolete manager-generated paths after replacements.
            for path, text in expected.items():
                atomic_write(path, text)
            for old in before:
                if old not in expected:
                    old.unlink()
            actual_paths = {
                path
                for pattern in ('*.yml', '*.yaml')
                for path in CAMILLA_ROOT.rglob(pattern)
                if path.is_file()
            }
            if actual_paths != set(expected) | preserved:
                missing = sorted(str(path) for path in (set(expected) | preserved) - actual_paths)
                extra = sorted(str(path) for path in actual_paths - (set(expected) | preserved))
                raise RuntimeError(
                    f'Profile verification path mismatch; missing={missing}, extra={extra}'
                )
            for path, text in expected.items():
                if path.read_text(encoding='utf-8') != text:
                    raise RuntimeError(f'Profile verification failed: {path}')
        except BaseException as commit_error:
            restore_errors = []
            for pattern in ('*.yml', '*.yaml'):
                for partial in CAMILLA_ROOT.rglob(pattern):
                    if partial.is_file() and partial not in preserved:
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
    changed = [path for path, text in expected.items() if before.get(path) != text]
    removed = [path for path in before if path not in expected]
    return changed, removed

def measurement_devices():
    """Configured labels plus every CSV-derived HpCF, without editing HPCFS.

    Ignore stale/unrelated WAVs. Expected WAV names use the builder's existing
    safe-name rule, verified against the builder in the regression tests.
    """
    if not MEASUREMENTS_DIR.is_dir():
        raise SystemExit(f'Missing measurement folder: {MEASUREMENTS_DIR}')
    csvs = sorted((p for p in MEASUREMENTS_DIR.iterdir()
                   if p.is_file() and p.suffix.casefold() == '.csv'),
                  key=lambda p: (p.name.casefold(), p.name))
    if not csvs:
        raise SystemExit(f'No CSV files found in {MEASUREMENTS_DIR}')
    result = [dict(item) for item in HPCFS]
    configured = {str(configured_path(item)) for item in result}
    expected_names = set()
    for csv_path in csvs:
        name = re.sub(r'[\\/:*?"<>|]+', '_', csv_path.stem).strip()
        if not name or name in ('.', '..') or any(ord(c) < 32 for c in name):
            raise SystemExit(f'Invalid measurement filename: {csv_path.name}')
        wav_name = name + ' - H5 Approx Anechoic HpCF.wav'
        if wav_name.casefold() in expected_names:
            raise SystemExit(f'Measurement output collision: {csv_path.name}')
        expected_names.add(wav_name.casefold())
        source = (HPCF_ANECHOIC / wav_name).resolve()
        if not source.is_file():
            raise SystemExit(f'Missing CSV-derived HpCF: {source}; run the HpCF builder first')
        if str(source) not in configured:
            result.append({'file': wav_name, 'label': name})
            configured.add(str(source))
    return result


def paired_hpcf_paths(source):
    """A normal WAV and its explicit Raw sibling, including custom paths."""
    source = Path(source)
    if source.stem.endswith(' - Raw'):
        return source.with_name(source.stem[:-6] + source.suffix), source
    return source, source.with_name(source.stem + ' - Raw' + source.suffix)

def build_profile_candidates(all_measurements=False):
    if not H5_WEIGHTED.is_file():
        raise SystemExit(f'Missing measured H5 weighted stereo average: {H5_WEIGHTED}')
    if not H5_MATRIX.is_file():
        raise SystemExit(f'Missing measured H5 hemisphere matrix: {H5_MATRIX}')
    if not HPCF_ANECHOIC.is_dir():
        raise SystemExit(f'Missing anechoic HpCF folder: {HPCF_ANECHOIC}')
    devices = []
    labels = set()
    profile_names = {'00-filterless.yml'}
    sources = set()
    for item in (measurement_devices() if all_measurements else HPCFS):
        label = str(item.get('label', '')).strip()
        source = configured_path(item)
        if not label:
            raise SystemExit(f'Invalid HPCFS entry: {item!r}')
        if not source.is_file() or source.suffix.casefold() != '.wav':
            raise SystemExit(f'Missing anechoic HpCF WAV: {source}')
        if label.casefold() in labels or os.path.normcase(str(source)) in sources:
            raise SystemExit(f'Duplicate HpCF device: {label} / {source}')
        profile_slug = slugify(label)
        filenames = {profile_slug + suffix for suffix in
                     ('.yml', '-crossfeed.yml', '-raw.yml', '-raw-crossfeed.yml')}
        if not profile_slug or filenames & profile_names:
            raise SystemExit(f'Empty or duplicate profile filename for label: {label}')
        profile_names.update(filenames)
        labels.add(label.casefold())
        sources.add(os.path.normcase(str(source)))
        normal, raw = paired_hpcf_paths(source)
        for variant in (normal, raw):
            if not variant.is_file():
                raise SystemExit(f'Missing paired HpCF: {variant}; run the updated HpCF builder first')
            read_fir(variant, 2)
        devices.append((item, normal))
    generated = []
    for index, (item, source) in enumerate(devices, 1):
        normal, raw_source = paired_hpcf_paths(source)
        for raw, variant in ((False, normal), (True, raw_source)):
            variant_item = dict(item, _raw=raw)
            # Assess the actual complete cascade, not separate filter bounds.
            # Flat-endpoint HpCFs still receive full-band sample protection:
            # ignoring ultrasonic clipping would not protect audible samples.
            stereo_gain = stereo_cascade_gain(H5_WEIGHTED, variant)
            crossfeed_gain = matrix_cascade_gain(H5_MATRIX, variant)
            generated.extend((
                make_stereo_profile(index, variant_item, variant, H5_WEIGHTED, stereo_gain),
                make_crossfeed_profile(index, variant_item, variant, H5_MATRIX, crossfeed_gain),
            ))
    candidates = [(CAMILLA_ROOT / '00-filterless.yml', filterless_text()), *generated]
    return candidates, devices


def rebuild_all_filters(all_measurements=False):
    candidates, devices = build_profile_candidates(all_measurements)
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
    existed = {path for path, text in candidates if path.is_file()}
    changed, removed = commit_profiles(candidates)
    for path, text in candidates:
        action = 'Replaced existing' if path in existed else 'Created'
        print(f'{action}: {path}')
    for path, text in candidates:
        match = re.search(r'  auto_gain:\n    type: Gain\n    parameters:\n      gain: ([^\n]+)', text)
        if match:
            print(f'{path.name}: sample-safe preamp {match.group(1)} dB')
    print(f'Using {len(devices)} anechoic HpCFs from {HPCF_ANECHOIC} or absolute paths')
    print(
        f'Verified {len(candidates)} CamillaDSP YAML profiles in {CAMILLA_ROOT}; '
        f'{len(changed)} changed, {len(removed)} obsolete removed'
    )
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
    row(4, 'Result: only 00-filterless.yml is regenerated; unrelated YAMLs are preserved')
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
    print('Only 00-filterless.yml remains manager-generated; unrelated YAMLs are preserved.')

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
    if sys.argv[1:] in (['--rebuild'], ['--rebuild-all']):
        rebuild_all_filters(all_measurements=sys.argv[1:] == ['--rebuild-all'])
        raise SystemExit(0)
    if sys.argv[1:]:
        raise SystemExit('Usage: add-audio-device.py [--rebuild | --rebuild-all]')
    if not (sys.stdin.isatty() and sys.stdout.isatty()):relaunch();raise SystemExit(0)
    status=0
    try:manager_main()
    except KeyboardInterrupt:print('\nCancelled.');status=130
    except Exception as error:print(f'\nError: {error}',file=sys.stderr);status=1
    finally:shutil.rmtree(SCRIPT_DIR/'__pycache__',ignore_errors=True);pause()
    raise SystemExit(status)
