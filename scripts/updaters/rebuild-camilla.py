#!/usr/bin/env python3

import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 96000
N_FFT = 65536
CAMILLA_PLAYBACK_DEVICE = "hw:Loopback,0,1"
CAMILLA_PLAYBACK_FORMAT = "S32_LE"
# Customize devices here. Each entry is: filename, profile label, and IE/OE class.
CAMILLA_ROOT = Path.home() / 'Documents/prefs/audio-filters'
BRIR_ROOT = CAMILLA_ROOT
HPCFS = (
    {
        'file': 'Apple_EarPods_Ahastyle_Covers_Custom_Average_A+B.wav',
        'label': 'Apple EarPods',
        'kind': 'IE',
    },
    {
        'file': 'HyperX_Cloud_III_Average.wav',
        'label': 'HyperX Cloud III',
        'kind': 'OE',
    },
    {
        'file': 'CMF_by_Nothing_Buds_Pro_2_Sample_A.wav',
        'label': 'cmf-buds-pro-2',
        'kind': 'IE',
    },
)
FOLDER_PATTERN = re.compile(r'^\((\d+)ms-(IE|OE)\)(?:\s+(.+))?$', re.I)

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
    path.write_text(f'''---
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
''', encoding='utf-8')
    return path


def main():
    if not CAMILLA_ROOT.is_dir() or not PROFILES:
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
    for profile in PROFILES:
        brir = profile['path'] / 'BRIR_True_Stereo.wav'
        if not brir.is_file():
            raise SystemExit(f'Missing BRIR: {brir}')
        if not any(device['kind'] == profile['kind'] for device in devices):
            raise SystemExit(f"No {profile['kind']} HpCF configured for {profile['folder']}")
    hf = load_required_ash_helpers()
    generated = []
    for profile in PROFILES:
        brir = profile['path'] / 'BRIR_True_Stereo.wav'
        room_key = (int(profile['milliseconds']), profile['room'].casefold())
        for device in devices:
            if device['kind'] != profile['kind']:
                continue
            *_, gain = calculate_gain(brir, device['path'], hf)
            generated.append(make_camilladsp_profile(
                PROFILE_NUMBERS[room_key], profile, gain, brir,
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
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        for path, text in generated:
            candidate = temporary / path.name
            candidate.write_text(text, encoding='utf-8')
            check = subprocess.run([str(camilladsp), '--check', str(candidate)], text=True,
                                   capture_output=True)
            if check.returncode:
                raise SystemExit(f'Invalid generated profile {path.name}: '+
                                 (check.stderr.strip() or check.stdout.strip()))
    # Validate every source before deleting stale generated YAML files.
    for path in CAMILLA_ROOT.rglob('*'):
        if path.is_file() and path.suffix.lower() in ('.yml', '.yaml'):
            path.unlink()
    output = [write_filterless_camilladsp_profile()]
    for path, text in generated:
        path.write_text(text, encoding='utf-8')
        output.append(path)
    assert len(output) == 1 + len(generated)
    print(f'Rebuilt {len(output)} CamillaDSP YAML profiles in {CAMILLA_ROOT}')

if __name__ == '__main__':
    main()
