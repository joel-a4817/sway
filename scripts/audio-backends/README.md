# Audio backend

This directory contains the local Media Control backend only.

Retained responsibilities:
- CamillaDSP filter discovery, validation, creation, startup and switching
- PipeWire/ALSA readiness and physical device/profile/route/sink transactions
- input and camera selection with commit/rollback
- logical master-volume preservation during graph changes
- SonoBus and AirPlay routing modes
- laptop output selection, including saved-device restoration
- away/display, VNC and iPad Web Remote launch controls

The retired laptop HTTP server, embedded web UI, YouTube Music integration, MPV/local-music library, MPRIS/system-media controls, artwork extraction and playlist APIs are intentionally absent. The iPad Web Remote owns media playback and its server; this backend only starts/stops that remote server and retains device-audio output configuration used by Media Control.

`audio-backend-cli.py` loads the ordered Python modules in `manifest.txt`. `media-control.sh` is the Sway UI entry point and is intentionally outside the Python manifest.
