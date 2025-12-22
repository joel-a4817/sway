
#!/usr/bin/env bash
set -euo pipefail

cfg="$(mktemp -t fastfetch-hardware-XXXX.jsonc)"
cat >"$cfg" <<'JSON'
{
  "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
  "logo": "none",
  "display": {
    "separator": " 󰅂 ",
    "color": { "keys": "light_yellow", "title": "light_magenta" },
    "key": { "width": 22, "type": "string" },
    "bar": { "width": 18, "char": { "elapsed": "■", "total": "·" } },
    "percent": { "type": 3, "green": 33, "yellow": 66 }
  },
  "modules": [
    { "type": "title", "key": "title", "keyColor": "light_magenta", "format": "{user-name}@{host-name}" },
    { "type": "custom", "key": "custom", "format": "\u001b[90m┈ Physical & Graphics ┈" },

    "break",
    { "type": "host",      "key": "host",      "keyColor": "light_blue" },
    { "type": "board",     "key": "board",     "keyColor": "light_blue" },
    { "type": "bluetooth", "key": "bluetooth", "keyColor": "light_green" },
    { "type": "command",   "key": "command",   "keyColor": "light_green",
      "shell": "bash",
      "command": "rfkill list 2>/dev/null | sed -n '1,10p' || echo 'radio: N/A'"
    },
    { "type": "camera",    "key": "camera",    "keyColor": "light_green" },
    { "type": "chassis",   "key": "chassis",   "keyColor": "light_blue" },

    "break",
    { "type": "gpu",       "key": "gpu",       "keyColor": "light_yellow" },
    { "type": "opencl",    "key": "opencl",    "keyColor": "light_yellow" },
    { "type": "opengl",    "key": "opengl",    "keyColor": "light_yellow" },
    { "type": "vulkan",    "key": "vulkan",    "keyColor": "light_yellow" },
    { "type": "display",   "key": "display",   "keyColor": "light_yellow" },
    { "type": "command",   "key": "command",   "keyColor": "light_yellow",
      "shell": "bash",
      "command": "xrandr --listmonitors 2>/dev/null | sed -n '2,10p' || echo 'monitor: N/A'"
    },

    "break",
    { "type": "physicalDisk",   "key": "physicalDisk",   "keyColor": "light_magenta" },
    { "type": "physicalMemory", "key": "physicalMemory", "keyColor": "light_magenta" },
    { "type": "tpm",            "key": "tpm",            "keyColor": "light_cyan" },

    "break",
    { "type": "colors", "key": "colors" }
  ]
}
JSON

fastfetch -c "$cfg" -l none
rm -f "$cfg"
``

