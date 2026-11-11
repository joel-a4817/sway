#!/usr/bin/env python3
"""Apply an exact wall-clock delay to raw interleaved PCM on stdin.

Unlike ffmpeg adelay, this relay has no timestamp interpretation: it emits an
exact number of silent PCM frames before forwarding every input byte unchanged.
The ALSA playback endpoint clocks the stream in real time.
"""
import argparse, os, sys

def write_all(fd, data):
    view=memoryview(data)
    while view:
        written=os.write(fd,view)
        if written<=0:raise BrokenPipeError
        view=view[written:]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--milliseconds',type=float,required=True)
    p.add_argument('--rate',type=int,default=96000)
    p.add_argument('--channels',type=int,default=2)
    p.add_argument('--bytes-per-sample',type=int,default=4)
    a=p.parse_args()
    if not 0<=a.milliseconds<=500:raise SystemExit('delay must be between 0 and 500 ms')
    frame_bytes=a.channels*a.bytes_per_sample
    frames=round(a.milliseconds*a.rate/1000)
    silence_bytes=frames*frame_bytes
    zero=bytes(65536-(65536%frame_bytes))
    while silence_bytes:
        block=zero[:min(silence_bytes,len(zero))]
        write_all(sys.stdout.fileno(),block);silence_bytes-=len(block)
    while True:
        block=os.read(sys.stdin.fileno(),262144)
        if not block:break
        write_all(sys.stdout.fileno(),block)
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except BrokenPipeError:raise SystemExit(0)
