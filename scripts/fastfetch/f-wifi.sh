
#!/usr/bin/env bash
set -euo pipefail

cfg="$(mktemp -t fastfetch-wifi-XXXX.jsonc)"
cat >"$cfg" <<'JSON'
{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "logo": "none",
  "display": {
    "separator": " 󰅂 ",
    "color": { "keys": "light_cyan", "title": "light_cyan" },
    "key": { "width": 20, "type": "string" },
    "bar": { "width": 18, "char": { "elapsed": "■", "total": "·" } },
    "percent": { "type": 3, "green": 33, "yellow": 66 }
  },
  "modules": [
    { "type": "title" },
    "break",
    { "type": "custom", "format": "\u001b[90m┈ Network & Reachability ┈" },

    "break",
    { "type": "dns",     "key": "dns",     "keyColor": "light_cyan" },
    { "type": "localIp", "key": "localIp", "keyColor": "light_cyan" },
    { "type": "netIo",   "key": "netIo",   "keyColor": "light_cyan" },
    { "type": "publicIp","key": "publicIp","keyColor": "light_cyan" },
    { "type": "wifi",    "key": "wifi",    "keyColor": "light_cyan" },

    "break",
    { "type": "command", "key": "command", "keyColor": "light_blue",
      "shell": "bash",
      "command": "curl -fsSL 'https://wttr.in/?format=1' 2>/dev/null || echo 'weather: N/A'"
    },

    "break",
    { "type": "colors", }
  ]
}
JSON

fastfetch -c "$cfg" -l none
rm -f "$cfg"

