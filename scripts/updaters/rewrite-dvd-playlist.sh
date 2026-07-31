#!/usr/bin/env bash

swaynag -t warning -y overlay \
    -m "Music DVD" \
    -z "Erase disc and burn current music library" \
    "$term -e bash -c 'dvd+rw-format -blank /dev/sr0 && mkisofs -J -R -o /tmp/music.iso ~/Media/Music && growisofs -dvd-compat -Z /dev/sr0=/tmp/music.iso && rm /tmp/music.iso; echo; read -n1 -rsp \"Press any key to close...\"'"
