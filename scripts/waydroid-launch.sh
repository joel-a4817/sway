#!/usr/bin/env bash

waydroid show-full-ui &

# wait until window appears
while ! swaymsg -t get_tree | grep -q "Waydroid"; do
    sleep 0.5
done

# now wait until it disappears (i.e., you close it)
while swaymsg -t get_tree | grep -q "Waydroid"; do
    sleep 0.5
done

waydroid session stop
