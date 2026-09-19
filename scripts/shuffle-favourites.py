#!/usr/bin/env python3

import argparse
import os
import random
import tempfile
from pathlib import Path

DEFAULT_SOURCE = Path("/home/joel/Downloads/Music/favourites.m3u")
DEFAULT_OUTPUT = Path("/home/joel/Downloads/Music/favourites-shuffled.m3u")


def main():
    parser = argparse.ArgumentParser(
        description="Shuffle the entries in favourites.m3u and replace favourites-shuffled.m3u."
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"source playlist (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output playlist (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    source = args.source.expanduser()
    output = args.output.expanduser()

    if not source.is_file():
        raise SystemExit(f"Playlist not found: {source}")

    lines = source.read_text(encoding="utf-8-sig").splitlines()

    entries = [
        line
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]

    if not entries:
        raise SystemExit(f"No playlist entries found in: {source}")

    random.shuffle(entries)

    output.parent.mkdir(parents=True, exist_ok=True)
    content = "#EXTM3U\n" + "\n".join(entries) + "\n"

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        dir=output.parent,
        text=True,
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

    print(f"Shuffled {len(entries)} tracks")
    print(f"Source: {source}")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
