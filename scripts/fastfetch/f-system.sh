
#!/usr/bin/env bash
set -euo pipefail

cfg="$(mktemp -t fastfetch-system-XXXX.jsonc)"
cat >"$cfg" <<'JSON'
{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "logo": "none",
  "display": {
    "separator": " → ",
    "color": { "keys": "light_blue", "title": "light_red" },
    "key": { "width": 22, "type": "string" },
    "bar": { "width": 18, "char": { "elapsed": "█", "total": "░" } },
    "percent": { "type": 3, "green": 33, "yellow": 66 }
  },
  "modules": [
    { "type": "title", "key": "title", "keyColor": "light_red", "format": "{user-name}@{host-name}" },
    { "type": "custom", "key": "custom", "format": "\u001b[90m┈ OS & Session ┈" },

    "break",
    { "type": "os",         "key": "os",         "keyColor": "light_blue" },
    { "type": "kernel",     "key": "kernel",     "keyColor": "light_blue" },
    { "type": "initSystem", "key": "initSystem", "keyColor": "light_blue" },
    { "type": "lm",         "key": "lm",         "keyColor": "light_blue" },
    { "type": "shell",      "key": "shell",      "keyColor": "light_blue" },
    { "type": "terminal",   "key": "terminal",   "keyColor": "light_blue" },
    { "type": "editor",     "key": "editor",     "keyColor": "light_blue" },
    { "type": "locale",     "key": "locale",     "keyColor": "light_blue" },

    "break",
    { "type": "bios",    "key": "bios",    "keyColor": "light_cyan" },
    { "type": "bootmgr", "key": "bootmgr", "keyColor": "light_cyan" },
    { "type": "btrfs",   "key": "btrfs",   "keyColor": "light_magenta" },
    { "type": "zpool",   "key": "zpool",   "keyColor": "light_magenta" },

    "break",
    { "type": "colors", "key": "colors" }
  ]
}
JSON

fastfetch -c "$cfg" -l none
rm -f "$cfg"

