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
    ap.add_argument('--offset-ms',type=float,default=0.0)
    a=ap.parse_args()
    if not -500.0<=a.offset_ms<=500.0:raise SystemExit('offset must be -500..500 ms')
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
        delay_samples=round(abs(a.offset_ms)*96.0)
        ext_pending=array.array('i',[0])*delay_samples if a.offset_ms<0 else array.array('i')
        pc_pending=array.array('i',[0])*delay_samples if a.offset_ms>0 else array.array('i')
        while RUN:
            block=src.stdout.read(262144)
            if not block:break
            block=remainder+block;usable=len(block)-(len(block)%8);remainder=block[usable:]
            if not usable:continue
            samples=array.array('i');samples.frombytes(block[:usable])
            ext_index=ext_offset//4;pc_index=pc_offset//4
            ext_samples=array.array('i',samples[ext_index::2])
            pc_samples=array.array('i',samples[pc_index::2])
            if ext_pending:
                ext_pending.extend(ext_samples);count=len(ext_samples)
                ext_data=ext_pending[:count].tobytes();del ext_pending[:count]
            else:ext_data=ext_samples.tobytes()
            if pc_pending:
                pc_pending.extend(pc_samples);count=len(pc_samples)
                pc_data=pc_pending[:count].tobytes();del pc_pending[:count]
            else:pc_data=pc_samples.tobytes()
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
