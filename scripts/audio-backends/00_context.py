#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote
import base64, fcntl, hashlib, json, math, os, random, re, select, shutil, signal, socket, struct, subprocess, threading, time, uuid
from collections import OrderedDict
from functools import lru_cache, wraps
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

HOME=Path.home(); PROFILES=HOME/'Documents/prefs/audio/filters'; MUSIC=HOME/'Downloads/Music'
SCRIPTS=HOME/'.config/sway/scripts'; UPDATERS=SCRIPTS/'updaters'
ADD_AUDIO_DEVICE=UPDATERS/'add-audio-device.py'
DEVICE_LOCK=SCRIPTS/'device-lock.py'
STATE=HOME/'.local/state/sway/camilladsp-webremote'; SWITCH_STATE=HOME/'.local/state/sway/media-control'; PORT=8766
CAMILLA=Path('/run/current-system/sw/bin/camilladsp'); SONOBUS=Path('/run/current-system/sw/bin/sonobus')
SONOSET=HOME/'.config/sonobus/SonoBus.settings'; EXTS={'.m4a','.aac','.mp3','.flac','.wav','.ogg','.opus'}
CAMPID=STATE/'camilladsp.pid'; ACTIVE=STATE/'active-profile'; SERVERPID=STATE/'web-server.pid'
NO_FILTER='__no_filter__'; BYPASSPID=STATE/'no-filter-bridge.pid'
MPVPID=STATE/'mpv.pid'; MPVSOCK=STATE/'mpv.sock'; MPVLOG=STATE/'mpv.log'; MODE=STATE/'mode'
MASTER_VOLUME=SWITCH_STATE/'master-volume'; CAM_WS_PORT=8767
QUEUE_FILE=STATE/'mpv-queue.json'; QUEUE_SOURCE=STATE/'mpv-queue-source.json'; RESTORE_LIST=STATE/'mpv-restore.m3u'; PLAYLIST_COVERS=STATE/'playlist-last-played.json'
QUEUE_SAVE_LOCK=threading.RLock(); LAST_QUEUE_WRITE=0.0; LAST_QUEUE_DATA=None; LAST_QUEUE_RAW=None
LOCK=threading.RLock(); MEDIA_TRANSACTION=threading.local(); PLAYERLOCK=threading.RLock(); MPRISLOCK=threading.RLock(); LAST_MPRIS=None
SYSTEM_AUDIO_SERVICE='camilladsp-system-audio.service'
STOPPED=STATE/'audio-stopped'
STOP_ACK=STATE/'audio-stop-complete'
STOP_CAP=STATE/'audio-stop-capable.pid'
CACHELOCK=threading.RLock()
CACHE={'profiles':(0.0,[]),'playlists':(0.0,[]),'songs':(0.0,[])}
TAGLOCK=threading.RLock(); TAGCACHE=OrderedDict(); TAGCACHE_LIMIT=8192
COVERLOCK=threading.RLock(); COVERCACHE=OrderedDict(); COVERCACHE_LIMIT=256
CAMILLA_PROCESS=None
SONOBUS_PROCESS=None
MASTER_WRITE_UNTIL=0.0
MASTER_RESTORING=True

