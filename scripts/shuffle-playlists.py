#!/usr/bin/env python3
import os
import random
import sys
import termios
import tty
import tempfile
from pathlib import Path

MUSIC_DIR = Path.home() / "Downloads" / "Music"


def pause_before_close() -> None:
    print("\nPress any key to close...", end="", flush=True)
    try:
        fd = sys.stdin.fileno()
        settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, settings)
    except (EOFError, OSError, termios.error):
        return
    print()


def natural_key(path: Path):
    import re
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", path.name)]


def shuffle_playlist(source: Path) -> None:
    output = source.with_name(f"{source.stem}-shuffled{source.suffix}")
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    entries = [
        line for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]

    if not entries:
        print(f"Skipped empty playlist: {source.name}")
        return

    random.shuffle(entries)
    content = "#EXTM3U\n" + "\n".join(entries) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent, text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    print(f"Shuffled {len(entries)} tracks: {source.name} -> {output.name}")


def main() -> None:
    if not MUSIC_DIR.is_dir():
        raise SystemExit(f"Music directory not found: {MUSIC_DIR}")

    playlists = sorted(
        (
            path for path in MUSIC_DIR.glob("*.m3u")
            if not path.stem.endswith("-shuffled")
            and (MUSIC_DIR / path.stem).is_dir()
        ),
        key=natural_key,
    )

    if not playlists:
        print(f"No source playlists found in: {MUSIC_DIR}")
        pause_before_close()
        return

    print("\nPlaylist Shuffle\n")
    print("[0] Exit without shuffling")
    for number, playlist in enumerate(playlists, start=1):
        print(f"[{number}] {playlist.stem}")

    all_choice = len(playlists) + 1
    print(f"[{all_choice}] Shuffle all playlists\n")

    try:
        choice = int(input("Select playlist: ").strip())
    except ValueError:
        print("Invalid selection.")
        pause_before_close()
        raise SystemExit(1)

    if choice == 0:
        print("No playlists shuffled.")
    elif choice == all_choice:
        for playlist in playlists:
            shuffle_playlist(playlist)
        print("\nAll playlists shuffled.")
    elif 1 <= choice <= len(playlists):
        shuffle_playlist(playlists[choice - 1])
    else:
        print("Invalid selection.")
        pause_before_close()
        raise SystemExit(1)

    pause_before_close()


if __name__ == "__main__":
    main()
