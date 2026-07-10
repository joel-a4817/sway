#!/usr/bin/env bash

waydroid app launch com.metrolist.music &

while ! swaymsg -t get_tree | grep -iq "Metrolist"; do
    sleep 0.5
done

while swaymsg -t get_tree | grep -iq "Metrolist"; do
    sleep 0.5
done

# Don't stop if full Waydroid is still open
if ! swaymsg -t get_tree | grep -q "Waydroid"; then
    waydroid session stop
fi
