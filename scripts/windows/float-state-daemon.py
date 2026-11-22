#!/usr/bin/env python3
"""Remember and restore floating geometry for each live Sway container."""
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "sway/windows"
STATE_FILE = STATE_DIR / "floating-geometry.json"
LOCK_FILE = STATE_DIR / "floating-geometry.lock"
POLL_SECONDS = 0.04
MIN_WIDTH = 100
MIN_HEIGHT = 80
MAX_DIMENSION = 16384


def sway_tree():
    return json.loads(subprocess.check_output(
        ["swaymsg", "-r", "-t", "get_tree"], text=True, stderr=subprocess.DEVNULL
    ))


def windows(node):
    if not isinstance(node, dict):
        return
    if node.get("type") in ("con", "floating_con") and (node.get("app_id") or node.get("window")):
        yield node
    for key in ("nodes", "floating_nodes"):
        for child in node.get(key, []):
            yield from windows(child)


def geometry(node):
    rect = node.get("rect") or {}
    try:
        value = {key: int(rect[key]) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if not (MIN_WIDTH <= value["width"] <= MAX_DIMENSION and MIN_HEIGHT <= value["height"] <= MAX_DIMENSION):
        return None
    return value


def is_floating(node):
    return node.get("floating") in ("user_on", "auto_on")


def load_state():
    try:
        value = json.loads(STATE_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(value):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE_FILE)


def restore(container_id, value):
    command = (
        f'[con_id={container_id}] resize set width {value["width"]} px '
        f'height {value["height"]} px, move position {value["x"]} px {value["y"]} px'
    )
    subprocess.run(["swaymsg", "--", command], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state = load_state()
        previous = {}
        pending_restore = {}
        while os.environ.get("SWAYSOCK") and Path(os.environ["SWAYSOCK"]).exists():
            try:
                current_nodes = list(windows(sway_tree()))
            except (OSError, subprocess.SubprocessError, ValueError):
                time.sleep(POLL_SECONDS)
                continue
            present = set()
            dirty = False
            for node in current_nodes:
                container_id = str(node["id"])
                present.add(container_id)
                floating = is_floating(node)
                prior = previous.get(container_id)
                saved = state.get(container_id)
                current_geometry = geometry(node)

                if floating and prior is False and saved:
                    # Re-floating a live window restores its last floating rectangle.
                    # Keep the target pending until Sway reports it, so the temporary
                    # default float rectangle cannot overwrite the saved rectangle.
                    pending_restore[container_id] = {"geometry": saved, "until": time.monotonic() + 1.0}
                    restore(container_id, saved)
                elif floating and container_id in pending_restore:
                    pending = pending_restore[container_id]
                    if current_geometry == pending["geometry"]:
                        pending_restore.pop(container_id, None)
                    elif time.monotonic() < pending["until"]:
                        restore(container_id, pending["geometry"])
                    else:
                        pending_restore.pop(container_id, None)
                elif floating and current_geometry and current_geometry != saved:
                    # This covers windows opened floating, first-time floats, and every
                    # later user move/resize while floating.
                    state[container_id] = current_geometry
                    dirty = True
                elif not floating:
                    pending_restore.pop(container_id, None)
                previous[container_id] = floating

            # Container IDs identify a particular live window. Forget records only
            # after that window closes; tiled windows retain their floating rectangle.
            for container_id in list(previous):
                if container_id not in present:
                    previous.pop(container_id, None)
                    pending_restore.pop(container_id, None)
                    if state.pop(container_id, None) is not None:
                        dirty = True
            if dirty:
                save_state(state)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
