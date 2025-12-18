#!/usr/bin/env bash
# Lock screen first, then suspend (Windows-style sleep)

set -euo pipefail
swaylock -f
systemctl suspend