#!/usr/bin/env bash
set -euo pipefail

MINECRAFT="$HOME/.config/sway/scripts/network/minecraft-server.py"
IPAD="$HOME/.config/sway/scripts/network/start-ipad-webremote.sh"
VNC_UNIT="camilladsp-wayvnc.service"
PEERS="$HOME/.config/minecraft-server/peers"

mapfile -t SERVERS < <("$MINECRAFT" list)
declare -A LOCAL=()
declare -A REMOTE=()
declare -A REMOTE_TRANSITION=()
peer_error=""

# Local truth: systemd on this laptop.
for server in "${SERVERS[@]}"; do
    state="$(systemctl is-active "minecraft-server-${server}.service" 2>/dev/null || true)"
    LOCAL["$server"]="${state:-unknown}"
done

# Remote truth: systemd directly on each configured peer. Do not depend on
# minecraft-server.py status parsing, floating-IP state, caches, or host prompts.
if [[ -s "$PEERS" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        read -ra fields <<< "$line"
        ((${#fields[@]} >= 2)) || continue
        label="${fields[0]//[|,;]/}"
        reply=""
        errors=()
        verified=0
        for address in "${fields[@]:1}"; do
            address="${address//[|,;]/}"
            [[ -n "$address" ]] || continue
            # First verify SSH once. Then query each unit separately so an
            # inactive unit (systemctl exit 3) cannot make the whole peer look
            # unreachable and shell quoting cannot corrupt the server list.
            if ! output="$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$address" true 2>&1)"; then
                errors+=("$address: ${output:-SSH verification failed}")
                continue
            fi
            verified=1
            for server in "${SERVERS[@]}"; do
                if ssh -o BatchMode=yes -o ConnectTimeout=4 "$address" \
                    systemctl is-active --quiet "minecraft-server-${server}.service"; then
                    reply+="${reply:+$'\n'}$server"
                else
                    rc=$?
                    # systemctl returns 3 when the known unit is simply inactive.
                    if ((rc != 3)); then
                        errors+=("$address: status check for $server exited $rc")
                        verified=0
                        break
                    fi
                fi
            done
            if ((verified)); then
                machine="$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$address" \
                    '~/.config/sway/scripts/network/minecraft-server.py status --machine' 2>&1 || true)"
                protocol="$(printf '%s\n' "$machine" | awk '/^(running|stopped|transitioning)( |$)/ {line=$0} END {print line}')"
                [[ "$protocol" == transitioning ]] && REMOTE_TRANSITION["$label"]=1
                break
            fi
        done
        if ((! verified)); then
            peer_error+="${peer_error:+; }$label (${errors[*]:-no usable address})"
            continue
        fi
        while IFS= read -r server; do
            [[ -n "$server" ]] || continue
            REMOTE["$server"]="${REMOTE[$server]:+${REMOTE[$server]},}$label"
        done <<< "$reply"
    done < "$PEERS"
else
    peer_error="peers file missing or empty: $PEERS"
fi

printf '%s\n' 'Toggle services'
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
    local_state="${LOCAL[$server]:-unknown}"
    remote_running="${REMOTE[$server]:-}"
    if [[ "$local_state" == active && -n "$remote_running" ]]; then
        printf '  [%d] Minecraft: %s [CONFLICT: local + %s]\n' "$number" "$server" "$remote_running"
    elif [[ "$local_state" == active ]]; then
        printf '  [%d] Minecraft: %s [running here]\n' "$number" "$server"
    elif [[ -n "$remote_running" ]]; then
        printf '  [%d] Minecraft: %s [running on %s]\n' "$number" "$server" "$remote_running"
    elif [[ "$local_state" == failed ]]; then
        printf '  [%d] Minecraft: %s [FAILED here]\n' "$number" "$server"
    elif [[ "$local_state" == activating || "$local_state" == deactivating ]]; then
        printf '  [%d] Minecraft: %s [%s here]\n' "$number" "$server" "$local_state"
    else
        printf '  [%d] Minecraft: %s\n' "$number" "$server"
    fi
done
if ((${#REMOTE_TRANSITION[@]})); then
    transitioning="$(IFS=,; echo "${!REMOTE_TRANSITION[*]}")"
    printf '  [~] Minecraft transition in progress on: %s\n' "$transitioning"
fi
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
