#!/usr/bin/env python3

import re
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 96000
N_FFT = 65536
CAMILLA_PLAYBACK_DEVICE = "hw:Loopback,0,1"
CAMILLA_PLAYBACK_FORMAT = "S32_LE"
BRIR_ROOT = Path('/home/joel/Documents/prefs/audio/BRIRs')
EARPODS_HPCF = Path('/home/joel/Documents/prefs/audio/Apple_EarPods_Ahastyle_Covers_Custom_Average_A+B.wav')
CLOUD3_HPCF = Path('/home/joel/Documents/prefs/audio/HyperX_Cloud_III_Average.wav')
CMF_BUDS_PRO_2_HPCF = Path(
    "/home/joel/Documents/prefs/audio/"
    "CMF_by_Nothing_Buds_Pro_2_Sample_A.wav"
)
CAMILLA_ROOT = Path(
    "/home/joel/Documents/prefs/audio/camilladsp"
)

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

PROFILES = sorted(
    (path.name for path in BRIR_ROOT.iterdir() if path.is_dir()),
    key=profile_sort_key,
)
# Reserve 00 for filterless. Both anechoic device variants use 01;
# subsequent reverberant environments advance once per environment.
ANECHOIC_OE = '(0000ms) Anechoic (OE)'
PROFILE_NUMBERS = {
    folder: index for index, folder in enumerate(
        (name for name in PROFILES if name != ANECHOIC_OE), 1
    )
}
if ANECHOIC_OE in PROFILES:
    PROFILE_NUMBERS[ANECHOIC_OE] = 1

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

    output_path = CAMILLA_ROOT / filename

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
    """Reusable CamillaDSP profile rebuild"""
    hf = load_required_ash_helpers()
    report = []
    for index, folder in enumerate(PROFILES):
        cloud3 = folder == '(0000ms) Anechoic (OE)'
        brir = BRIR_ROOT / folder / 'BRIR_True_Stereo.wav'
        hpcf = CLOUD3_HPCF if cloud3 else EARPODS_HPCF
        if not brir.is_file() or not hpcf.is_file():
            raise SystemExit(f'Missing BRIR/HpCF: {brir} / {hpcf}')
        left, right, peak, preamp, gain = calculate_gain(brir, hpcf, hf)
        key = f'{PROFILE_NUMBERS[folder]:02d}-{"cloud3" if cloud3 else "earpods"}-{slugify(folder)}'
        report.append((key, left, right, peak, preamp, gain, brir, hpcf))
    if not CMF_BUDS_PRO_2_HPCF.is_file():
        raise SystemExit(f'Missing CMF Buds Pro 2 HpCF: {CMF_BUDS_PRO_2_HPCF}')
    paths = write_camilladsp_profiles(report)
    active_file = Path('/home/joel/.local/state/sway/audio/camilladsp-webremote/active-profile')
    if active_file.is_file():
        old_name = active_file.read_text(encoding='utf-8').strip()
        for old_index, folder in enumerate(PROFILES):
            if folder == ANECHOIC_OE:
                devices = ('cloud3',)
            else:
                devices = ('earpods', 'cmf-buds-pro-2')
            for device in devices:
                old = f'{old_index:02d}-{device}-{slugify(folder)}.yml'
                new = f'{PROFILE_NUMBERS[folder]:02d}-{device}-{slugify(folder)}.yml'
                if old_name == old and old != new and (CAMILLA_ROOT / new).is_file():
                    temporary = active_file.with_name(active_file.name + '.tmp')
                    temporary.write_text(new + '\n', encoding='utf-8')
                    temporary.replace(active_file)
                    break
    print(f'Rebuilt {len(paths)} CamillaDSP YAML profiles in {CAMILLA_ROOT}')

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

def write_camilladsp_profiles(report):
    CAMILLA_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not CMF_BUDS_PRO_2_HPCF.is_file():
        raise SystemExit(
            f"Missing CMF Buds Pro 2 HpCF: "
            f"{CMF_BUDS_PRO_2_HPCF}"
        )

    hf = load_required_ash_helpers()
    expected_paths = set()

    # Existing EarPods and Cloud III CamillaDSP profiles.
    for (
        index,
        (
            key,
            left,
            right,
            peak,
            preamp,
            gain,
            brir,
            hpcf,
        ),
    ) in enumerate(report):
        folder_name = PROFILES[index]
        cloud3 = folder_name == "(0000ms) Anechoic (OE)"

        device = (
            "cloud3"
            if cloud3
            else "earpods"
        )

        title_device = (
            "HyperX Cloud III"
            if cloud3
            else "Apple EarPods"
        )

        output_path, config = make_camilladsp_profile(
            index=PROFILE_NUMBERS[folder_name],
            folder_name=folder_name,
            gain=gain,
            brir=brir,
            hpcf=hpcf,
            device=device,
            title_device=title_device,
        )

        output_path.write_text(
            config,
            encoding="utf-8",
        )

        expected_paths.add(output_path)

    # CMF Buds Pro 2 profiles for every BRIR except the OE profile.
    for index, folder_name in enumerate(PROFILES):
        if folder_name == "(0000ms) Anechoic (OE)":
            continue

        brir = (
            BRIR_ROOT
            / folder_name
            / "BRIR_True_Stereo.wav"
        )

        if not brir.is_file():
            raise SystemExit(
                f"Missing BRIR: {brir}"
            )

        (
            left,
            right,
            peak,
            preamp,
            gain,
        ) = calculate_gain(
            brir,
            CMF_BUDS_PRO_2_HPCF,
            hf,
        )

        output_path, config = make_camilladsp_profile(
            index=PROFILE_NUMBERS[folder_name],
            folder_name=folder_name,
            gain=gain,
            brir=brir,
            hpcf=CMF_BUDS_PRO_2_HPCF,
            device="cmf-buds-pro-2",
            title_device="CMF Buds Pro 2",
        )

        output_path.write_text(
            config,
            encoding="utf-8",
        )

        expected_paths.add(output_path)

    # Remove only stale profiles managed by this generator.
    managed_pattern = re.compile(
        r"^[0-9]{2}-"
        r"(earpods|cloud3|cmf-buds-pro-2)-"
        r".*\.yml$"
    )

    for existing in CAMILLA_ROOT.glob("*.yml"):
        if (
            managed_pattern.match(existing.name)
            and existing not in expected_paths
        ):
            existing.unlink()

    verify_camilladsp_profiles(
        expected_paths
    )

    filterless_path = write_filterless_camilladsp_profile()
    return sorted(expected_paths | {filterless_path})

def verify_camilladsp_profiles(paths):
    cloud3_profiles = [
        profile_path
        for profile_path in paths
        if "-cloud3-" in profile_path.name
    ]

    earpods_profiles = [
        profile_path
        for profile_path in paths
        if "-earpods-" in profile_path.name
    ]

    cmf_profiles = [
        profile_path
        for profile_path in paths
        if "-cmf-buds-pro-2-" in profile_path.name
    ]

    expected_cloud3 = sum(
        folder == "(0000ms) Anechoic (OE)"
        for folder in PROFILES
    )

    expected_earpods = (
        len(PROFILES) - expected_cloud3
    )

    expected_cmf = sum(
        folder != "(0000ms) Anechoic (OE)"
        for folder in PROFILES
    )

    expected_total = (
        expected_cloud3
        + expected_earpods
        + expected_cmf
    )

    if len(paths) != expected_total:
        raise SystemExit(
            "CamillaDSP verification failed: "
            f"expected {expected_total} profiles, "
            f"got {len(paths)}"
        )

    if len(cloud3_profiles) != expected_cloud3:
        raise SystemExit(
            "CamillaDSP verification failed: "
            f"expected {expected_cloud3} Cloud III profile, "
            f"got {len(cloud3_profiles)}"
        )

    if len(earpods_profiles) != expected_earpods:
        raise SystemExit(
            "CamillaDSP verification failed: "
            f"expected {expected_earpods} EarPods profiles, "
            f"got {len(earpods_profiles)}"
        )

    if len(cmf_profiles) != expected_cmf:
        raise SystemExit(
            "CamillaDSP verification failed: "
            f"expected {expected_cmf} CMF Buds Pro 2 profiles, "
            f"got {len(cmf_profiles)}"
        )

    capture_block = """  capture:
    type: Alsa
    channels: 2
    device: "hw:Loopback,1,0"
    format: S32_LE
"""

    playback_block = f"""  playback:
    type: Alsa
    channels: 2
    device: "{CAMILLA_PLAYBACK_DEVICE}"
    format: {CAMILLA_PLAYBACK_FORMAT}
"""

    pipeline_order = [
        "name: split_true_stereo",
        "names: [brir_FL_left]",
        "names: [brir_FL_right]",
        "names: [brir_FR_left]",
        "names: [brir_FR_right]",
        "name: sum_to_ears",
        "names: [hpcf_left]",
        "names: [hpcf_right]",
    ]

    for profile_path in sorted(paths):
        profile_text = profile_path.read_text(
            encoding="utf-8"
        )

        if "samplerate: 96000" not in profile_text:
            raise SystemExit(
                f"{profile_path}: expected samplerate 96000"
            )

        if "chunksize: 1024" not in profile_text:
            raise SystemExit(
                f"{profile_path}: expected chunksize 1024"
            )

        if capture_block not in profile_text:
            raise SystemExit(
                f"{profile_path}: incorrect capture block; "
                "expected hw:Loopback,1,0 using S32_LE"
            )

        if playback_block not in profile_text:
            raise SystemExit(
                f"{profile_path}: incorrect playback block; "
                f"expected {CAMILLA_PLAYBACK_DEVICE} using "
                f"{CAMILLA_PLAYBACK_FORMAT}"
            )

        if profile_text.count("format: S32_LE") != 2:
            raise SystemExit(
                f"{profile_path}: expected exactly two "
                "S32_LE device formats"
            )

        if "format: S24_3_LE" in profile_text:
            raise SystemExit(
                f"{profile_path}: stale S24_3_LE playback "
                "format found"
            )

        if profile_text.count("filename:") != 6:
            raise SystemExit(
                f"{profile_path}: expected six convolution "
                "filename entries"
            )

        if "-cloud3-" in profile_path.name:
            expected_hpcf = CLOUD3_HPCF

        elif "-cmf-buds-pro-2-" in profile_path.name:
            expected_hpcf = CMF_BUDS_PRO_2_HPCF

        else:
            expected_hpcf = EARPODS_HPCF

        if profile_text.count(str(expected_hpcf)) != 2:
            raise SystemExit(
                f"{profile_path}: expected two references "
                f"to HpCF {expected_hpcf}"
            )

        brir_references = [
            line
            for line in profile_text.splitlines()
            if (
                "filename:" in line
                and "BRIR_True_Stereo.wav" in line
            )
        ]

        if len(brir_references) != 4:
            raise SystemExit(
                f"{profile_path}: expected four BRIR "
                "filename references"
            )

        pipeline_start = profile_text.find(
            "\npipeline:\n"
        )

        if pipeline_start == -1:
            raise SystemExit(
                f"{profile_path}: missing pipeline section"
            )

        pipeline_text = profile_text[
            pipeline_start:
        ]

        positions = []

        for item in pipeline_order:
            position = pipeline_text.find(item)

            if position == -1:
                raise SystemExit(
                    f"{profile_path}: missing pipeline item "
                    f"{item!r}"
                )

            positions.append(position)

        if positions != sorted(positions):
            raise SystemExit(
                f"{profile_path}: pipeline is not in "
                "true-stereo BRIR -> ear sum -> HpCF order"
            )

        if profile_text.count("scale: linear") != 8:
            raise SystemExit(
                f"{profile_path}: expected eight linear "
                "mixer source mappings"
            )


if __name__ == "__main__":
    main()
