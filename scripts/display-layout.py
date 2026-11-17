#!/usr/bin/env python3
"""Cold-start HDMI reset and one-shot first/second window placement."""
from __future__ import annotations
import json
import select
import subprocess
import sys
import time


def swaymsg(*args: str):
    result = subprocess.run(["swaymsg", "-r", *args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "swaymsg failed")
    data = json.loads(result.stdout)
    if isinstance(data, list) and data and all(isinstance(row, dict) and "success" in row for row in data):
        if not all(row.get("success") for row in data):
            raise RuntimeError(f"Sway rejected command: {data}")
    return data


def command(text: str) -> None:
    swaymsg(text)


def active_outputs():
    return [row for row in swaymsg("-t", "get_outputs") if row.get("active")]


def classify(outputs):
    internal = next((row for row in outputs if str(row.get("name", "")).startswith(("eDP-", "LVDS-", "DSI-"))), None)
    hdmi = next((row for row in outputs if str(row.get("name", "")).startswith("HDMI-")), None)
    return internal, hdmi


def wait_output(name: str, active: bool, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = next((row for row in swaymsg("-t", "get_outputs") if row.get("name") == name), None)
        if found is not None and bool(found.get("active")) == active:
            return
        time.sleep(0.1)
    raise RuntimeError(f"{name} did not become {'active' if active else 'inactive'}")


def eligible(container: dict) -> bool:
    rect = container.get("rect") or {}
    return (
        container.get("type") == "con"
        and isinstance(container.get("id"), int)
        and container.get("floating") in (None, "auto_off", "user_off")
        and int(rect.get("width", 0) or 0) > 0
        and int(rect.get("height", 0) or 0) > 0
    )


def main() -> int:
    # Wait briefly for startup output discovery. If HDMI is already active, cycle
    # only that output once. This reproduces the native post-login hot-plug order:
    # the laptop exists first and Sway then adds HDMI using its normal geometry.
    internal = hdmi = None
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        internal, hdmi = classify(active_outputs())
        if internal and hdmi:
            break
        time.sleep(0.2)
    if not internal or not hdmi:
        return 0

    internal_name = str(internal["name"])
    hdmi_name = str(hdmi["name"])
    command(f'output "{hdmi_name}" disable')
    wait_output(hdmi_name, False)
    time.sleep(0.35)
    command(f'output "{hdmi_name}" enable')
    wait_output(hdmi_name, True)

    # Move existing workspace 1 after the HDMI cycle, create workspace 2 on HDMI,
    # and return focus to the laptop. No output coordinates or pointer mapping.
    command(f'workspace number 1; move workspace to output "{internal_name}"')
    command(f'workspace number 2 output "{hdmi_name}"')
    command(f'workspace number 2; move workspace to output "{hdmi_name}"')
    command(f'workspace number 1; focus output "{internal_name}"')

    # Apply the requested first/second tiled-window split once, then terminate.
    process = subprocess.Popen(
        ["swaymsg", "-m", "-r", "-t", "subscribe", '["window"]'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    placed = 0
    placement_deadline = time.monotonic() + 300.0
    try:
        while placed < 2 and time.monotonic() < placement_deadline:
            ready, _, _ = select.select([process.stdout], [], [], 1.0)
            if not ready:
                if process.poll() is not None:
                    return process.returncode or 0
                continue
            line = process.stdout.readline()
            if not line:
                return process.wait()
            event = json.loads(line)
            if event.get("change") != "new":
                continue
            container = event.get("container") or {}
            if not eligible(container):
                continue
            target = 1 if placed == 0 else 2
            command(f'[con_id={container["id"]}] move container to workspace number {target}')
            command(f'workspace number {target}')
            placed += 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print(f"display-layout: {error}", file=sys.stderr)
        raise SystemExit(1)
