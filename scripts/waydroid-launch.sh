#!/usr/bin/env bash

waydroid show-full-ui &

while ! swaymsg -t get_tree | grep -q "Waydroid"; do
    sleep 0.5
done

while swaymsg -t get_tree | grep -q "Waydroid"; do
    sleep 0.5
done

# Don't stop if MetroList is still open
if ! swaymsg -t get_tree | grep -iq "Metrolist"; then
    waydroid session stop
fi
