#!/usr/bin/env bash

RESULT_DIR="/home/joel/.local/state/sway/updaters"
RESULT_FILE="$RESULT_DIR/pixymon-update-complete.$$"

mkdir -p "$RESULT_DIR"
rm -f "$RESULT_FILE"

swaynag \
    -t warning \
    -y overlay \
    -m "Pixymon Updater" \
    -z "Update Pixymon and copy udev rules" \
'
cd ~/pixy2 || exit 1

git pull --ff-only || exit 1

cd scripts || exit 1
./build_pixymon_src.sh || exit 1

cp ~/pixy2/src/host/linux/pixy.rules ~/nixos || exit 1

touch "'"$RESULT_FILE"'"
' &

while [[ ! -f "$RESULT_FILE" ]]; do
    sleep 0.1
done

rm -f "$RESULT_FILE"

echo
echo "Remember to run a NixOS rebuild."
read -n 1 -rsp "Press any key to close..."
