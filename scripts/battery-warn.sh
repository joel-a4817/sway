#!/usr/bin/env bash
# Battery warnings via swaynag: every 3 minutes after hitting 25%.
# Robust against sway reloads, suspend/resume, battery flaps, and nag stacking.

set -euo pipefail

# ---- configuration ----
POLL_SEC=30
NAG_INTERVAL_SEC=180
THRESHOLD=25
EXIT_HYSTERESIS=2
REENTRY_BACKOFF_SEC=90
SKIP_IF_SWAYNAG_RUNNING=true

STATE_DIR="${HOME}/.local/state"
LOG="${STATE_DIR}/battery-interval-swaynag.log"
LOCKFILE="${STATE_DIR}/battery-watcher.lock"

mkdir -p "$STATE_DIR"

# ---- autodetect battery ----
BAT_PATH=""
for p in /sys/class/power_supply/BAT*; do
  [[ -d "$p" ]] && BAT_PATH="$p" && break
done

if [[ -z "$BAT_PATH" ]]; then
  echo "$(date -Iseconds) [FATAL] no BAT* device found" >>"$LOG"
  exit 1
fi

# ---- single-instance lock ----
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "$(date -Iseconds) [INFO] another instance is running; exiting" >>"$LOG"
  exit 0
fi

export TERMINAL="${TERMINAL:-foot}"

echo "$(date -Iseconds) [START] watcher on ${BAT_PATH} (threshold=${THRESHOLD}%)" >>"$LOG"

low_mode=0
last_nag_time=0
last_exit_time=0

while true; do
  # ---- safe reads (survive suspend/resume) ----
  if ! read -r cap < "$BAT_PATH/capacity" 2>/dev/null \
     || ! read -r status < "$BAT_PATH/status" 2>/dev/null; then
    echo "$(date -Iseconds) [WARN] battery files unreadable; retrying" >>"$LOG"
    sleep "$POLL_SEC"
    continue
  fi

  cap=${cap//[[:space:]]/}
  now=${EPOCHSECONDS:-$(date +%s)}

  # ---- enter low-mode ----
  if [[ "$status" == "Discharging" && "$cap" =~ ^[0-9]+$ && $cap -le $THRESHOLD ]]; then
    if (( low_mode == 0 )); then
      if (( last_exit_time == 0 || now - last_exit_time >= REENTRY_BACKOFF_SEC )); then
        last_nag_time=0
      fi
      low_mode=1
      echo "$(date -Iseconds) [STATE] enter low-mode (cap=${cap}%)" >>"$LOG"
    fi
  else
    # ---- exit low-mode ----
    exit_due_to_status=0
    exit_due_to_hysteresis=0

    [[ "$status" != "Discharging" ]] && exit_due_to_status=1
    [[ "$cap" =~ ^[0-9]+$ && $cap -gt $((THRESHOLD + EXIT_HYSTERESIS)) ]] && exit_due_to_hysteresis=1

    if (( low_mode == 1 && (exit_due_to_status || exit_due_to_hysteresis) )); then
      low_mode=0
      last_exit_time=$now
      echo "$(date -Iseconds) [STATE] exit low-mode (status=$status cap=${cap}%)" >>"$LOG"
    fi
  fi

  # ---- nag logic ----
  if (( low_mode == 1 )); then
    if (( last_nag_time == 0 || now - last_nag_time >= NAG_INTERVAL_SEC )); then
      if [[ "$SKIP_IF_SWAYNAG_RUNNING" == "true" ]] && pgrep -x swaynag >/dev/null 2>&1; then
        echo "$(date -Iseconds) [INFO] swaynag already running; skip" >>"$LOG"
      else
        msg="Battery is at ${cap}%. Plug in now!"
        swaynag -t warning -y overlay -m "$msg" -s "OK"
        last_nag_time=$now
        echo "$(date -Iseconds) [WARN] ${msg}" >>"$LOG"
      fi
    fi
  fi

  sleep "$POLL_SEC"
done

