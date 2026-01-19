
#!/usr/bin/env bash
set -euo pipefail

cfg="$(mktemp -t fastfetch-main-XXXX.jsonc)"
cat >"$cfg" <<'JSON'
{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "logo": "none",
  "display": {
    "separator": " 󰅂 ",
    "color": { "keys": "light_cyan", "title": "light_magenta" },
    "key": { "width": 18, "type": "string" },
    "bar": { "width": 18, "char": { "elapsed": "■", "total": "·" } },
    "percent": { "type": 3, "green": 33, "yellow": 66 }
  },
  "modules": [
    { "type": "title" },
    { "type": "colors"},
    "break",

    { "type": "custom", "format": "\u001b[38;5;208m┈ Session Snapshot ┈" },

    "break",
    { "type": "datetime",  "key": "datetime",  "keyColor": "light_cyan",  "format": "{1}-{3}-{11} {14}:{17}:{20}" },
    { "type": "brightness","key": "brightness","keyColor": "light_yellow","percent": { "type": 3 } },
    { "type": "battery",   "key": "battery",   "keyColor": "light_blue",  "percent": { "type": 3 } },
    { "type": "media",     "key": "media",     "keyColor": "light_magenta" },
    { "type": "player",    "key": "player",    "keyColor": "light_magenta" },
    { "type": "sound",     "key": "sound",     "keyColor": "light_magenta" },
  ]
}
JSON

fastfetch -c "$cfg" -l none
rm -f "$cfg"

