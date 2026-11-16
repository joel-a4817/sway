#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
host="$(hostname -s)"; stamp="$(date +%Y%m%d-%H%M%S)"; out="${1:-$HOME/Downloads/config-audit-${host}-${stamp}.zip}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/details" "$tmp/archives/nixos" "$tmp/archives/sway"
{
  echo "generated=$(date --iso-8601=seconds)"; echo "host=$host"; uname -a
  command -v nixos-version >/dev/null && nixos-version || true
  command -v swaymsg >/dev/null && swaymsg -t get_version || true
} >"$tmp/details/system.txt" 2>&1
{
  systemctl --failed --no-pager || true
  systemctl --user --failed --no-pager || true
} >"$tmp/details/failed-units.txt" 2>&1
{
  echo '=== minecraft directories ==='
  find "$HOME/minecraft-server" "$HOME/minecraft-backups/$host" /srv/minecraft "$HOME/.local/state/minecraft-server" -maxdepth 4 -printf '%M %u:%g %s %TY-%Tm-%TdT%TH:%TM:%TS %p\n' 2>&1 || true
  echo '=== rename metadata ==='
  find "$HOME/minecraft-server" "$HOME/.local/state/minecraft-server" -type f \( -name '*rename*' -o -name '*intent*' -o -name '*alias*' \) -maxdepth 5 -print -exec sh -c 'echo --- "$1"; cat "$1"' _ {} \; 2>&1 || true
} >"$tmp/details/minecraft.txt"
{
  swaymsg -r -t get_outputs || true; swaymsg -r -t get_tree || true
} >"$tmp/details/sway-runtime.json" 2>&1
rsync -a --delete --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' "$HOME/nixos/" "$tmp/archives/nixos/"
rsync -a --delete --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' "$HOME/.config/sway/" "$tmp/archives/sway/"
mkdir -p "$(dirname "$out")"; (cd "$tmp" && zip -qr "$out" .); printf 'Created %s\n' "$out"
