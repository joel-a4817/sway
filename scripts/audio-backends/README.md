# Audio backend files

## Package scope

This directory contains the modular backends loaded by the separately installed
server launcher, the shell fragments sourced by the separately installed Media
Control launcher, and the temporary YouTube Music extension. It deliberately
does not contain or install either external launcher.

## `manifest.txt`

**What it does:** Defines the Python backend loading order.

**How it does it:** Lists one Python filename per line. The server launcher executes those files from top to bottom into the same namespace.

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

### SOFA-backed custom spatial filters

Custom Spatial Filter reads P0135 free-field-compensated minimum-phase-compensation coordinates directly from `~/Documents/prefs/audio/P0135/HRTF/HRTF/96kHz/P0135_FreeFieldCompMinPhase_96kHz.sofa`. It accepts only a 96 kHz `SimpleFreeFieldHRIR` dataset marked `free field`, extracts only selected measured coordinates into each profile's private `.assets` directory, preserves all supplied HRIR samples and embedded timing, applies `Data.Delay` when nonzero, and adds no room or reverberation stage. The existing updater still rebuilds the complete custom profile and its Safe max metadata.

### YouTube Music authenticated server and Glide bridge

`72_ytmusic_api.py` owns Quick Picks, authenticated ratings, Liked Music playback and multi-select removal, and in-place browser-cookie refresh through `/home/joel/ytmusicapi` and `~/.config/ytmusicapi/browser.json`. YouTube Music actions reuse an open Glide instance; when Glide is closed, the backend starts the bundled web-ext launcher so Glide opens with the temporary extension already loaded. The bundled Glide bridge captures the next signed-in YouTube Music API request automatically, sends its request headers only to the local Media Control server, and atomically replaces the cookie-backed browser authentication file only after validation. It deliberately contains no MPRIS logic. The Glide temporary extension reports the current `videoId` and physically activates YouTube Music's Start playback control for URLs marked `mcautoplay=1`.

## `ytmusic-extension/background.js`

**What it does:** Bridges YouTube Music tabs to the local Media Control API.

**How it does it:** Reports Quick Picks tracks, captures a requested signed-in cookie refresh, queues only automated tab closes, and leaves manual tab closure to Glide.

## `ytmusic-extension/content.js`

**What it does:** Reports the current YouTube Music track and performs requested one-shot autoplay.

**How it does it:** Reads the active video ID and title, reports changes to the extension background script, and activates playback only for URLs marked `mcautoplay=1`.

## `ytmusic-extension/manifest.json`

**What it does:** Declares the temporary WebExtension.

**How it does it:** Grants only the tabs, request-observation, YouTube Music, and loopback API permissions used by the two extension scripts.

## `ytmusic-extension/glide-rdp-browser`

**What it does:** Lets `web-ext` launch the normal Glide profile.

**How it does it:** Retains only the debugger port supplied by `web-ext`, discards temporary-profile flags, and starts Glide with that debugger port.

## `ytmusic-extension/glide-with-ytmusic-temp-extension`

**What it does:** Starts Glide with the unpacked temporary extension.

**How it does it:** Runs `web-ext` against this extension directory through `glide-rdp-browser`, without extension auto-reload or config discovery.
