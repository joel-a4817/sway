# Audio backend

`audio-backend-cli.py` loads the Python modules in `manifest.txt` in order. `media-control.sh` is the Sway UI entry point and is intentionally not part of the Python manifest.

## Media Control
The compact top-level actions are Route, Filters, Web Remote, and Network. Route presents four user-facing modes:

- Listen: external source to this PC
- Cast: PC source to the external device
- Relay: receive and send external audio
- Local: PC playback and PC output only

The `AirPlay Direct` NetworkManager profile is enabled automatically for Listen, Cast, and Relay and disabled for Local. `wifi-toggle.sh` also exposes an `AirPlay` network button.

## CamillaDSP profiles
Each canonical profile remains at `<device>/<profile>.yml` for custom-filter rebasing and one canonical S5 export. Runtime-only CamillaDSP copies are generated at:

`<device>/<chunksize>-frames/<limiter-on|limiter-off>/<profile>.yml`

Generated chunk sizes are 64, 128, 256, 512, 1024, 2048, 4096, and 8192 frames. Runtime variants are excluded from Downloads-S5 exports.


## Audio output menu
The existing Audio output action first selects the source/destination mode, then reuses the existing device, profile, route and PipeWire sink transaction for configurations that include PC output.
