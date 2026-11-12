#!/usr/bin/env python3
"""Apply a persistent sample-exact delay to raw interleaved PCM on stdin."""
import argparse, os, sys

def write_all(fd,data):
    view=memoryview(data)
    while view:
        n=os.write(fd,view)
        if n<=0:raise BrokenPipeError
        view=view[n:]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--milliseconds',type=float,required=True)
    p.add_argument('--rate',type=int,default=96000)
    p.add_argument('--channels',type=int,default=2)
    p.add_argument('--bytes-per-sample',type=int,default=4)
    a=p.parse_args()
    if not 0<a.milliseconds<=500:raise SystemExit('delay must be between 0 and 500 ms')
    frame_bytes=a.channels*a.bytes_per_sample
    delay_frames=round(a.milliseconds*a.rate/1000)
    pending=bytearray(delay_frames*frame_bytes)
    while True:
        block=os.read(sys.stdin.fileno(),262144)
        if not block:break
        pending.extend(block)
        emit=len(block)-(len(block)%frame_bytes)
        if emit:
            write_all(sys.stdout.fileno(),pending[:emit])
            del pending[:emit]
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except BrokenPipeError:raise SystemExit(0)
