# Capture control uses both PulseAudio compatibility data and PipeWire's graph.
# pactl supplies readable audio source ports and movable recording streams;
# pw-dump supplies physical camera/device relationships; wpctl commits defaults.
camera_topology() {
    pw-dump | python3 -c '
import json,sys
x=json.load(sys.stdin); objs={o.get("id"):o for o in x}; devices={}
for o in x:
 p=o.get("info",{}).get("props",{}); c=p.get("media.class","")
 if c=="Video/Source":
  did=p.get("device.id"); d=objs.get(did,{}) if isinstance(did,int) else {}
  dp=d.get("info",{}).get("props",{})
  physical=str(did) if isinstance(did,int) else "node:"+str(o.get("id"))
  dl=dp.get("device.description") or dp.get("device.nick") or dp.get("device.product.name") or p.get("device.description") or "Camera"
  nl=p.get("node.description") or p.get("node.nick") or p.get("node.name") or str(o.get("id"))
  devices.setdefault(physical,{"key":physical,"label":dl,"nodes":[]})["nodes"].append({"id":o.get("id"),"name":p.get("node.name",""),"label":nl})
print(json.dumps(list(devices.values())))'
}
camera_picker() (
    local topo current device_key device_label node_id node_name node_label i
    local -a device_keys=() device_labels=() node_ids=() node_names=() node_labels=()
    topo="$(camera_topology)" || return 1
    mapfile -t device_keys < <(jq -r '.[].key' <<<"$topo"); mapfile -t device_labels < <(jq -r '.[].label' <<<"$topo")
    ((${#device_keys[@]})) || { wrap_line 'No physical cameras are exposed.'; return 1; }
    current="$(wpctl list video sources | awk -F '\t' '$4=="*"{print $2;exit}')"
    current_device=''; current_device_label=''
    for i in "${!device_keys[@]}"; do
      jq -e --arg k "${device_keys[i]}" --arg n "$current" '.[]|select(.key==$k)|.nodes[]|select(.name==$n)' <<<"$topo" >/dev/null && { current_device="${device_keys[i]}"; current_device_label="${device_labels[i]}"; }
    done
    while :; do
    choose_index 'Cameras | Physical device' "Current${current_device_label:+ ($(compact_item_label "$current_device_label"))}" "${device_labels[@]}" || return 1
    if ((number==0)); then [[ -n "$current_device" ]] || { wrap_line 'No current camera is exposed; select a numbered camera.'; continue; }; device_key="$current_device"; device_label="$current_device_label"
    else device_key="${device_keys[number-1]}"; device_label="${device_labels[number-1]}"; fi
    break
    done
    # Device Select applies its last endpoint before the endpoint menu.
    local camera_committed=0 original_camera_id='' camera_clients='[]'
    trap 'if (( ! camera_committed )); then if [[ "$camera_clients" != "[]" ]]; then python3 "$TOPOLOGY_SCRIPT" --camera-restore-clients "$camera_clients" >/dev/null || echo "Camera client rollback failed" >&2; fi; if [[ "$original_camera_id" =~ ^[0-9]+$ ]]; then wpctl set-default "$original_camera_id" >/dev/null 2>&1 || echo "Camera rollback failed" >&2; fi; fi' EXIT
    original_camera="$current"
    original_camera_id="$(jq -r --arg n "$original_camera" '.[]|.nodes[]|select(.name==$n)|.id' <<<"$topo" | head -n 1)"
    [[ "$original_camera_id" =~ ^[0-9]+$ ]] || { wrap_line 'Cannot identify the current camera for rollback; no camera change was made.' >&2; return 1; }
    camera_last="$STATE_DIR/camera-last-endpoints.json"
    remembered_node="$(jq -r --arg k "$device_key" '.[$k] // empty' "$camera_last" 2>/dev/null || true)"
    stage_node="$(jq -r --arg k "$device_key" --arg n "$remembered_node" '.[]|select(.key==$k)|.nodes[]|select(.name==$n)|.id' <<<"$topo" | head -n 1)"
    [[ "$stage_node" =~ ^[0-9]+$ ]] || stage_node="$(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[0].id // empty' <<<"$topo")"
    [[ "$stage_node" =~ ^[0-9]+$ ]] || { wrap_line 'Selected camera has no endpoint.' >&2; return 1; }
    wpctl set-default "$stage_node" || return 1
    stage_name="$(jq -r --arg k "$device_key" --argjson id "$stage_node" '.[]|select(.key==$k)|.nodes[]|select(.id==$id)|.name' <<<"$topo")"
    wpctl list video sources | awk -F '\t' -v n="$stage_name" '$2==n && $4=="*"{ok=1} END{exit !ok}' || { wrap_line 'Camera device stage did not become default.' >&2; return 1; }
    current="$stage_name"
    mapfile -t node_ids < <(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[].id' <<<"$topo")
    mapfile -t node_names < <(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[].name' <<<"$topo")
    mapfile -t node_labels < <(jq -r --arg k "$device_key" '.[]|select(.key==$k)|.nodes[].label' <<<"$topo")
    while :; do
      current_node_label=''; for i in "${!node_names[@]}"; do [[ "${node_names[i]}" == "$current" ]] && current_node_label="${node_labels[i]}"; done
      choose_index "$device_label | Camera endpoint" "Current${current_node_label:+ ($(compact_item_label "$current_node_label"))}" "${node_labels[@]}" || { [[ "$original_camera_id" =~ ^[0-9]+$ ]] && wpctl set-default "$original_camera_id" >/dev/null 2>&1; return 1; }
      if ((number==0)); then
        if [[ -z "$current_node_label" ]]; then wrap_line 'No current endpoint belongs to this camera. Select again.'; continue; fi
        for i in "${!node_names[@]}"; do [[ "${node_names[i]}" == "$current" ]] && node_id="${node_ids[i]}" && node_name="${node_names[i]}" && node_label="${node_labels[i]}"; done
      else node_id="${node_ids[number-1]}"; node_name="${node_names[number-1]}"; node_label="${node_labels[number-1]}"; fi
      break
    done
    # Revalidate against a fresh graph, then set and confirm the default.
    fresh="$(camera_topology)" || return 1
    jq -e --arg k "$device_key" --argjson id "$node_id" --arg n "$node_name" '.[]|select(.key==$k)|.nodes[]|select(.id==$id and .name==$n)' <<<"$fresh" >/dev/null || { wrap_line 'Camera topology changed; reopen Media Control.'; return 1; }
    old_camera_id="$(jq -r --arg n "$original_camera" '.[] | .nodes[] | select(.name==$n) | .id' <<<"$topo" | head -n 1)"
    wpctl set-default "$node_id" || return 1
    if ! wpctl list video sources | awk -F '\t' -v n="$node_name" '$2==n && $4=="*"{ok=1} END{exit !ok}'; then
        [[ "$old_camera_id" =~ ^[0-9]+$ ]] && wpctl set-default "$old_camera_id" || true
        wrap_line 'Camera default change could not be confirmed.'; return 1
    fi
    if ! camera_status="$(python3 "$TOPOLOGY_SCRIPT" --camera-selection-status "$node_id")"; then
        [[ "$old_camera_id" =~ ^[0-9]+$ ]] && wpctl set-default "$old_camera_id" || true
        return 1
    fi
    camera_clients="$(jq -c '.previousClients // []' <<<"$camera_status")"
    if [[ -s "$camera_last" ]]; then camera_saved="$(cat "$camera_last")"; else camera_saved='{}'; fi
    if ! jq -n --argjson old "$camera_saved" --arg k "$device_key" --arg n "$node_name" '$old + {($k):$n}' >"$camera_last.tmp" || ! mv -- "$camera_last.tmp" "$camera_last"; then
        rm -f -- "$camera_last.tmp"
        wrap_line 'Could not save camera endpoint; restoring previous camera.' >&2
        return 1
    fi
    camera_committed=1
    wrap_line "Camera selected: $device_label${node_label:+ | $node_label}"
)
