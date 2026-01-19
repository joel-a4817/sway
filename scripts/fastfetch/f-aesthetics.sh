
#!/usr/bin/env bash
set -euo pipefail

cfg="$(mktemp -t fastfetch-aesthetics-XXXX.jsonc)"
cat >"$cfg" <<'JSON'
{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "logo": "none",
  "display": {
    "separator": " 󰅂 ",
    "color": { "keys": "light_yellow", "title": "light_yellow" },
    "key": { "width": 22, "type": "string" },
    "bar": { "width": 18, "char": { "elapsed": "■", "total": "·" } },
    "percent": { "type": 3, "green": 40, "yellow": 70 }
  },
  "modules": [
    { "type": "title" },
    { "type": "colors"},
    "break",

    { "type": "custom", "format": "\u001b[38;5;208m┈ Desktop Visuals ┈" },

    "break",
    { "type": "de",           "key": "de",           "keyColor": "light_blue" },
    { "type": "wm",           "key": "wm",           "keyColor": "light_blue" },
    { "type": "theme",        "key": "theme",        "keyColor": "light_magenta" },
    { "type": "wallpaper",    "key": "wallpaper",    "keyColor": "light_yellow" },
    { "type": "icons",        "key": "icons",        "keyColor": "light_yellow" },
    { "type": "font",         "key": "font",         "keyColor": "light_yellow" },
    { "type": "cursor",       "key": "cursor",       "keyColor": "light_yellow" },

    "break",
    { "type": "terminalFont", "key": "terminalFont", "keyColor": "light_blue" },
    { "type": "terminalSize", "key": "terminalSize", "keyColor": "light_blue" },
    { "type": "terminalTheme","key": "terminalTheme","keyColor": "light_blue" },
  ]
}
JSON

fastfetch -c "$cfg" -l none
rm -f "$cfg"

