#!/usr/bin/env bash

set -euo pipefail

result_file="/tmp/music-eq-complete.$$"
mode_file="/tmp/music-eq-mode.$$"

rm -f "$result_file" "$mode_file"

export result_file
export mode_file

ffmpeg_opts=(
    -hide_banner
    -loglevel warning
    -nostats
)

profile_selection=""
sofa_track_gain=""

get_sofa_gain() {
    local input="$1"

    if [[ -z "$sofa_track_gain" ]]; then
        sofa_track_gain="$(get_chain_gain "$input")"
        echo "    final gain: ${sofa_track_gain} dB"
    fi
}

# ==================================================
# dsp settings
# ==================================================

sofa="$(find /nix/store -iname 'mit_kemar_normal_pinna.sofa' -print -quit)"

# bs2b
bs2b_fcut="700"
bs2b_feed="45"

# ==================================================
# sofalizer settings
# ==================================================

# maximum gain

get_chain_gain() {
    local input="$1"

    local peak

    peak="$(
        ffmpeg -hide_banner \
            -i "$input" \
            -af "${sofa_filter},astats=metadata=1:reset=0" \
            -f null - 2>&1 |
        awk -F': ' '
            /Peak level dB/ {
                peak=$2
            }
            END {
                print peak
            }
        '
    )"
        awk -v p="$peak" '
            BEGIN {
                g = -p

                g = int(g * 10) / 10.0

                if (g > -p)
                    g -= 0.1

                printf "%.1f\n", g
            }
        '
}

sofa_gain="0"

# position
sofa_rotation="0"
sofa_elevation="0"
sofa_radius="1"

# processing
sofa_type="freq"
sofa_framesize="1024"

# hrtf behaviour
sofa_normalize="true"
sofa_interpolate="false"
sofa_minphase="false"

# neighbor search
sofa_anglestep="0.5"
sofa_radstep="0.01"

# lfe
sofa_lfegain="0" #cannot use on stereo files

# leave empty is default
sofa_speakers=""

# ==================================================
# build filter
# ==================================================

sofa_filter="sofalizer=\
sofa=$sofa:\
gain=${sofa_gain}:\
rotation=${sofa_rotation}:\
elevation=${sofa_elevation}:\
radius=${sofa_radius}:\
type=${sofa_type}:\
lfegain=${sofa_lfegain}:\
framesize=${sofa_framesize}:\
normalize=${sofa_normalize}:\
interpolate=${sofa_interpolate}:\
minphase=${sofa_minphase}:\
anglestep=${sofa_anglestep}:\
radstep=${sofa_radstep}"

if [[ -n "$sofa_speakers" ]]; then
    sofa_filter="${sofa_filter}:speakers=${sofa_speakers}"
fi

copy_cover_and_tags() {
    local original="$1"
    local processed="$2"

    [[ -f "$original" ]] || return 0
    [[ -f "$processed" ]] || return 0

    local tmp="${processed}.tmp.m4a"

    ffmpeg "${ffmpeg_opts[@]}" -y \
        -i "$processed" \
        -i "$original" \
        -map 0:a:0 \
        -map 1:v:0? \
        -map_metadata 1 \
        -c:a copy \
        -c:v copy \
        -disposition:v attached_pic \
        "$tmp"

    mv "$tmp" "$processed"
}

# ==================================================
# paths
# ==================================================

src="$HOME/Downloads/Music/favourites"
covers_dir="$HOME/Downloads/Music/covers"

out_bs2b="$HOME/Downloads/Music/favourites eq/bs2b"

out_earpods_fir="$HOME/Downloads/Music/favourites eq/earpods fir"
out_cloud3_fir="$HOME/Downloads/Music/favourites eq/cloud3 fir"

out_earpods_fir_bs2b="$HOME/Downloads/Music/favourites eq/earpods fir + bs2b"
out_cloud3_fir_bs2b="$HOME/Downloads/Music/favourites eq/cloud3 fir + bs2b"

out_sofalizer="$HOME/Downloads/Music/favourites eq/sofalizer"

out_earpods_fir_sofalizer="$HOME/Downloads/Music/favourites eq/earpods fir + sofalizer"
out_cloud3_fir_sofalizer="$HOME/Downloads/Music/favourites eq/cloud3 fir + sofalizer"

out_earpods_ash="$HOME/Downloads/Music/favourites eq/ash earpods"
out_cloud3_ash="$HOME/Downloads/Music/favourites eq/ash cloud3"

selected() {
    local wanted="$1"

    [[ ",${profile_selection}," == *",$wanted,"* ]]
}

process_file() {
    local input="$1"

    sofa_track_gain=""

    local file_basename
    file_basename="$(basename "$input")"

    local stem
    stem="${file_basename%.*}"

    local rate
    rate="$(ffprobe \
        -v error \
        -select_streams a:0 \
        -show_entries stream=sample_rate \
        -of default=noprint_wrappers=1:nokey=1 \
        "$input")"

    local ash_rate

    if (( rate > 96000 )); then
        ash_rate=96000
    else
        ash_rate="$rate"
    fi

    local earpods_ir
    earpods_ir="$HOME/Documents/prefs/audio/output1/earpods_stereo/earpods_stereo minimum phase ${rate}Hz.wav"

    local cloud3_ir
    cloud3_ir="$HOME/Documents/prefs/audio/output1/cloud3_stereo/cloud3_stereo minimum phase ${rate}Hz.wav"

    echo
    echo "processing: $file_basename"

    # earpods fir only
    if selected 1; then
        mkdir -p "$out_earpods_fir"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$earpods_ir" \
            -vn \
            -filter_complex "[0:a][1:a]afir=irnorm=-1" \
            -c:a alac \
            "$out_earpods_fir/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_earpods_fir/${stem}.m4a"

    fi

    # cloud3 fir only
    if selected 2; then
        mkdir -p "$out_cloud3_fir"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$cloud3_ir" \
            -vn \
            -filter_complex "[0:a][1:a]afir=irnorm=-1" \
            -c:a alac \
            "$out_cloud3_fir/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_cloud3_fir/${stem}.m4a"

    fi

    # bs2b only (bauer)
    if selected 3; then
        mkdir -p "$out_bs2b"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -vn \
            -af "bs2b=fcut=${bs2b_fcut}:feed=${bs2b_feed}" \
            -c:a alac \
            "$out_bs2b/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_bs2b/${stem}.m4a"

    fi

    # earpods fir + bs2b
    if selected 4; then
        mkdir -p "$out_earpods_fir_bs2b"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$earpods_ir" \
            -vn \
            -filter_complex "[0:a][1:a]afir=irnorm=-1[f];[f]bs2b=fcut=${bs2b_fcut}:feed=${bs2b_feed}" \
            -c:a alac \
            "$out_earpods_fir_bs2b/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_earpods_fir_bs2b/${stem}.m4a"

    fi

    # cloud3 fir + bs2b
    if selected 5; then
        mkdir -p "$out_cloud3_fir_bs2b"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$cloud3_ir" \
            -vn \
            -filter_complex "[0:a][1:a]afir=irnorm=-1[f];[f]bs2b=fcut=${bs2b_fcut}:feed=${bs2b_feed}" \
            -c:a alac \
            "$out_cloud3_fir_bs2b/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_cloud3_fir_bs2b/${stem}.m4a"

    fi

    
    # sofalizer only
    if selected 6; then
        mkdir -p "$out_sofalizer"

        get_sofa_gain "$input"
        gain="$sofa_track_gain"

        filter="${sofa_filter/gain=${sofa_gain}/gain=${gain}}"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -vn \
            -af "${filter}" \
            -ar "$rate" \
            -c:a alac \
            "$out_sofalizer/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_sofalizer/${stem}.m4a"

    fi

    # earpods fir + sofalizer
    if selected 7; then
        mkdir -p "$out_earpods_fir_sofalizer"

        get_sofa_gain "$input"
        gain="$sofa_track_gain"

        filter="${sofa_filter/gain=${sofa_gain}/gain=${gain}}"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$earpods_ir" \
            -vn \
            -filter_complex "${filter}[s];[s][1:a]afir=irnorm=-1" \
            -ar "$rate" \
            -c:a alac \
            "$out_earpods_fir_sofalizer/${stem}.m4a"

        copy_cover_and_tags "$input" \
            "$out_earpods_fir_sofalizer/${stem}.m4a"

    fi

    # cloud3 fir + sofalizer
    if selected 8; then
        mkdir -p "$out_cloud3_fir_sofalizer"

        get_sofa_gain "$input"
        gain="$sofa_track_gain"

        filter="${sofa_filter/gain=${sofa_gain}/gain=${gain}}"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$cloud3_ir" \
            -vn \
            -filter_complex "${filter}[s];[s][1:a]afir=irnorm=-1" \
            -ar "$rate" \
            -c:a alac \
            "$out_cloud3_fir_sofalizer/${stem}.m4a"

        copy_cover_and_tags "$input" \
            "$out_cloud3_fir_sofalizer/${stem}.m4a"

    fi
    # earpods ash
    if selected 9; then
        mkdir -p "$out_earpods_ash"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$HOME/Documents/prefs/audio/ASH-Toolset earpods/BRIR_True_Stereo.wav" \
            -i "$HOME/Documents/prefs/audio/ASH-Toolset earpods/Apple_EarPods_Averaged_Measurements.wav" \
            -vn \
            -filter_complex "[0:a][2:a]afir=irnorm=-1[c];[c][1:a]afir=irnorm=-1" \
            -ar "${ash_rate}" \
            -c:a alac \
            "$out_earpods_ash/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_earpods_ash/${stem}.m4a"

    fi
    # cloud3 ash
    if selected 10; then
        mkdir -p "$out_cloud3_ash"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$input" \
            -i "$HOME/Documents/prefs/audio/ASH-Toolset cloud3/BRIR_True_Stereo.wav" \
            -i "$HOME/Documents/prefs/audio/ASH-Toolset cloud3/HyperX_Cloud_III_Rtings.wav" \
            -vn \
            -filter_complex "[0:a][2:a]afir=irnorm=-1[c];[c][1:a]afir=irnorm=-1" \
            -ar "${ash_rate}" \
            -c:a alac \
            "$out_cloud3_ash/${stem}.m4a"

        copy_cover_and_tags "$input" "$out_cloud3_ash/${stem}.m4a"

    fi
}

swaynag \
    -t warning \
    -y overlay \
    -m "music eq" \
-z "update music library" '
echo full > "$mode_file"
touch "$result_file"
' \
-z "extract all covers" '
echo covers > "$mode_file"
touch "$result_file"
' \
-z "process single music file" '
echo single > "$mode_file"
touch "$result_file"
'&

while [[ ! -f "$result_file" ]]; do
    sleep 0.1
done

rm -f "$result_file"

mode="$(cat "$mode_file" 2>/dev/null || true)"
rm -f "$mode_file"

if [[ "$mode" == "single" ]]; then
    echo
    echo "enter filename from favourites or absolute path:"
    read -r input_arg


    if [[ "$input_arg" = /* ]]; then
      input="$input_arg"
    else
        input="$src/$input_arg"
    fi

    if [[ ! -f "$input" ]]; then
        echo
        echo "file not found:"
        echo "$input"
        echo
        read -n 1 -rsp "Press any key to close..."
        exit 1
    fi

echo

file_basename="$(basename "$input")"
stem="${file_basename%.*}"

mkdir -p "$covers_dir"

ffmpeg "${ffmpeg_opts[@]}" -y \
    -i "$input" \
    -an \
    -map 0:v:0 \
    -frames:v 1 \
    "$covers_dir/$stem.png" \
    >/dev/null 2>&1 || true

echo

profile_selection="1,2,3,4,5,6,7,8,9,10"

process_file "$input"

# covers

elif [[ "$mode" == "covers" ]]; then

    echo "extracting covers..."

    mkdir -p "$covers_dir"

    find "$src" -type f \
        \( -iname "*.m4a" -o \
           -iname "*.mp3" -o \
           -iname "*.flac" -o \
           -iname "*.aac" -o \
           -iname "*.ogg" -o \
           -iname "*.opus" \
        \) \
        -print0 |
    while ifs= read -r -d '' file; do

        base="$(basename "${file%.*}")"
        out="$covers_dir/$base.png"

        echo "cover: $base"

        ffmpeg "${ffmpeg_opts[@]}" -y \
            -i "$file" \
            -an \
            -map 0:v:0 \
            -frames:v 1 \
            "$out" \
            >/dev/null 2>&1 || true

    done



#full library



elif [[ "$mode" == "full" ]]; then

echo
echo "[0] keep existing files (exit)"
echo
echo "[1] earpods fir"
echo "[2] cloud3 fir"
echo "[3] bs2b"
echo "[4] earpods fir + bs2b"
echo "[5] cloud3 fir + bs2b"
echo "[6] sofalizer"
echo "[7] earpods fir + sofalizer"
echo "[8] cloud3 fir + sofalizer"
echo "[9] earpods ash"
echo "[10] cloud3 ash"
echo "[11] update all folders"
echo

read -rp "selection: " profile_selection

profile_selection="${profile_selection// /}"

if [[ "$profile_selection" == "0" ]]; then
    echo "keeping existing files."
    exit 0
fi

if [[ "$profile_selection" == "11" ]]; then
    profile_selection="1,2,3,4,5,6,7,8,9,10"
fi

for item in ${profile_selection//,/ }; do
    if ! [[ "$item" =~ ^(1|2|3|4|5|6|7|8|9|10)$ ]]; then
        echo "invalid selection: $item"
        exit 1
    fi
done

mapfile -d '' files < <(
find "$src" -type f \( \
    -iname "*.m4a" -o \
    -iname "*.aac" -o \
    -iname "*.mp3" -o \
    -iname "*.flac" -o \
    -iname "*.wav" -o \
    -iname "*.ogg" -o \
    -iname "*.opus" \
\) -print0
)

for file in "${files[@]}"; do
    process_file "$file"
done

fi

echo
read -n 1 -rsp "Press any key to close..."
