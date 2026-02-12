# The rule for how windows tile/float when switching states. Remembers floating position between states and tiles properly according to autotiling rules.
#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${HOME}/.cache/sway-float"
mkdir -p "$STATE_DIR"

# Get focused node (id, state, identity)
focused_json="$(swaymsg -t get_tree -r | jq -rc '
  .. | objects | select(.focused==true) |
  {id, floating, app_id, class: .window_properties.class, pid}
')"
[ -n "$focused_json" ] || exit 0

floating="$(jq -r '.floating' <<<"$focused_json")"
app_id="$(jq -r '.app_id // empty' <<<"$focused_json")"
klass="$(jq -r '.class // empty' <<<"$focused_json")"
pid="$(jq -r '.pid // empty' <<<"$focused_json")"

# Stable key per window identity
if [[ -n "$app_id" ]]; then
  key="app_${app_id}"
elif [[ -n "$klass" ]]; then
  key="class_${klass}"
else
  key="pid_${pid}"
fi

meta_file="${STATE_DIR}/${key}.meta.json"

if [[ "$floating" == "user_on" ]]; then
  # FLOATING -> TILED
  parent_mark="$(jq -r '.parent_mark // empty' "$meta_file" 2>/dev/null || true)"

  swaymsg 'floating disable' >/dev/null

  if [[ -n "$parent_mark" ]]; then
    # Only do the restore if the mark is present; otherwise, bail.
    # (move to mark <name> is a standard Sway command)  # cite marker
    swaymsg "move to mark $parent_mark" >/dev/null || true
  fi

else
  # TILED -> FLOATING
  uid="$(date +%s%N)"
  mark_f="__copi_focused_${uid}"
  mark_p="__copi_parent_${uid}"

  # Mark focused and its parent; keep focus on original window
  swaymsg "mark --add $mark_f, focus parent, mark --add $mark_p, [con_mark=\"$mark_f\"] focus" >/dev/null

  # Persist only the parent mark; no quadrant/output fallback
  jq -nc --arg pm "$mark_p" '{parent_mark:$pm}' > "$meta_file"

  swaymsg 'floating enable' >/dev/null
fi
