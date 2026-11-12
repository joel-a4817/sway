#!/usr/bin/env bash
set -euo pipefail

MINECRAFT="$HOME/.config/sway/scripts/network/minecraft-server.py"
IPAD="$HOME/.config/sway/scripts/network/start-ipad-webremote.sh"
VNC_UNIT="camilladsp-wayvnc.service"
PEERS="$HOME/.config/minecraft-server/peers"

mapfile -t SERVERS < <("$MINECRAFT" list)
THIS_DEVICE="$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//; s/\..*$//' || true)"
[[ -n "$THIS_DEVICE" ]] || THIS_DEVICE="$(hostname -s)"
declare -A LOCAL=()
declare -A REMOTE=()
peer_error=""

# Local truth: systemd on this laptop.
for server in "${SERVERS[@]}"; do
    systemctl is-active --quiet "minecraft-server-${server}.service" && LOCAL["$server"]=1 || true
done

# Remote truth: systemd directly on each configured peer. Do not depend on
# minecraft-server.py status parsing, floating-IP state, caches, or host prompts.
if [[ -s "$PEERS" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        read -ra fields <<< "$line"
        ((${#fields[@]} >= 2)) || continue
        label="${fields[0]//[|,;]/}"
        verified=0
        errors=()
        for address in "${fields[@]:1}"; do
            address="${address//[|,;]/}"
            [[ -n "$address" ]] || continue
            # One SSH invocation returns an exact ActiveState for every unit.
            # systemctl show exits successfully for inactive units, unlike
            # systemctl is-active, so stopped peers cannot be mistaken for SSH failures.
            remote="$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$address"                 systemctl show --property=ActiveState --value                 "${SERVERS[@]/#/minecraft-server-}" 2>&1)" || {
                errors+=("$address: $remote")
                continue
            }
            mapfile -t states <<< "$remote"
            if ((${#states[@]} != ${#SERVERS[@]})); then
                errors+=("$address: expected ${#SERVERS[@]} unit states, got ${#states[@]}")
                continue
            fi
            verified=1
            for i in "${!SERVERS[@]}"; do
                [[ "${states[$i]}" == active ]] || continue
                name="${SERVERS[$i]}"
                REMOTE["$name"]="${REMOTE[$name]:+${REMOTE[$name]},}$label"
            done
            break
        done
        if ((! verified)); then
            peer_error+="${peer_error:+; }$label (${errors[*]:-no usable address})"
        fi
    done < "$PEERS"
else
    peer_error="peers file missing or empty: $PEERS"
fi

printf 'Toggle services [this device: %s]\n' "$THIS_DEVICE"
printf '%s\n' 'Enter comma-separated numbers. [0] exits.'
printf '  [E] Exit\n'
if "$IPAD" --status >/dev/null 2>&1; then
    printf '  [1] iPad Web Remote [running]\n'
else
    printf '  [1] iPad Web Remote\n'
fi
if systemctl --user is-active --quiet "$VNC_UNIT"; then
    printf '  [2] VNC [running]\n'
else
    printf '  [2] VNC\n'
fi

for i in "${!SERVERS[@]}"; do
    server="${SERVERS[$i]}"
    number=$((i + 3))
    local_running="${LOCAL[$server]:-}"
    remote_running="${REMOTE[$server]:-}"
    if [[ -n "$local_running" && -n "$remote_running" ]]; then
        printf '  [%d] Minecraft: %s [CONFLICT: local + %s]\n' "$number" "$server" "$remote_running"
    elif [[ -n "$local_running" ]]; then
        printf '  [%d] Minecraft: %s [running here]\n' "$number" "$server"
    elif [[ -n "$remote_running" ]]; then
        printf '  [%d] Minecraft: %s [running on %s]\n' "$number" "$server" "$remote_running"
    else
        printf '  [%d] Minecraft: %s\n' "$number" "$server"
    fi
done
[[ -z "$peer_error" ]] || printf '  [!] Peer unavailable: %s\n' "$peer_error"

read -r -p 'Selection: ' input
[[ -n "${input//[[:space:]]/}" ]] || exit 0
[[ "$input" =~ ^[Ee]$ ]] && exit 0
IFS=',' read -ra tokens <<< "$input"
declare -A SEEN=()
for token in "${tokens[@]}"; do
    token="${token//[[:space:]]/}"
    [[ "$token" =~ ^[0-9]+$ ]] || { printf 'Invalid selection: %s\n' "$token" >&2; exit 2; }
    ((token == 0)) && exit 0
    ((token >= 1 && token <= ${#SERVERS[@]} + 2)) || { printf 'Selection out of range: %s\n' "$token" >&2; exit 2; }
    [[ -z "${SEEN[$token]:-}" ]] || continue
    SEEN[$token]=1
    case "$token" in
        1) "$IPAD" toggle ;;
        2)
            if systemctl --user is-active --quiet "$VNC_UNIT"; then
                systemctl --user stop "$VNC_UNIT"
            else
                systemctl --user start "$VNC_UNIT"
            fi
            ;;
        *) "$MINECRAFT" toggle "${SERVERS[$((token - 3))]}" ;;
    esac
done
