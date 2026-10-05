# Audio backend layout

Loaded in `manifest.txt` order into a shared namespace so existing API and CLI contracts remain unchanged.

- `05_runtime.py`: process, state, PipeWire and service primitives
- `10_engine_filters.py`, `15_engine_process.py`: profile discovery, CamillaDSP and bypass
- `20_gain_volume.py`, `25_gain_normalization.py`: single master and ALSA 0 dB normalization
- `30_players.py`, `70_player_commands.py`: SonoBus process support, MPV, MPRIS, library and queues
- `40_modes_sonobus.py`, `65_routing_modes.py`: routing policy and source/output modes
- `50_output_topology.py`, `60_output_transactions.py`: output discovery, staged commit, cancel and recovery
- `55_input_camera.py`: input and camera transactions
- `75_display.py`: Away and display power
- `80_state_api.py`, `85_web_assets.py`, `90_http_server.py`: state, UI and HTTP routes
- `99_lifecycle_cli.py`: startup, shutdown and stable CLI contract

Shell fragments are sourced by `media-control.sh`.
