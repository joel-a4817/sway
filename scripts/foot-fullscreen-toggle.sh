
#!/usr/bin/env bash
set -euo pipefail

# Grab exactly ONE focused container from the tree
focused="$(swaymsg -r -t get_tree | jq -rc '
  [recurse(.nodes[]?, .floating_nodes[]?) | select(.focused == true)]
  | first // empty
')"

# If we couldn't find anything focused, just toggle fullscreen
if [[ -z "${focused}" ]]; then
  swaymsg fullscreen toggle >/dev/null
  exit 0
fi

pid="$(jq -r '.pid // empty' <<<"${focused}")"
app_id="$(jq -r '.app_id // empty' <<<"${focused}")"
fs="$(jq -r '.fullscreen_mode // 0' <<<"${focused}")"

# If PID is missing, fallback
if [[ -z "${pid}" ]]; then
  swaymsg fullscreen toggle >/dev/null
  exit 0
fi

# Only apply to foot (normal mode) or footclient (server mode)
# foot defaults its app-id to "foot" in normal mode. [3](https://manpages.debian.org/trixie/waybar/waybar-sway-window.5.en.html)
if [[ "${app_id}" != "foot" && "${app_id}" != "footclient" ]]; then
  swaymsg fullscreen toggle >/dev/null
  exit 0
fi

# Theme switch (signal) + fullscreen toggle
# Foot signals: SIGUSR1 -> [colors], SIGUSR2 -> [colors2]. [1](https://www.reddit.com/r/swaywm/comments/s80101/how_do_you_avoid_opening_windows_in_fullscreen/)
if [[ "${fs}" == "0" ]]; then
  # Going fullscreen: switch to opaque theme first (colors2), then fullscreen
  kill -USR2 "${pid}" 2>/dev/null || true
  swaymsg fullscreen enable >/dev/null
else
  # Leaving fullscreen: switch back to transparent theme first (colors), then unfullscreen
  kill -USR1 "${pid}" 2>/dev/null || true
  swaymsg fullscreen disable >/dev/null
fi

