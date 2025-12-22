
#!/usr/bin/env bash
set -euo pipefail

GNOME_SCHEMA="org.gnome.system.location"

toggle_gnome_location() {
  local state="$1"
  if command -v gsettings >/dev/null 2>&1; then
    if gsettings list-schemas | grep -q "^${GNOME_SCHEMA}$"; then
      gsettings set "${GNOME_SCHEMA}" enabled "${state}" || true
    fi
  fi
}

get_timezone() {
  if command -v curl >/dev/null 2>&1; then
    local tz
    tz="$(curl -s --max-time 5 https://ipapi.co/timezone || true)"
    if [[ -n "${tz}" && "${tz}" =~ .*/.* ]]; then
      echo "${tz}"
      return 0
    fi
  fi
  echo "ERROR: Couldn’t resolve timezone from IP." >&2
  return 1
}

set_timezone() {
  local tz="$1"
  if sudo -n timedatectl set-timezone "${tz}"; then
    echo "✅ Timezone set to ${tz}"
  else
    sudo timedatectl set-timezone "${tz}"
  fi
}

main() {
  toggle_gnome_location true || true
  tz="$(get_timezone)" || exit 1
  set_timezone "${tz}"
  toggle_gnome_location false || true
}

main "$@"
