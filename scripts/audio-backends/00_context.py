#!/usr/bin/env python3
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote
import ast, base64, fcntl, hashlib, json, math, os, random, re, select, shutil, signal, socket, struct, subprocess, threading, time, uuid, yaml
from collections import OrderedDict
from functools import lru_cache, wraps
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

HOME=Path.home(); PROFILES=HOME/'Documents/prefs/audio/filters'
SCRIPTS=HOME/'.config/sway/scripts'; UPDATERS=SCRIPTS/'updaters'
ADD_AUDIO_DEVICE=UPDATERS/'add-audio-device.py'
DEVICE_LOCK=SCRIPTS/'device-lock.py'
STATE=HOME/'.local/state/sway/audio-runtime'; SWITCH_STATE=HOME/'.local/state/sway/media-control'
CAMILLA=Path('/run/current-system/sw/bin/camilladsp'); SONOBUS=Path('/run/current-system/sw/bin/sonobus')
SONOSET=HOME/'.config/sonobus/SonoBus.settings'
CAMPID=STATE/'camilladsp.pid'; ACTIVE=STATE/'active-profile'
NO_FILTER='__no_filter__'; BOTH_FILTER='00-filterless.yml'; BYPASSPID=STATE/'no-filter-bridge.pid'
MODE=STATE/'mode'
MASTER_VOLUME=SWITCH_STATE/'master-volume'; SAFE_VOLUME_INDEX=PROFILES/'safe-volume.json'; CAM_WS_PORT=8767
LOCK=threading.RLock(); MEDIA_TRANSACTION=threading.local()
SYSTEM_AUDIO_SERVICE='camilladsp-system-audio.service'
STOPPED=STATE/'audio-stopped'
STOP_ACK=STATE/'audio-stop-complete'
CACHELOCK=threading.RLock()
CACHE={'profiles':(0.0,[])}
CAMILLA_PROCESS=None
SONOBUS_PROCESS=None
MASTER_WRITE_UNTIL=0.0
MASTER_RESTORING=True

