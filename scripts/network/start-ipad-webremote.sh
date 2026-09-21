#!/usr/bin/env bash

exec ssh -t mobile@iPad4817 '
  echo "Stopping previous iPad web remote..."

  ps ax -o pid= -o command= |
  while read -r pid command; do
    case "$command" in
      "/var/jb/usr/bin/python3 server.py"*)
        echo "Killing detached server PID: $pid"
        kill -9 "$pid" 2>/dev/null || true
        ;;

      "/var/jb/usr/bin/python3 /var/mobile/webremote/server.py"*)
        echo "Killing previous server PID: $pid"
        kill -9 "$pid" 2>/dev/null || true
        ;;
    esac
  done

  sleep 1

  echo "Starting iPad web remote..."

  cd /var/mobile/webremote ||
  exit 1

  exec /var/jb/usr/bin/python3 \
    /var/mobile/webremote/server.py
'
