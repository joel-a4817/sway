#!/usr/bin/env python3
"""Interactive backup restore entrypoint for the Minecraft lifecycle manager."""
import os,sys
sys.dont_write_bytecode=True
from pathlib import Path
manager=Path.home()/'.config/sway/scripts/network/minecraft-server.py'
if not manager.is_file():raise SystemExit(f'Missing manager: {manager}')
os.execv(sys.executable,[sys.executable,'-B',str(manager),'restore',*sys.argv[1:]])
