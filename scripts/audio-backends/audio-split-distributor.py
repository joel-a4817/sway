#!/usr/bin/env python3
"""Split stereo PCM into independent mono destinations; optionally delay external only."""
import argparse, array, os, queue, signal, subprocess, threading
RUN=True

def stop(*_):
    global RUN;RUN=False

def writer(pipe,q,failed):
    try:
        while RUN:
            data=q.get()
            if data is None:return
            view=memoryview(data)
            while view and RUN:
                n=pipe.write(view)
                if not n:raise BrokenPipeError
                pipe.flush();view=view[n:]
    except BaseException as exc:
        failed.append(exc);stop()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--external-channel',choices=('left','right'),required=True);ap.add_argument('--external-delay-ms',type=float,default=0.0);ap.add_argument('--pc-delay-ms',type=float,default=0.0);a=ap.parse_args()
    if not 0.0<=a.external_delay_ms<=500.0:raise SystemExit('external delay must be 0..500 ms')
    if not 0.0<=a.pc_delay_ms<=500.0:raise SystemExit('PC delay must be 0..500 ms')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
    external=['aplay','-q','-D','hw:Loopback,0,2','-r','96000','-f','S32_LE','-c','1','-t','raw']
    local=['aplay','-q','-D','hw:Loopback,0,3','-r','96000','-f','S32_LE','-c','1','-t','raw']
    source=['arecord','-q','-D','camilladsp_output_shared','-r','96000','-f','S32_LE','-c','2','-t','raw']
    children=[];threads=[];failed=[];eq=queue.Queue(maxsize=16);pq=queue.Queue(maxsize=16)
    try:
        ext=subprocess.Popen(external,stdin=subprocess.PIPE);children.append(ext)
        pc=subprocess.Popen(local,stdin=subprocess.PIPE);children.append(pc)
        src=subprocess.Popen(source,stdout=subprocess.PIPE);children.append(src)
        threads=[threading.Thread(target=writer,args=(ext.stdin,eq,failed),daemon=True),threading.Thread(target=writer,args=(pc.stdin,pq,failed),daemon=True)]
        for t in threads:t.start()
        remainder=b'';external_index=0 if a.external_channel=='left' else 1;pc_index=1-external_index
        external_pending=array.array('i',[0])*round(a.external_delay_ms*96.0)
        pc_pending=array.array('i',[0])*round(a.pc_delay_ms*96.0)
        while RUN and not failed:
            block=src.stdout.read(262144)
            if not block:break
            block=remainder+block;usable=len(block)-(len(block)%8);remainder=block[usable:]
            if not usable:continue
            samples=array.array('i');samples.frombytes(block[:usable])
            external_samples=array.array('i',samples[external_index::2]);pc_samples=array.array('i',samples[pc_index::2])
            if external_pending:
                external_pending.extend(external_samples);count=len(external_samples);external_data=external_pending[:count].tobytes();del external_pending[:count]
            else:external_data=external_samples.tobytes()
            if pc_pending:
                pc_pending.extend(pc_samples);count=len(pc_samples);pc_data=pc_pending[:count].tobytes();del pc_pending[:count]
            else:pc_data=pc_samples.tobytes()
            # Independent queues are essential: external/SonoBus backpressure can
            # never hold the PC branch behind its delay or network scheduling.
            eq.put(external_data);pq.put(pc_data)
        return 0 if not failed else 1
    finally:
        for q in (eq,pq):
            try:q.put_nowait(None)
            except queue.Full:pass
        for child in reversed(children):
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:child.kill()
if __name__=='__main__':raise SystemExit(main())
