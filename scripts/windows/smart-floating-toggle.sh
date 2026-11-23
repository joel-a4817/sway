#!/usr/bin/env bash
set -Eeuo pipefail
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/sway/windows"
state_file="$state_dir/floating-geometry.json"
mkdir -p -m 700 "$state_dir"
focused_json=$(swaymsg -r -t get_tree | jq -c '.. | objects | select(.focused? == true)' | head -n1)
[[ -n $focused_json ]] || { echo 'No focused Sway container.' >&2; exit 1; }
id=$(jq -r '.id' <<<"$focused_json")
floating=$(jq -r '.floating // "user_off"' <<<"$focused_json")
if [[ $floating == user_on || $floating == auto_on ]]; then
  # Save synchronously before Sway destroys the floating rectangle during tiling.
  python3 - "$state_file" "$focused_json" <<'PY'
import json,os,sys
from pathlib import Path
path=Path(sys.argv[1]);node=json.loads(sys.argv[2]);r=node.get('rect') or {}
g={k:int(r[k]) for k in ('x','y','width','height')}
if g['width']<100 or g['height']<80:raise SystemExit('refusing invalid floating geometry')
try:state=json.loads(path.read_text())
except (OSError,json.JSONDecodeError):state={}
state[str(node['id'])]=g
tmp=path.with_name('.'+path.name+'.tmp');tmp.write_text(json.dumps(state,indent=2,sort_keys=True)+'\n');os.chmod(tmp,0o600);os.replace(tmp,path)
PY
  exec swaymsg -- "[con_id=$id] floating disable"
fi
# Read this exact container's prior rectangle, enable floating, then restore after Sway materializes it.
geometry=$(python3 - "$state_file" "$id" <<'PY'
import json,sys
try:g=json.load(open(sys.argv[1])).get(sys.argv[2])
except (OSError,json.JSONDecodeError):g=None
if g:print(g['x'],g['y'],g['width'],g['height'])
PY
)
swaymsg -- "[con_id=$id] floating enable" >/dev/null
if [[ -n $geometry ]]; then
  read -r x y width height <<<"$geometry"
  # Target the saved con_id, never the newly focused window.
  swaymsg -- "[con_id=$id] resize set width $width px height $height px, move absolute position $x px $y px" >/dev/null
fi
