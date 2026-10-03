#!/usr/bin/env bash
set -euo pipefail
output=''
file=''
while (($#)); do
    case "$1" in
        -o) output="${2:?}"; shift 2 ;;
        -f) file="${2:?}"; shift 2 ;;
        *) echo "Unsupported recorder option: $1" >&2; exit 2 ;;
    esac
done
[[ -n "$file" ]] || { echo 'Missing -f recording file' >&2; exit 2; }
mkdir -p "$(dirname "$file")"
tmp="$(mktemp -d)"
video_pid='' audio_pid=''
finish() {
    trap - INT TERM EXIT
    [[ -z "$video_pid" ]] || kill -INT "$video_pid" 2>/dev/null || true
    [[ -z "$audio_pid" ]] || kill -INT "$audio_pid" 2>/dev/null || true
    [[ -z "$video_pid" ]] || wait "$video_pid" 2>/dev/null || true
    [[ -z "$audio_pid" ]] || wait "$audio_pid" 2>/dev/null || true
    if [[ -s "$tmp/video.mkv" && -s "$tmp/audio.flac" ]]; then
        ffmpeg -hide_banner -loglevel error -y -i "$tmp/video.mkv" -i "$tmp/audio.flac" -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -shortest "$file" || echo "Mux failed; tracks retained in $tmp" >&2
    else
        echo "Recording incomplete; tracks retained in $tmp" >&2
        return
    fi
    rm -rf -- "$tmp"
}
trap finish INT TERM EXIT
ffmpeg -hide_banner -loglevel error -f alsa -i sonobus_camilladsp -c:a flac "$tmp/audio.flac" & audio_pid=$!
if [[ -n "$output" ]]; then wf-recorder -o "$output" -f "$tmp/video.mkv" &
else wf-recorder -f "$tmp/video.mkv" & fi
video_pid=$!
wait "$video_pid" || true
