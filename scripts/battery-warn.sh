
#!/usr/bin/env bash

BAT="/sys/class/power_supply/BAT0"

while true; do
    cap=$(cat "$BAT/capacity")
    status=$(cat "$BAT/status")

    if [ "$status" = "Discharging" ] && [ "$cap" -le 5 ]; then
        swaynag -t warning \
            -m "Battery is at ${cap}%. Plug in now!" \
            -B "OK"
        sleep 60   # prevents spamming another popup immediately
    fi

    sleep 20
done

