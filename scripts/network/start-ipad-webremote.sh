#!/usr/bin/env bash

exec ssh -t mobile@iPad4817 '
  ps ax |
  while read -r pid tty state time executable argument remainder; do
    if [ "$executable" = "/var/jb/usr/bin/python3" ] &&
       [ "$argument" = "/var/mobile/webremote/server.py" ]; then
      echo "Killing previous server PID: $pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  done

  sleep 1

  echo "Starting iPad web remote..."

  exec /var/jb/usr/bin/python3 \
    /var/mobile/webremote/server.py
'
