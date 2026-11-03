#!/usr/bin/env python3
"""Standalone local CLI for Media Control.

Loads the same ordered backend modules as the web server launcher, but never
starts or contacts the HTTP server.  All lifecycle and routing commands execute
in this process, so Media Control remains functional when the server is absent.
"""
from pathlib import Path

BACKENDS = Path(__file__).resolve().parent
MANIFEST = BACKENDS / "manifest.txt"
namespace = {
    "__name__": "audio_backend_local_cli",
    "__file__": str(Path(__file__).resolve()),
    "__package__": None,
}

for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
    name = raw.strip()
    if not name or name.startswith("#"):
        continue
    path = BACKENDS / name
    if not path.is_file():
        raise SystemExit(f"Missing audio backend module: {path}")
    exec(compile(path.read_bytes(), str(path), "exec"), namespace, namespace)

namespace["topology_cli"]()
