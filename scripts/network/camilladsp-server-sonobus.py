#!/usr/bin/env python3
from pathlib import Path
import os, signal, sys, time

ME=os.getpid()
HOME=Path.home()
ROOT=HOME/'.config/sway/scripts/audio-backends'
SCRIPT=(HOME/'.config/sway/scripts/network/camilladsp-server-sonobus.py').resolve()
SERVER_NAMES={'camilladsp-server-sonobus.py','camilladsp-server.py'}

def process_args(pid):
    try:
        entry=Path(f'/proc/{pid}')
        if entry.stat().st_uid!=os.getuid():return []
        return [os.fsdecode(part) for part in (entry/'cmdline').read_bytes().split(b'\0') if part]
    except (OSError,ValueError):return []

def is_previous_server(pid):
    if pid==ME:return False
    args=process_args(pid)
    if not args:return False
    for argument in args[1:]:
        try:path=Path(argument).expanduser().resolve()
        except (OSError,ValueError):continue
        if path==SCRIPT:return True
        if path.name in SERVER_NAMES and path.parent==SCRIPT.parent:return True
    return False

def replace_previous_servers():
    targets=[]
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal() and is_previous_server(int(entry.name)):
            targets.append(int(entry.name))
    for pid in targets:
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    deadline=time.monotonic()+10
    while targets and time.monotonic()<deadline:
        targets=[pid for pid in targets if is_previous_server(pid)]
        if targets:time.sleep(.05)
    for pid in targets:
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    deadline=time.monotonic()+2
    while targets and time.monotonic()<deadline:
        targets=[pid for pid in targets if is_previous_server(pid)]
        if targets:time.sleep(.05)
    if targets:
        raise RuntimeError('Previous CamillaDSP server did not exit: '+', '.join(map(str,targets)))

# CLI calls must talk to the running server, not replace it.
if len(sys.argv)==1:
    replace_previous_servers()

namespace={'__name__':'audio_runtime','__file__':str(ROOT/'manifest.txt')}
for name in (ROOT/'manifest.txt').read_text().splitlines():
    path=ROOT/name
    exec(compile(path.read_text(),str(path),'exec'),namespace,namespace)
if len(sys.argv)>1:
    namespace['topology_cli']()
else:
    namespace['main']()
