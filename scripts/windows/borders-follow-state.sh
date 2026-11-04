#!/usr/bin/env bash
set -Eeuo pipefail
D="${XDG_STATE_HOME:-$HOME/.local/state}/sway/windows"; mkdir -p -m700 "$D"
P="$D/borders-follow-state.pid"; L="$D/borders-follow-state.lock"
if [[ ${1:-} == --replace && -r $P ]]; then read -r p <"$P" || p=; if [[ $p =~ ^[0-9]+$ && -r /proc/$p/cmdline ]] && tr '\0' ' ' <"/proc/$p/cmdline" | grep -Fq 'borders-follow-state'; then kill -TERM "$p" 2>/dev/null ||:; for _ in {1..50}; do kill -0 "$p" 2>/dev/null||break;sleep .02;done; kill -KILL "$p" 2>/dev/null||:;fi;fi
exec 9>"$L"; flock -w3 9||exit 1; echo $$ >"$P"; chmod 600 "$P" "$L"
cleanup(){ [[ -r $P ]]&&read -r p <"$P"&&[[ $p == $$ ]]&&rm -f "$P"||:;};trap cleanup EXIT INT TERM HUP
reconcile(){ t=$(swaymsg -r -t get_tree 2>/dev/null)||return;while IFS=$'\t' read -r id f b;do [[ $id =~ ^[0-9]+$ ]]||continue;if [[ $f == user_on || $f == auto_on ]];then n=8;else n=2;fi;[[ $b == "$n" ]]||swaymsg -q "[con_id=$id] border pixel $n"||:;done < <(jq -r '..|objects|select((.type?=="con" or .type?=="floating_con") and (.id?!=null))|select((.app_id?!=null) or (.window?!=null))|[.id,(.floating//"user_off"),(.current_border_width//-1)]|@tsv'<<<"$t");}
while sleep .25;do reconcile;done
