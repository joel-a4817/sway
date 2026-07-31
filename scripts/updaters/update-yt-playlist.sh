#!/usr/bin/env bash

swaynag -t warning -y overlay \
    -m "Music Library" \
    -z "Download latest tracks from YouTube playlist" \
    "$term -e bash -c 'yt-dlp --yes-playlist -x --audio-format mp3 --audio-quality 0 --force-overwrites -P ~/Media/Music \"https://youtube.com/playlist?list=PLPxzU5kNqyqU&si=LJw9jQ6tvBfz5qAe\"; echo; read -n1 -rsp \"Press any key to close...\"'"
