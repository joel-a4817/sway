#!/usr/bin/env python3
import argparse
from evdev import InputDevice, UInput, ecodes
from evdev import AbsInfo

# Codes to rotate (absolute coordinates)
ABS_X = ecodes.ABS_X
ABS_Y = ecodes.ABS_Y
MT_X  = ecodes.ABS_MT_POSITION_X
MT_Y  = ecodes.ABS_MT_POSITION_Y
MT_SLOT = ecodes.ABS_MT_SLOT

current_rotation = args.rot   # or however you store it
def reset_to_normal(signum, frame):
    global current_rotation
    current_rotation = 0

signal.signal(signal.SIGHUP, reset_to_normal)

def rotate_xy(x, y, max_x, max_y, rot):
    # rot in {0, 90, 180, 270} clockwise
    if rot == 0:
        return x, y
    elif rot == 90:
        return max_y - y, x
    elif rot == 180:
        return max_x - x, max_y - y
    elif rot == 270:
        return y, max_x - x
    else:
        raise ValueError("Rotation must be 0/90/180/270")

def swapped_absinfo(ai: AbsInfo) -> AbsInfo:
    # Return a copy (python-evdev AbsInfo is immutable-ish)
    return AbsInfo(value=ai.value, min=ai.min, max=ai.max, fuzz=ai.fuzz, flat=ai.flat, resolution=ai.resolution)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", required=True, help="Touchpad device node (use stable symlink we create)")
    ap.add_argument("--rot", type=int, choices=[0, 90, 180, 270], default=0)
    args = ap.parse_args()

    src = InputDevice(args.dev)
    src.grab()  # prevent compositor from reading the real device

    caps = src.capabilities(absinfo=True)

    caps.pop(ecodes.EV_SYN, None)
    caps.pop(ecodes.EV_MSC, None)  # drops MSC_TIMESTAMP

    # Pull ABS axis info
    abs_entries = dict(caps.get(ecodes.EV_ABS, []))
    if ABS_X not in abs_entries or ABS_Y not in abs_entries:
        raise RuntimeError("Device lacks ABS_X/ABS_Y, cannot rotate.")

    in_x = abs_entries[ABS_X]
    in_y = abs_entries[ABS_Y]
    max_x = in_x.max
    max_y = in_y.max

    # For 90/270 rotate: swap axis ranges on virtual device
    if args.rot in (90, 270):
        out_x = AbsInfo(value=in_x.value, min=in_x.min, max=max_y, fuzz=in_x.fuzz, flat=in_x.flat, resolution=in_x.resolution)
        out_y = AbsInfo(value=in_y.value, min=in_y.min, max=max_x, fuzz=in_y.fuzz, flat=in_y.flat, resolution=in_y.resolution)
    else:
        out_x = swapped_absinfo(in_x)
        out_y = swapped_absinfo(in_y)

    # Update caps for ABS_X/ABS_Y and MT position axes
    abs_entries[ABS_X] = out_x
    abs_entries[ABS_Y] = out_y
    if MT_X in abs_entries and MT_Y in abs_entries:
        # MT axes should follow same max swap
        if args.rot in (90, 270):
            abs_entries[MT_X] = AbsInfo(value=abs_entries[MT_X].value, min=abs_entries[MT_X].min, max=max_y,
                                        fuzz=abs_entries[MT_X].fuzz, flat=abs_entries[MT_X].flat, resolution=abs_entries[MT_X].resolution)
            abs_entries[MT_Y] = AbsInfo(value=abs_entries[MT_Y].value, min=abs_entries[MT_Y].min, max=max_x,
                                        fuzz=abs_entries[MT_Y].fuzz, flat=abs_entries[MT_Y].flat, resolution=abs_entries[MT_Y].resolution)
        else:
            abs_entries[MT_X] = swapped_absinfo(abs_entries[MT_X])
            abs_entries[MT_Y] = swapped_absinfo(abs_entries[MT_Y])

    # Rebuild caps EV_ABS list
    caps[ecodes.EV_ABS] = list(abs_entries.items())

    ui = UInput(
        events=caps,
        name=f"SYNA Touchpad Rotated {args.rot}",
        input_props=[ecodes.INPUT_PROP_POINTER, ecodes.INPUT_PROP_BUTTONPAD],
    )

    # Track last-known values
    last_abs = {ABS_X: 0, ABS_Y: 0}
    # Per slot last-known values for MT positions
    last_mt = {}  # slot -> {MT_X: v, MT_Y: v}
    current_slot = 0

    frame = []

    for ev in src.read_loop():
        frame.append(ev)

        if ev.type == ecodes.EV_ABS and ev.code == MT_SLOT:
            current_slot = ev.value
            if current_slot not in last_mt:
                last_mt[current_slot] = {MT_X: 0, MT_Y: 0}

        if ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_REPORT:
            # Process and emit transformed frame
            # We rewrite coordinate events but pass everything else unchanged
            for e in frame:
                if e.type == ecodes.EV_ABS:
                    if e.code == ABS_X:
                        last_abs[ABS_X] = e.value
                        x, y = last_abs[ABS_X], last_abs[ABS_Y]
                        rx, ry = rotate_xy(x, y, max_x, max_y, args.rot)
                        ui.write(ecodes.EV_ABS, ABS_X, rx)
                    elif e.code == ABS_Y:
                        last_abs[ABS_Y] = e.value
                        x, y = last_abs[ABS_X], last_abs[ABS_Y]
                        rx, ry = rotate_xy(x, y, max_x, max_y, args.rot)
                        ui.write(ecodes.EV_ABS, ABS_Y, ry)
                    elif e.code == MT_X:
                        last_mt.setdefault(current_slot, {MT_X: 0, MT_Y: 0})
                        last_mt[current_slot][MT_X] = e.value
                        x, y = last_mt[current_slot][MT_X], last_mt[current_slot][MT_Y]
                        rx, ry = rotate_xy(x, y, max_x, max_y, args.rot)
                        ui.write(ecodes.EV_ABS, MT_X, rx)
                    elif e.code == MT_Y:
                        last_mt.setdefault(current_slot, {MT_X: 0, MT_Y: 0})
                        last_mt[current_slot][MT_Y] = e.value
                        x, y = last_mt[current_slot][MT_X], last_mt[current_slot][MT_Y]
                        rx, ry = rotate_xy(x, y, max_x, max_y, args.rot)
                        ui.write(ecodes.EV_ABS, MT_Y, ry)
                    else:
                        # Forward other ABS events unchanged (slot, tracking id, tool type, etc.)
                        ui.write(e.type, e.code, e.value)
                elif e.type == ecodes.EV_SYN and e.code == ecodes.SYN_REPORT:
                    # We'll issue a single syn after the frame
                    pass
                else:
                    ui.write(e.type, e.code, e.value)

            ui.syn()  # end-of-frame marker
            frame.clear()

if __name__ == "__main__":
    main()
