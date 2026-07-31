
#!/usr/bin/env bash
#! nix-shell -i bash -p gsettings-desktop-schemas

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
    local tz

    tz="$(curl -fsS --max-time 5 https://ipinfo.io/timezone)" || {
        echo "ERROR: Could not determine timezone" >&2
        return 1
    }

    if timedatectl list-timezones | grep -Fxq "$tz"; then
        echo "$tz"
        return 0
    fi

    echo "ERROR: Invalid timezone returned: '$tz'" >&2
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
