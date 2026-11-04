#!/usr/bin/env bash
set -Eeuo pipefail
D="${XDG_STATE_HOME:-$HOME/.local/state}/sway/windows";G="$D/geometry";mkdir -p -m700 "$G"
P="$D/float-geometry-watcher.pid";L="$D/float-geometry-watcher.lock";T="$D/float-transition.lock"
if [[ ${1:-} == --replace && -r $P ]];then read -r p <"$P"||p=;if [[ $p =~ ^[0-9]+$ && -r /proc/$p/cmdline ]]&&tr '\0' ' ' <"/proc/$p/cmdline"|grep -Fq 'float-geometry';then kill -TERM "$p" 2>/dev/null||:;for _ in {1..50};do kill -0 "$p" 2>/dev/null||break;sleep .02;done;kill -KILL "$p" 2>/dev/null||:;fi;fi
exec 9>"$L";flock -w3 9||exit 1;echo $$>"$P";chmod 600 "$P" "$L"
cleanup(){ [[ -r $P ]]&&read -r p <"$P"&&[[ $p == $$ ]]&&rm -f "$P"||:;};trap cleanup EXIT INT TERM HUP
safe(){ printf %s "$1"|tr -c 'A-Za-z0-9._-' _;}
snap(){ [[ -e $T ]]&&return;t=$(swaymsg -r -t get_tree 2>/dev/null)||return;while IFS= read -r c;do k=$(jq -r 'if(.app_id?//"")!="" then "app_"+.app_id elif(.window_properties.class?//"")!="" then "class_"+.window_properties.class elif(.window_properties.instance?//"")!="" then "instance_"+.window_properties.instance else "pid_"+((.pid//0)|tostring) end'<<<"$c");f="$G/$(safe "$k").json";x=$(mktemp "$G/.geometry.XXXXXX");jq -c '{x:.rect.x,y:.rect.y,width:.rect.width,height:.rect.height}'<<<"$c">"$x";chmod 600 "$x";if [[ -f $f ]]&&cmp -s "$x" "$f";then rm -f "$x";else mv -f "$x" "$f";fi;done < <(jq -c '..|objects|select((.type?=="con" or .type?=="floating_con") and .floating?=="user_on" and (.fullscreen_mode?//0)==0)|select((.app_id?!=null) or (.window?!=null))'<<<"$t");}
while sleep .15;do snap;done
