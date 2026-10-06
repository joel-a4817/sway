# Audio backend files

## `camilladsp-server.py`

**What it does:** Starts the webremote server or runs one of its command-line operations.

**How it does it:** Reads `audio-backends/manifest.txt`, executes each listed Python file into one shared namespace, then calls `main()` for the server or `topology_cli()` when arguments are supplied.

## `camilladsp-server-sonobus.py`

**What it does:** Provides the package copy of the server launcher using the established compatibility filename.

**How it does it:** Loads the same Python backend manifest and dispatches to the same server or CLI entry points as `camilladsp-server.py`.

## `media-control.sh`

**What it does:** Starts Media Control.

**How it does it:** Sources `10_session.sh`, `20_menu_helpers.sh`, `30_camera.sh`, and `40_main_menu.sh` from `~/.config/sway/scripts/audio-backends`, in that order.

## `install.sh`

**What it does:** Installs the modular server, Media Control, and all backend files.

**How it does it:** Copies the backend directory to `~/.config/sway/scripts/audio-backends`, installs the two launchers at their required paths, sets launchers to mode `0755`, and sets sourced or loaded backend files to mode `0644`.

## `manifest.txt`

**What it does:** Defines the Python backend loading order.

**How it does it:** Lists one Python filename per line. The server launcher executes those files from top to bottom into the same namespace.

## `FILE-LIST.txt`

**What it does:** Lists the files included in the modular package.

**How it does it:** Stores a plain-text package inventory for inspection.

## `README.md`

**What it does:** Explains what each modular file is responsible for.

**How it does it:** Provides this file-by-file reference.

## `00_context.py`

**What it does:** Defines shared imports, constants, paths, service names, process names, audio endpoint names, modes, locks, caches, and state locations.

**How it does it:** Creates the common variables and synchronization objects that later Python backends use through the shared namespace.

## `05_runtime.py`

**What it does:** Provides low-level process, PipeWire, service, PID-file, AirPlay, cache, and command-execution functions.

**How it does it:** Runs system commands, parses the PipeWire graph, finds sinks and devices, assigns playback streams, controls user services, validates owned processes, waits for PipeWire, and applies AirPlay service state.

## `10_engine_filters.py`

**What it does:** Discovers and describes available listening profiles.

**How it does it:** Scans the audio-filter directory for No filter, root Filterless, and room profiles, parses their folder and filename metadata, groups them for display, resolves profile keys to YAML files, and reads the selected profile.

## `15_engine_process.py`

**What it does:** Controls CamillaDSP and the No-filter bypass bridge.

**How it does it:** Starts, stops, validates, and reuses CamillaDSP; starts and stops the bypass process; selects the required desktop sink; waits for the expected CamillaDSP configuration; switches filters; and reconciles SonoBus when the active engine changes.

## `20_gain_volume.py`

**What it does:** Maintains one logical master volume across system audio and MPV.

**How it does it:** Chooses the correct gain point for the active route, persists the master value, adjusts the physical or CamillaDSP PipeWire sink as appropriate, synchronizes MPV gain when MPV bypasses sink volume, and keeps unused gain stages at unity.

## `25_gain_normalization.py`

**What it does:** Normalizes applicable ALSA playback and capture controls to exactly 0 dB.

**How it does it:** Discovers writable ALSA controls, converts 0 dB to their raw values, writes every channel, reads the settings back, reports the exact failing card or control, and restores the saved logical master afterward.

## `30_players.py`

**What it does:** Manages SonoBus process detection, MPV, MPRIS system media, songs, playlists, queues, metadata, and artwork.

**How it does it:** Uses MPV IPC and MPRIS commands, reads media tags and cover art, builds player and library state, stores queue and playlist information, identifies SonoBus processes, and performs atomic JSON state updates.

## `40_modes_sonobus.py`

**What it does:** Manages SonoBus groups, device settings, and restart behavior.

**How it does it:** Reads and saves group profiles, updates the nested SonoBus `audioSetup` value, verifies required ALSA input and output devices, handles password-required groups, and starts or restarts SonoBus for the active mode.

## `50_output_topology.py`

**What it does:** Discovers physical playback devices and applies basic output profile and route changes.

**How it does it:** Reads PipeWire topology, excludes internal virtual audio objects, identifies physical cards, profiles, routes and sinks, associates sinks with their cards, waits for changes to settle, and applies selected card profiles or routes.

## `55_input_camera.py`

**What it does:** Handles input-device selection and camera selection.

**How it does it:** Discovers input devices, profiles, routes, sources and ports; applies staged input changes; preserves shared output-card state; tracks input stream origins; discovers cameras; and restores camera clients when a selection is cancelled or fails.

## `60_output_transactions.py`

**What it does:** Manages safe staged output selection and rollback.

**How it does it:** Starts and owns preview transactions, snapshots existing output state, stages Device, Profile and Route changes, re-reads topology between stages, commits the selected sink, restores prior state on cancellation, and recovers abandoned previews.

## `65_routing_modes.py`

**What it does:** Applies the complete source and destination audio mode.

**How it does it:** Chooses Laptop or External input policy, selects CamillaDSP or the bypass bridge, routes desktop audio to the appropriate ingress, enables local monitoring when Laptop is included, controls SonoBus when External is included, and persists the selected mode.

## `70_player_commands.py`

**What it does:** Performs higher-level local music playback commands.

**How it does it:** Sends MPV IPC commands, plays individual songs or complete playlists, creates shuffled queues, and stores the active playlist information needed for restoration.

## `75_display.py`

**What it does:** Controls Away and Display Off behavior.

**How it does it:** Invokes the shared standalone device-lock.py executable for status and toggle operations, so the web button and Sway key binding use one implementation.

## `80_state_api.py`

**What it does:** Builds the state objects returned to the web UI.

**How it does it:** Combines filter, mode, player, group, output, display, and system-media information into full and frequently polled state dictionaries.

## `85_web_assets.py`

**What it does:** Contains the complete browser interface.

**How it does it:** Embeds the HTML, CSS, and JavaScript for the home page, media controls, Audio options, listening profiles, source grid, output selector, SonoBus groups, Media services, notifications, polling, and API calls.

## `90_http_server.py`

**What it does:** Serves the web interface and exposes the HTTP API.

**How it does it:** Handles GET and POST requests, serves the embedded page and artwork, parses request data, calls the appropriate backend function, serializes JSON responses, and converts backend failures into HTTP errors.

## `99_lifecycle_cli.py`

**What it does:** Controls server startup, shutdown, cleanup, audio stop/start, and command-line operations.

**How it does it:** Restores saved state during startup, starts the saved mode, prevents unsafe duplicate instances, stops owned processes and services, handles signals, runs the HTTP server, tracks SonoBus windows, and dispatches CLI commands used by Media Control.

## `10_session.sh`

**What it does:** Sets up and cleans up a Media Control session.

**How it does it:** Defines paths, creates logs, takes the single-instance lock, installs exit and signal traps, tracks active output previews, cancels unfinished previews, and displays logged errors when an action fails.

## `20_menu_helpers.sh`

**What it does:** Formats Media Control menus and reads selections.

**How it does it:** Detects terminal width, wraps text, compacts labels, prints numbered menu items, and validates the selected index.

## `30_camera.sh`

**What it does:** Provides the Media Control camera-selection workflow.

**How it does it:** Requests camera topology from the Python backend, displays available endpoints, sends the chosen camera to the backend, and relies on the Python transaction logic for restoration.

## `40_main_menu.sh`

**What it does:** Builds the main Media Control interface and runs its actions.

**How it does it:** Creates the Swaynag buttons, launches filter, input, camera, and output selectors, requires the current mode to include Laptop without changing that mode, performs the Device to Profile to Route to Sink workflow, commits or cancels output previews, and invokes audio stop/start.
