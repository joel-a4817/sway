
#!/usr/bin/env bash
# Battery warnings via swaynag: every 3 minutes after hitting 25%.
# Robust against sway reloads (single-instance lock), status flaps, and nag stacking.

set -euo pipefail

BAT_PATH="/sys/class/power_supply/BAT0"   # change to BAT1 if your machine uses that
POLL_SEC=30                               # lightweight polling
NAG_INTERVAL_SEC=180                      # 3 minutes
THRESHOLD=25                              # start warning at/below 25%
EXIT_HYSTERESIS=2                         # leave low-mode only when > THRESHOLD + 2%
REENTRY_BACKOFF_SEC=90                    # don't immediate-nag if we re-enter within this window
SKIP_IF_SWAYNAG_RUNNING=true              # avoid stacking multiple swaynag windows

STATE_DIR="${HOME}/.local/state"
LOG="${STATE_DIR}/battery-interval-swaynag.log"
LOCKFILE="${STATE_DIR}/battery-watcher.lock"
mkdir -p "${STATE_DIR}"

# ---- single-instance lock ----
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "$(date -Iseconds) [INFO] another instance is running; exiting" >> "$LOG"
  exit 0
fi

# Run swaynag actions inside foot (optional)
export TERMINAL="${TERMINAL:-foot}"

echo "$(date -Iseconds) [START] battery watcher (threshold=${THRESHOLD}%, every ${NAG_INTERVAL_SEC}s)" >> "$LOG"

low_mode=0             # 0=inactive, 1=active once under/equal threshold while discharging
last_nag_time=0        # epoch seconds
last_exit_time=0       # epoch seconds (for backoff on re-entry)

while true; do
  # Verify sysfs files exist
  if [[ ! -r "$BAT_PATH/capacity" || ! -r "$BAT_PATH/status" ]]; then
    echo "$(date -Iseconds) [ERR] $BAT_PATH not readable" >> "$LOG"
    sleep "$POLL_SEC"
    continue
  fi

  cap=$(<"$BAT_PATH/capacity"); cap=${cap//[[:space:]]/}
  status=$(<"$BAT_PATH/status")
  now=${EPOCHSECONDS:-$(date +%s)}

  # ---- enter/exit low-mode with hysteresis ----
  if [[ "$status" == "Discharging" && "$cap" =~ ^[0-9]+$ && $cap -le $THRESHOLD ]]; then
    if (( low_mode == 0 )); then
      # Only force immediate nag if we haven't just exited low-mode recently
      if (( last_exit_time == 0 || (now - last_exit_time) >= REENTRY_BACKOFF_SEC )); then
        last_nag_time=0  # immediate nag on first entry
      fi
      low_mode=1
      echo "$(date -Iseconds) [STATE] enter low-mode (cap=${cap}%)" >> "$LOG"
    fi
  else
    # Exit if charging/full OR above threshold + hysteresis
    exit_due_to_status=$([[ "$status" != "Discharging" ]] && echo 1 || echo 0)
    exit_due_to_hysteresis=$([[ "$cap" =~ ^[0-9]+$ && $cap -gt $((THRESHOLD + EXIT_HYSTERESIS)) ]] && echo 1 || echo 0)
    if (( low_mode == 1 && (exit_due_to_status == 1 || exit_due_to_hysteresis == 1) )); then
      low_mode=0
      last_exit_time=$now
      echo "$(date -Iseconds) [STATE] exit low-mode (status=$status cap=${cap}%)" >> "$LOG"
    fi
  fi

  # ---- nag on interval while in low-mode ----
  if (( low_mode == 1 )); then
    if (( last_nag_time == 0 || (now - last_nag_time) >= NAG_INTERVAL_SEC )); then
      # Avoid stacking multiple swaynag windows
      if [[ "$SKIP_IF_SWAYNAG_RUNNING" == "true" ]] && pgrep -x swaynag >/dev/null 2>&1; then
        echo "$(date -Iseconds) [INFO] swaynag already running; skip new nag" >> "$LOG"
      else
        msg="Battery is at ${cap}%. Plug in now!"
        swaynag -t warning -y overlay -m "$msg" -s "OK"
        last_nag_time=$now
        echo "$(date -Iseconds) [WARN] ${msg} (nagged)" >> "$LOG"
      fi
    else
      remaining=$(( NAG_INTERVAL_SEC - (now - last_nag_time) ))
      echo "$(date -Iseconds) [INFO] low-mode cap=${cap}% (next nag in ${remaining}s)" >> "$LOG"
    fi
  else
    echo "$(date -Iseconds) [INFO] status=$status cap=${cap}% (no nag)" >> "$LOG"
  fi

  sleep "$POLL_SEC"
done

