#!/usr/bin/env python3
"""Split one stereo S32_LE stream into two physically separate mono ALSA PCMs."""
import argparse, array, os, signal, subprocess, sys

RUN=True

def stop(*_):
    global RUN
    RUN=False

def write_all(pipe,data):
    view=memoryview(data)
    while view and RUN:
        n=pipe.write(view)
        if not n:raise BrokenPipeError
        pipe.flush();view=view[n:]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--external-channel',choices=('left','right'),required=True)
    a=ap.parse_args()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
    arecord=['arecord','-q','-D','camilladsp_output_shared','-r','96000','-f','S32_LE','-c','2','-t','raw']
    external=['aplay','-q','-D','hw:Loopback,0,2','-r','96000','-f','S32_LE','-c','1','-t','raw']
    local=['aplay','-q','-D','hw:Loopback,0,3','-r','96000','-f','S32_LE','-c','1','-t','raw']
    children=[]
    try:
        ext=subprocess.Popen(external,stdin=subprocess.PIPE);children.append(ext)
        pc=subprocess.Popen(local,stdin=subprocess.PIPE);children.append(pc)
        src=subprocess.Popen(arecord,stdout=subprocess.PIPE);children.append(src)
        remainder=b'';ext_offset=0 if a.external_channel=='left' else 4;pc_offset=4-ext_offset
        while RUN:
            block=src.stdout.read(262144)
            if not block:break
            block=remainder+block;usable=len(block)-(len(block)%8);remainder=block[usable:]
            if not usable:continue
            samples=array.array('i');samples.frombytes(block[:usable])
            ext_index=ext_offset//4;pc_index=pc_offset//4
            ext_data=array.array('i',samples[ext_index::2]).tobytes()
            pc_data=array.array('i',samples[pc_index::2]).tobytes()
            write_all(ext.stdin,ext_data);write_all(pc.stdin,pc_data)
        return 0 if not RUN else 1
    except BrokenPipeError:return 1
    finally:
        for child in reversed(children):
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:child.kill()
if __name__=='__main__':raise SystemExit(main())
