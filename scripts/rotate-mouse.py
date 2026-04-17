#!/usr/bin/env python3
import argparse
import signal
import sys
from evdev import InputDevice, UInput, ecodes

REL_X = ecodes.REL_X
REL_Y = ecodes.REL_Y

current_rotation = args.rot   # or however you store it
def reset_to_normal(signum, frame):
    global current_rotation
    current_rotation = 0

signal.signal(signal.SIGHUP, reset_to_normal)

def rotate(dx, dy, rot):
    if rot == 0:
        return dx, dy
    elif rot == 90:
        return -dy, dx
    elif rot == 180:
        return -dx, -dy
    elif rot == 270:
        return dy, -dx
    else:
        raise ValueError("Rotation must be 0/90/180/270")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", required=True)
    ap.add_argument("--rot", type=int, choices=[0, 90, 180, 270], default=0)
    args = ap.parse_args()

    src = InputDevice(args.dev)
    src.grab()

    caps = src.capabilities()

    # ✅ FILTER capabilities so UInput accepts them
    ui_caps = {}

    if ecodes.EV_REL in caps:
        ui_caps[ecodes.EV_REL] = caps[ecodes.EV_REL]

    if ecodes.EV_KEY in caps:
        # only mouse buttons
        ui_caps[ecodes.EV_KEY] = [
            code for code in caps[ecodes.EV_KEY]
            if code >= ecodes.BTN_LEFT and code <= ecodes.BTN_TASK
        ]

    ui = UInput(
        ui_caps,
        name=f"Rotated Mouse {args.rot}",
    )

    acc_dx = 0
    acc_dy = 0

    def cleanup(*_):
        try:
            src.ungrab()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    for ev in src.read_loop():
        if ev.type == ecodes.EV_REL:
            if ev.code == REL_X:
                acc_dx += ev.value
            elif ev.code == REL_Y:
                acc_dy += ev.value
            else:
                ui.write(ev.type, ev.code, ev.value)

        elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_REPORT:
            if acc_dx or acc_dy:
                rx, ry = rotate(acc_dx, acc_dy, args.rot)
                ui.write(ecodes.EV_REL, REL_X, rx)
                ui.write(ecodes.EV_REL, REL_Y, ry)

            ui.syn()
            acc_dx = 0
            acc_dy = 0

        else:
            ui.write(ev.type, ev.code, ev.value)

if __name__ == "__main__":
    main()
