#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path.home()/'.config/sway/scripts/audio-backends'
namespace={'__name__':'audio_runtime','__file__':str(ROOT/'manifest.txt')}
for name in (ROOT/'manifest.txt').read_text().splitlines():
    path=ROOT/name
    exec(compile(path.read_text(),str(path),'exec'),namespace,namespace)
if len(sys.argv)>1:
    namespace['topology_cli']()
else:
    namespace['main']()
