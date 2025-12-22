
#!/usr/bin/env bash
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/sway"
STATE_FILE="$STATE_DIR/floating_modifier_state"
mkdir -p "$STATE_DIR"

current="normal"
[[ -f "$STATE_FILE" ]] && read -r current < "$STATE_FILE"
next=$([[ "$current" == "normal" ]] && echo inverse || echo normal)

# Use your actual mod key here (Mod1=Alt, Mod4=Super)
swaymsg "floating_modifier Mod4 $next" >/dev/null

echo "$next" > "$STATE_FILE"
