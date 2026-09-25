#!/usr/bin/env bash
set -euo pipefail

music_dir="$HOME/Downloads/Music"

pause_before_close() {
    echo
    if [[ -r /dev/tty && -w /dev/tty ]]; then
        read -n 1 -r -s -p "Press any key to close..." </dev/tty || true
        echo >/dev/tty
    fi
}

update_playlist() {
    local src="$1"
    local folder playlist temporary
    folder="$(basename "$src")"
    playlist="$music_dir/$folder.m3u"
    temporary="$(mktemp --tmpdir="$music_dir" ".${folder}.m3u.XXXXXX")"

    {
        echo '#EXTM3U'
        find "$src" -maxdepth 1 -type f \
            \( -iname '*.m4a' \
            -o -iname '*.aac' \
            -o -iname '*.mp3' \
            -o -iname '*.flac' \
            -o -iname '*.wav' \
            -o -iname '*.ogg' \
            -o -iname '*.opus' \) \
            -printf '%f\n' |
        sort -V |
        while IFS= read -r file; do
            printf '%s/%s\n' "$folder" "$file"
        done
    } >"$temporary"

    mv -f -- "$temporary" "$playlist"
    printf 'Updated: %s\n' "$playlist"
}

[[ -d "$music_dir" ]] || {
    echo "Music directory not found: $music_dir"
    pause_before_close
    exit 1
}

mapfile -d '' -t folders < <(
    find "$music_dir" -mindepth 1 -maxdepth 1 -type d -print0 |
        sort -z -V
)

if ((${#folders[@]} == 0)); then
    echo "No playlist folders found in: $music_dir"
    pause_before_close
    exit 0
fi

echo
echo "Playlist Update"
echo
echo '[0] Exit without updating'
for i in "${!folders[@]}"; do
    printf '[%d] %s\n' "$((i + 1))" "$(basename "${folders[$i]}")"
done
all_choice=$((${#folders[@]} + 1))
printf '[%d] Update all playlists\n' "$all_choice"
echo
read -r -p "Select playlist: " choice

if [[ "$choice" == 0 ]]; then
    echo "No playlists updated."
    pause_before_close
    exit 0
fi

[[ "$choice" =~ ^[0-9]+$ ]] || {
    echo "Invalid selection."
    pause_before_close
    exit 1
}

if ((choice == all_choice)); then
    for folder in "${folders[@]}"; do
        update_playlist "$folder"
    done
    echo
    echo "All playlists updated."
elif ((choice >= 1 && choice <= ${#folders[@]})); then
    update_playlist "${folders[$((choice - 1))]}"
else
    echo "Invalid selection."
    pause_before_close
    exit 1
fi

pause_before_close
