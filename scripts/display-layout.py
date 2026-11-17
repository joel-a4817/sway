#!/usr/bin/env python3
"""One-shot HDMI cold-start correction; returns only when routing is ready."""
from __future__ import annotations
import json
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


def outputs():
    return swaymsg("-t", "get_outputs")


def classify(rows):
    internal = next((row for row in rows if row.get("active") and str(row.get("name", "")).startswith(("eDP-", "LVDS-", "DSI-"))), None)
    hdmi = next((row for row in rows if str(row.get("name", "")).startswith("HDMI-")), None)
    return internal, hdmi


def wait_for_outputs() -> tuple[dict | None, dict | None]:
    # State-driven startup discovery. The deadline is only a failure bound; it does
    # not delay success once both outputs actually exist.
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        internal, hdmi = classify(outputs())
        if internal:
            return internal, hdmi
        time.sleep(0.05)
    return None, None


def wait_for_state(name: str, active: bool) -> None:
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        row = next((item for item in outputs() if item.get("name") == name), None)
        if row is not None and bool(row.get("active")) == active:
            return
        time.sleep(0.05)
    raise RuntimeError(f"{name} did not become {'active' if active else 'inactive'}")


def main() -> int:
    internal, hdmi = wait_for_outputs()
    if not internal or not hdmi:
        return 0
    internal_name = str(internal["name"])
    hdmi_name = str(hdmi["name"])

    # Reproduce the native order that works when HDMI is connected after login.
    command(f'output "{hdmi_name}" disable')
    wait_for_state(hdmi_name, False)
    command(f'output "{hdmi_name}" enable')
    wait_for_state(hdmi_name, True)

    # No coordinates, calibration, pointer mapping, timers, or
    # continuous subscriptions. Correct current workspace ownership once and exit.
    command(f'workspace number 1; move workspace to output "{internal_name}"')
    command(f'workspace number 2 output "{hdmi_name}"')
    command(f'workspace number 2; move workspace to output "{hdmi_name}"')
    command(f'workspace number 1; focus output "{internal_name}"')
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print(f"display-layout: {error}", file=sys.stderr)
        raise SystemExit(1)
