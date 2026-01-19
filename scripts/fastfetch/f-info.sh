
#!/usr/bin/env bash
set -euo pipefail

cfg="$(mktemp -t fastfetch-stats-XXXX.jsonc)"
cat >"$cfg" <<'JSON'
{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "logo": "none",
  "display": {
    "separator": " → ",
    "color": { "keys": "light_green", "title": "light_magenta" },
    "key": { "width": 22, "type": "string" },
    "bar": { "width": 18, "char": { "elapsed": "█", "total": "░" } },
    "percent": { "type": 3, "green": 33, "yellow": 66 }
  },
  "modules": [
    { "type": "title" },
    { "type": "colors"},
    "break",

    { "type": "custom", "format": "\u001b[38;5;208m┈ Live System Stats ┈" },

    "break",
    { "type": "cpu",      "key": "cpu",      "keyColor": "light_green",
      "format": "{name} ({cores-physical}C/{cores-logical}T) @ {freq-max}" },
    { "type": "cpuCache", "key": "cpuCache", "keyColor": "light_green" },
    { "type": "cpuUsage", "key": "cpuUsage", "keyColor": "light_green", "percent": { "type": 3 } },
    { "type": "loadavg",  "key": "loadavg",  "keyColor": "light_green" },

    "break",
    { "type": "memory",   "key": "memory",   "keyColor": "light_blue",   "percent": { "type": 3 } },
    { "type": "swap",     "key": "swap",     "keyColor": "light_blue",   "percent": { "type": 3 } },
    { "type": "processes","key": "processes","keyColor": "light_blue" },
    { "type": "packages", "key": "packages", "keyColor": "light_blue" },

    "break",
    { "type": "disk",     "key": "disk",     "keyColor": "light_magenta" },
    { "type": "diskIo",   "key": "diskIo",   "keyColor": "light_magenta" },

    "break",
    { "type": "users",     "key": "users",     "keyColor": "light_cyan" },
    { "type": "uptime",   "key": "uptime",   "keyColor": "light_cyan" },
  ]
}
JSON

fastfetch -c "$cfg" -l none
rm -f "$cfg"

