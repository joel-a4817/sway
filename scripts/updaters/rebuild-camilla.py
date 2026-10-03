#!/usr/bin/env python3

import re
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 96000
N_FFT = 65536
CAMILLA_PLAYBACK_DEVICE = "hw:Loopback,0,1"
CAMILLA_PLAYBACK_FORMAT = "S32_LE"
BRIR_ROOT = Path.home() / 'Documents/prefs/audio-filters'
EARPODS_HPCF = BRIR_ROOT / 'Apple_EarPods_Ahastyle_Covers_Custom_Average_A+B.wav'
CLOUD3_HPCF = BRIR_ROOT / 'HyperX_Cloud_III_Average.wav'
CMF_BUDS_PRO_2_HPCF = BRIR_ROOT / "CMF_by_Nothing_Buds_Pro_2_Sample_A.wav"
CAMILLA_ROOT = BRIR_ROOT

# Discover every BRIR profile folder automatically and sort by the
# millisecond value at the start of its name, from least to most reverb.
def profile_sort_key(folder_name):
    match = re.match(r'^\((\d+)ms\)', folder_name)
    if match is None:
        raise SystemExit(
            f'BRIR folder name does not start with "(NNNNms)": '
            f'{folder_name}'
        )
    return int(match.group(1)), folder_name.casefold()

def discover_profiles():
    if not BRIR_ROOT.is_dir():
        raise SystemExit(f'Missing audio filter directory: {BRIR_ROOT}')
    return sorted((p.name for p in BRIR_ROOT.iterdir()
                   if p.is_dir() and (p / 'BRIR_True_Stereo.wav').is_file()),
                  key=profile_sort_key)

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

def make_camilladsp_profile(
    index,
    folder_name,
    gain,
    brir,
    hpcf,
    device,
    title_device,
):
    slug = slugify(folder_name)

    filename = (
        f"{index:02d}-{device}-{slug}.yml"
    )

    output_path = BRIR_ROOT / folder_name / filename

    gain_text = f"{gain:.17f}"
    brir_text = yaml_string(brir)
    hpcf_text = yaml_string(hpcf)

    title = yaml_string(
        f"{title_device} ASH - {folder_name}"
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

def main():
    folders = discover_profiles()
    hf = load_required_ash_helpers()
    expected = {write_filterless_camilladsp_profile()}
    for folder in folders:
        brir = BRIR_ROOT / folder / 'BRIR_True_Stereo.wav'
        # OE uses the over-ear calibration; IE and all other rooms offer both in-ear calibrations.
        variants = (('cloud3', 'HyperX Cloud III', CLOUD3_HPCF),) if folder.casefold().endswith('(oe)') else (
            ('cmf-buds-pro-2', 'CMF Buds Pro 2', CMF_BUDS_PRO_2_HPCF),
            ('earpods', 'Apple EarPods', EARPODS_HPCF))
        for device, title, hpcf in variants:
            if not hpcf.is_file():
                raise SystemExit(f'Missing HpCF: {hpcf}')
            *_, gain = calculate_gain(brir, hpcf, hf)
            path, config = make_camilladsp_profile(
                index=folders.index(folder)+1, folder_name=folder, gain=gain,
                brir=brir, hpcf=hpcf, device=device, title_device=title)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config, encoding='utf-8')
            expected.add(path)
    for path in sorted(expected):
        import subprocess
        result = subprocess.run(['camilladsp', '--check', str(path)], capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(f'Invalid profile {path}: {result.stderr or result.stdout}')
    legacy = BRIR_ROOT / 'camilladsp'
    if legacy.is_dir():
        # Do not delete the old directory: the previous engine may still be using it.
        print(f'Old flat profiles remain in {legacy}; switch to the new server before removing them.')
    print(f'Validated {len(expected)} profiles in BRIR folders under {BRIR_ROOT}')

def write_filterless_camilladsp_profile():
    CAMILLA_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = CAMILLA_ROOT / "00-filterless.yml"
    output_path.write_text(f"""---
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
""", encoding="utf-8")
    return output_path

if __name__ == "__main__":
    main()
