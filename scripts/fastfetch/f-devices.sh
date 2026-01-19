
#!/usr/bin/env bash
set -euo pipefail

cfg="$(mktemp -t fastfetch-devices-XXXX.jsonc)"
cat >"$cfg" <<'JSON'
{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "logo": "none",
  "display": {
    "separator": " → ",
    "color": { "keys": "light_green", "title": "light_red" },
    "key": { "width": 20, "type": "string" },
    "bar": { "width": 18, "char": { "elapsed": "█", "total": "░" } },
    "percent": { "type": 3, "green": 40, "yellow": 70 }
  },
   "modules": [
    { "type": "title" },
    { "type": "colors"},
    "break",

    { "type": "custom", "format": "\u001b[38;5;208m┈ Input & Wireless ┈" },

    "break",
    { "type": "bluetooth",    "key": "bluetooth",    "keyColor": "light_green" },
    { "type": "gamepad",      "key": "gamepad",      "keyColor": "light_green" },
    { "type": "keyboard",     "key": "keyboard",     "keyColor": "light_green" },
    { "type": "mouse",        "key": "mouse",        "keyColor": "light_green" },
    { "type": "powerAdapter", "key": "powerAdapter", "keyColor": "light_blue" },
  ]
}
JSON

fastfetch -c "$cfg" -l none
rm -f "$cfg"

