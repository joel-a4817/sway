#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote
import base64, fcntl, hashlib, json, math, os, random, re, shutil, signal, socket, struct, subprocess, threading, time, uuid
from collections import OrderedDict
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

HOME=Path.home(); PROFILES=HOME/'Documents/prefs/audio/camilladsp'; MUSIC=HOME/'Downloads/Music'
STATE=HOME/'.local/state/sway/camilladsp-webremote'; SWITCH_STATE=HOME/'.local/state/sway/audio-switch'; PORT=8766
CAMILLA=Path('/run/current-system/sw/bin/camilladsp'); SONOBUS=Path('/run/current-system/sw/bin/sonobus')
SONOSET=HOME/'.config/sonobus/SonoBus.settings'; EXTS={'.m4a','.aac','.mp3','.flac','.wav','.ogg','.opus'}
CAMPID=STATE/'camilladsp.pid'; ACTIVE=STATE/'active-profile'; SERVERPID=STATE/'web-server.pid'
MPVPID=STATE/'mpv.pid'; MPVSOCK=STATE/'mpv.sock'; MPVLOG=STATE/'mpv.log'; MODE=STATE/'mode'
MASTER_VOLUME=SWITCH_STATE/'master-volume'; CAM_WS_PORT=8767
QUEUE_FILE=STATE/'mpv-queue.json'; QUEUE_SOURCE=STATE/'mpv-queue-source.json'; RESTORE_LIST=STATE/'mpv-restore.m3u'; PLAYLIST_COVERS=STATE/'playlist-last-played.json'
QUEUE_SAVE_LOCK=threading.RLock(); LAST_QUEUE_WRITE=0.0; LAST_QUEUE_DATA=None; LAST_QUEUE_RAW=None
LOCK=threading.RLock(); PLAYERLOCK=threading.RLock(); MPRISLOCK=threading.RLock(); LAST_MPRIS=None
SYSTEM_AUDIO_SERVICE='camilladsp-system-audio.service'
STOPPED=STATE/'audio-stopped'
STOP_ACK=STATE/'audio-stop-complete'
STOP_CAP=STATE/'audio-stop-capable.pid'
CACHELOCK=threading.RLock()
CACHE={'profiles':(0.0,[]),'playlists':(0.0,[]),'songs':(0.0,[])}
TAGLOCK=threading.RLock(); TAGCACHE=OrderedDict(); TAGCACHE_LIMIT=8192
COVERLOCK=threading.RLock(); COVERCACHE=OrderedDict(); COVERCACHE_LIMIT=256
CAMILLA_PROCESS=None
LOCAL_ENGINE_OWNED=False
SONOBUS_PROCESS=None
MASTER_WRITE_UNTIL=0.0

def cached(name, ttl, loader):
    now=time.monotonic()
    with CACHELOCK:
        stamp,value=CACHE.get(name,(0.0,None))
        if value is not None and now-stamp < ttl:return value
    value=loader()
    with CACHELOCK:CACHE[name]=(now,value)
    return value

def invalidate_cache(*names):
    with CACHELOCK:
        for name in names:CACHE.pop(name,None)

@lru_cache(maxsize=32)
def exe(*names):
    for n in names:
        for p in (Path('/run/current-system/sw/bin')/n,HOME/'.nix-profile/bin'/n,Path(shutil.which(n) or '/nonexistent')):
            if p.is_file() and os.access(p,os.X_OK): return str(p)
    raise FileNotFoundError('Executable not found: '+', '.join(names))
def run(a,check=True,timeout=30,env=None): return subprocess.run(a,text=True,capture_output=True,check=check,timeout=timeout,env=env)
def ensure():
    STATE.mkdir(parents=True,exist_ok=True)
    SWITCH_STATE.mkdir(parents=True,exist_ok=True)
    MUSIC.mkdir(parents=True,exist_ok=True)
    legacy=HOME/'.local/state/sway/audio'
    old_server=legacy/'camilladsp-webremote'
    # One-time migration. Never resurrect a deliberately cleared stop marker.
    marker=STATE/'state-migrated'
    if marker.exists():return
    for source,destination in ((old_server,STATE),(legacy,SWITCH_STATE)):
        if not source.is_dir():continue
        for item in source.iterdir():
            if not item.is_file() or item.is_symlink():continue
            if source==legacy and item.name not in ('master-volume','audio-switch.log','audio-switch.lock','audio-cards-last.txt','camilladsp-local.log'):continue
            target=destination/item.name
            if not target.exists() and item.name!='mpv.sock':
                try:shutil.move(str(item),str(target))
                except OSError:pass
    for name in ('audio-stopped','audio-stop-complete','audio-stop-capable.pid'):
        old=legacy/name;new=STATE/name
        if old.is_file() and not new.exists():
            try:shutil.move(str(old),str(new))
            except OSError:pass
    marker.touch()
    for directory in (old_server,legacy):
        try:directory.rmdir()
        except OSError:pass

def rpid(p):
    try:return int(p.read_text().strip())
    except (OSError,ValueError):return None
def alive(pid,name=None):
    if not pid:return False
    try:
        os.kill(pid,0)
        return not name or name.lower() in Path(f'/proc/{pid}/comm').read_text().strip().lower()
    except (OSError,PermissionError):return False
def killpidfile(path,name=None):
    pid=rpid(path)
    if alive(pid,name):
        try:os.killpg(pid,signal.SIGTERM)
        except OSError:
            try:os.kill(pid,signal.SIGTERM)
            except OSError:pass
        deadline=time.monotonic()+3
        while alive(pid) and time.monotonic()<deadline:time.sleep(.05)
        if alive(pid):
            try:os.killpg(pid,signal.SIGKILL)
            except OSError:pass
    path.unlink(missing_ok=True)
def media_env():
    env=os.environ.copy(); runtime=f'/run/user/{os.getuid()}'
    env.setdefault('XDG_RUNTIME_DIR',runtime); env.setdefault('DBUS_SESSION_BUS_ADDRESS',f'unix:path={runtime}/bus')
    return env
def user_service(action,name):
    r=run(['systemctl','--user',action,name],False,30)
    if r.returncode:raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f'Could not {action} {name}')
def airplay(start):
    action='restart' if start else 'stop'; r=run(['systemctl',action,'nqptp.service','shairport-sync.service'],False)
    if r.returncode:raise RuntimeError(r.stderr.strip() or 'Could not change AirPlay services')
def apply_source_services(source):
    if source=='airplay':
        if run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode==0:
            user_service('stop',SYSTEM_AUDIO_SERVICE)
        if MODES[audio_mode()][1]!='airplay' and alive(rpid(MPVPID),'mpv'):stop_mpv()
        for service in ('nqptp.service','shairport-sync.service'):
            if run(['systemctl','is-active','--quiet',service],False,5).returncode:
                r=run(['systemctl','start',service],False,30)
                if r.returncode:raise RuntimeError(r.stderr.strip() or f'Could not start {service}')
    elif source=='system':
        if any(run(['systemctl','is-active','--quiet',service],False,5).returncode==0
               for service in ('nqptp.service','shairport-sync.service')):airplay(False)
        if run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode:
            user_service('start',SYSTEM_AUDIO_SERVICE)
    else:raise ValueError('Invalid audio source')
def restart_vnc():user_service('restart','camilladsp-wayvnc.service');return {'restarted':True}


def profiles():
    def load():
        return sorted(
            [p for pattern in ('*.yml','*.yaml') for p in PROFILES.glob(pattern) if p.is_file()],
            key=lambda item:item.name.lower(),
        )
    return cached('profiles',5.0,load)
BRIR_DIR=HOME/'Documents/prefs/audio/BRIRs'
def _filter_key(value):
    return re.sub(r'[^a-z0-9]+','',value.casefold())
def listening_filters():
    """Resolve display-only labels from BRIR directories; retain YAML IDs."""
    folders={}
    if BRIR_DIR.is_dir():
        for folder in BRIR_DIR.iterdir():
            if not folder.is_dir():continue
            match=re.match(r'^\((\d+)ms\)\s*(.*)$',folder.name,re.I)
            if match:folders.setdefault(match.group(1),[]).append((folder.name,_filter_key(match.group(2))))
    result={}
    for item in profiles():
        stem=item.stem
        if stem.casefold()=='00-filterless':
            result[item.name]={'group':'Other','label':'Filterless'}
            continue
        match=re.match(r'^\d+-(.+?)-(\d+)ms-(.+)$',stem,re.I)
        if not match:
            result[item.name]={'group':'Other','label':stem}
            continue
        device,delay,slug=match.groups()
        # The device label comes from the filename prefix, not a fixed device list.
        group=device.replace('-',' ').strip().title()
        candidates=folders.get(delay,[])
        suffix=_filter_key(slug)
        matches=[name for name,key in candidates if key==suffix]
        if not matches and delay=='0000':
            matches=[name for name,key in candidates if key.endswith(suffix)]
        label=matches[0] if len(matches)==1 else stem
        result[item.name]={'group':group,'label':label}
    return result
def profile(name):
    if not isinstance(name,str) or Path(name).name!=name:raise ValueError('Invalid profile')
    p=(PROFILES/name).resolve()
    if p.parent!=PROFILES.resolve() or p.suffix.lower() not in {'.yml','.yaml'} or not p.is_file():raise FileNotFoundError(name)
    return p
def active():
    if STOPPED.exists() and not alive(rpid(CAMPID),'camilladsp'):return ''
    try:return ACTIVE.read_text().strip()
    except OSError:return ''
def stop_camilla(include_stale=False):
    global CAMILLA_PROCESS
    process=CAMILLA_PROCESS
    tracked=rpid(CAMPID)
    if process is not None and process.poll() is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except OSError:
            try:process.terminate()
            except OSError:pass
        try:process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except OSError:
                try:process.kill()
                except OSError:pass
            try:process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:pass
    CAMILLA_PROCESS=None
    if include_stale and alive(tracked,'camilladsp'):
        try:os.killpg(tracked,signal.SIGTERM)
        except OSError:
            try:os.kill(tracked,signal.SIGTERM)
            except OSError:pass
        deadline=time.monotonic()+1.0
        while alive(tracked,'camilladsp') and time.monotonic()<deadline:time.sleep(.02)
        if alive(tracked,'camilladsp'):
            try:os.killpg(tracked,signal.SIGKILL)
            except OSError:pass
    if include_stale and alive(tracked,'camilladsp'):
        raise RuntimeError('Tracked CamillaDSP process did not stop; refusing another start')
    CAMPID.unlink(missing_ok=True)
def validate_camilla_profile(p):
    result=run([str(CAMILLA),'--check',str(p)],False,30)
    if result.returncode:
        detail=(result.stderr or result.stdout).strip()
        raise RuntimeError(
            'Invalid CamillaDSP profile '+p.name+
            ((': '+detail) if detail else '')
        )

def log_tail(path,limit=8192):
    try:
        with path.open('rb') as handle:
            handle.seek(0,os.SEEK_END)
            handle.seek(max(0,handle.tell()-limit))
            return handle.read().decode('utf-8',errors='replace')
    except OSError:return ''

def other_camilla_processes():
    result=run(['pgrep','-x','camilladsp'],False,5)
    found=[]
    for word in result.stdout.split():
        if not word.isdecimal():continue
        pid=int(word)
        try:
            if Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1][:1]!='Z':found.append(pid)
        except OSError:pass
    return found

def reusable_camilla(pid):
    """Recognize this engine by executable and websocket, not its launch profile."""
    try:
        args=[part.decode('utf-8') for part in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if part]
        if not args or Path(args[0]).resolve()!=CAMILLA.resolve():return False
        if f'--port={CAM_WS_PORT}' not in args:return False
        with socket.create_connection(('127.0.0.1',CAM_WS_PORT),timeout=.5):pass
        current=camilla_command('GetConfigFilePath')
        if not isinstance(current,str):return False
        selected=Path(current).resolve()
        if selected.parent!=PROFILES.resolve() or not selected.is_file():return False
        return selected
    except (OSError,ValueError,RuntimeError,socket.timeout):return False

def start_camilla(p):
    global CAMILLA_PROCESS
    target=profile(p.name if isinstance(p,Path) else str(p))
    validate_camilla_profile(target)
    competing=other_camilla_processes()
    if competing:
        raise RuntimeError('CamillaDSP already running (PID '+', '.join(map(str,competing))+'); refusing a second instance. Check the local switch or another service.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',CAM_WS_PORT))==0:
            raise RuntimeError('CamillaDSP websocket port 8767 is already occupied; refusing to start another instance')
    logpath=STATE/'camilladsp.log'
    marker='\n===== start '+time.strftime('%Y-%m-%d %H:%M:%S')+' profile='+target.name+' =====\n'
    with logpath.open('ab',buffering=0) as log:
        log.write(marker.encode())
        process=subprocess.Popen(
            [str(CAMILLA),f'--port={CAM_WS_PORT}','--gain=0.0',str(target)],stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
        )
    CAMILLA_PROCESS=process;CAMPID.write_text(str(process.pid)+'\n')
    # ALSA open/start failures occur during initial backend setup. A short,
    # active readiness window catches them without adding seconds to every switch.
    deadline=time.monotonic()+2.0
    while time.monotonic()<deadline:
        code=process.poll()
        if code is not None:
            CAMILLA_PROCESS=None;CAMPID.unlink(missing_ok=True)
            content=log_tail(logpath)
            tail=content[content.rfind('===== start '):][-4000:].strip()
            raise RuntimeError(
                f'CamillaDSP exited with status {code} while starting {target.name}'+
                (f': {tail}' if tail else '')
            )
        time.sleep(.05)
    ACTIVE.write_text(target.name+'\n')
    return process.pid
def release_local_engine():
    global LOCAL_ENGINE_OWNED
    local_running=alive(rpid(CAMPID),'camilladsp') and alive(rpid(LOCALMONPID))
    if not LOCAL_ENGINE_OWNED and not local_running:return
    others=other_camilla_processes()
    tracked=rpid(CAMPID)
    if any(pid!=tracked for pid in others):
        raise RuntimeError('Untracked CamillaDSP is running (PID '+', '.join(map(str,others))+'); refusing to replace it')
    stop_local_monitor()
    stop_camilla(include_stale=True)
    LOCAL_ENGINE_OWNED=False

def switch_profile(name):
    with LOCK:
        target=profile(name)
        validate_camilla_profile(target)
        was_stopped=STOPPED.exists()
        try:previous=ACTIVE.read_text().strip()
        except OSError:previous=''
        _,source,local_wanted,_=MODES[audio_mode()]
        pid=rpid(CAMPID)
        # Keep the receiver, SonoBus, local monitor and engine alive for filter changes.
        if not was_stopped and alive(pid,'camilladsp'):
            old_path=camilla_command('GetConfigFilePath')
            if not isinstance(old_path,str) or not old_path:
                raise RuntimeError('CamillaDSP did not report its active config path')
            if previous==target.name and Path(old_path).resolve()==target.resolve():
                return {'profile':target.name,'pid':pid,'mode':mode_state()}
            camilla_command({'SetConfigFilePath':str(target)})
            try:
                camilla_command('Reload')
                if camilla_command('GetConfigFilePath')!=str(target):
                    raise RuntimeError('CamillaDSP did not confirm the selected config')
                if not alive(pid,'camilladsp'):
                    raise RuntimeError('CamillaDSP exited during config reload')
            except Exception:
                if alive(pid,'camilladsp'):
                    try:
                        camilla_command({'SetConfigFilePath':old_path})
                        camilla_command('Reload')
                    except Exception:pass
                raise
            ACTIVE.write_text(target.name+'\n')
            invalidate_cache('profiles')
            return {'profile':target.name,'pid':pid,'mode':mode_state()}
        # A stopped or crashed engine still needs the original recovery path.
        release_local_engine()
        if was_stopped and alive(rpid(CAMPID),'camilladsp'):
            stop_local_monitor();stop_camilla(include_stale=True)
        if local_wanted:stop_local_monitor()
        if was_stopped:
            user_service('start','pipewire.socket')
            user_service('start','pipewire-pulse.socket')
            user_service('start','wireplumber.service')
        # Only source transitions/recovery configure services; never restart AirPlay
        # merely to select another filter.
        if was_stopped or source!='airplay':apply_source_services(source)
        stop_camilla()
        alsa100()
        try:
            pid=start_camilla(target)
            if was_stopped:
                apply_mode(audio_mode(),restore_camilla=False,normalize=False)
                STOPPED.unlink(missing_ok=True)
            elif local_wanted:start_local_monitor()
        except Exception:
            if local_wanted:stop_local_monitor()
            stop_camilla()
            if previous and previous!=target.name:
                try:
                    start_camilla(profile(previous))
                    if local_wanted:start_local_monitor()
                except Exception:pass
            raise
        normalize_audio_volumes()
        invalidate_cache('profiles')
        return {'profile':target.name,'pid':pid,'mode':mode_state()}
def restart_camilla():
    name=active()
    if not name:raise RuntimeError('No active profile')
    return switch_profile(name)
def alsa100():
    r=run(['amixer','-c','Loopback','sset','PCM','0dB'],False)
    if r.returncode:raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'Could not set Loopback PCM')

# This file is shared with audio-switch.sh. It is the only saved master.
def master_volume():
    # The Sway volume keys and both web sliders share the DEFAULT PipeWire sink.
    # Read the live value so external pactl key presses appear in both sliders.
    try:
        result=run([exe('pactl'),'get-sink-volume','@DEFAULT_SINK@'],False,5,media_env())
        match=re.search(r'(\d+(?:\.\d+)?)%',result.stdout)
        if result.returncode==0 and match:
            value=max(0.0,min(100.0,float(match.group(1))))
            MASTER_VOLUME.parent.mkdir(parents=True,exist_ok=True)
            with LOCK:
                try:saved=float(MASTER_VOLUME.read_text().strip().rstrip('%'))
                except (OSError,ValueError):saved=None
                if time.monotonic()<MASTER_WRITE_UNTIL:
                    return max(0.0,min(100.0,saved)) if saved is not None else value
                if saved is None or abs(saved-value)>=.5:
                    atomic(MASTER_VOLUME,f'{value:.2f}%\n')
                    sync_mpv_master(value)
            return value
    except (OSError,FileNotFoundError,subprocess.TimeoutExpired):pass
    try:return max(0.0,min(100.0,float(MASTER_VOLUME.read_text().strip().rstrip('%'))))
    except (OSError,ValueError):return 50.0


def _ws_read(sock, size):
    data=b''
    while len(data)<size:
        chunk=sock.recv(size-len(data))
        if not chunk:raise RuntimeError('CamillaDSP websocket closed unexpectedly')
        data+=chunk
    return data

def camilla_command(command):
    # Local-only RFC 6455 client: no third-party Python package is required.
    key=base64.b64encode(os.urandom(16)).decode('ascii')
    with socket.create_connection(('127.0.0.1',CAM_WS_PORT),timeout=2) as sock:
        sock.settimeout(2)
        request=(f'GET / HTTP/1.1\r\nHost: 127.0.0.1:{CAM_WS_PORT}\r\n'
                 'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                 f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        sock.sendall(request.encode('ascii'))
        header=b''
        while b'\r\n\r\n' not in header:
            if len(header)>8192:raise RuntimeError('CamillaDSP websocket handshake too large')
            header+=_ws_read(sock,1)
        expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if not header.startswith(b'HTTP/1.1 101 ') or b'sec-websocket-accept: '+expected.lower() not in header.lower():
            raise RuntimeError('CamillaDSP websocket handshake failed')
        payload=json.dumps(command,separators=(',',':')).encode('utf-8')
        mask=os.urandom(4)
        length=len(payload)
        size=(bytes([length]) if length<126 else b'\x7e'+struct.pack('!H',length)
              if length<65536 else b'\x7f'+struct.pack('!Q',length))
        sock.sendall(b'\x81'+bytes([size[0]|128])+size[1:]+mask+
                     bytes(byte^mask[i%4] for i,byte in enumerate(payload)))
        first,second=_ws_read(sock,2)
        if first & 15 != 1 or not first & 128:raise RuntimeError('Unexpected CamillaDSP websocket response')
        count=second & 127
        if count==126:count=struct.unpack('!H',_ws_read(sock,2))[0]
        elif count==127:count=struct.unpack('!Q',_ws_read(sock,8))[0]
        if count>65536:raise RuntimeError('CamillaDSP response too large')
        response_mask=_ws_read(sock,4) if second & 128 else None
        data=_ws_read(sock,count)
        if response_mask:data=bytes(byte^response_mask[i%4] for i,byte in enumerate(data))
    reply=json.loads(data.decode('utf-8'))
    name=command if isinstance(command,str) else next(iter(command))
    item=reply.get(name,{})
    if item.get('result')!='Ok':raise RuntimeError(f'CamillaDSP {name} failed: {item}')
    return item.get('value')

def sync_mpv_master(value):
    if not (alive(rpid(MPVPID),'mpv') and MPVSOCK.exists()):return
    try:mpv_direct(['set_property','volume',float(value)])
    except (OSError,RuntimeError,ValueError) as error:
        raise RuntimeError(f'Could not sync MPV to saved master: {error}') from error

def watch_master_volume(stop):
    # Sway volume keys use pactl directly, even when the web page is closed.
    while not stop.wait(.6):
        try:master_volume()
        except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired):pass

def apply_master_volume(value,normalize_sinks=True):
    # Normalize other sinks on route changes, not on each slider update.
    # CamillaDSP Main remains at unity.
    default=run([exe('pactl'),'get-default-sink'],False,5,media_env())
    if default.returncode:raise RuntimeError(default.stderr.strip() or 'No default audio sink')
    name=default.stdout.strip()
    if not name:raise RuntimeError('No default audio sink')
    if normalize_sinks:
        for sink in _pactl_json('sinks'):
            target=str(sink.get('name') or '') if isinstance(sink,dict) else ''
            if not target or target==name:continue
            result=run([exe('pactl'),'set-sink-volume',target,'100%'],False,8,media_env())
            if result.returncode:raise RuntimeError(f'Could not set {target} to 100%: {result.stderr.strip()}')
    result=run([exe('pactl'),'set-sink-volume',name,f'{value:.2f}%'],False,8,media_env())
    if result.returncode:raise RuntimeError(f'Could not set {name} to {value:.2f}%: {result.stderr.strip()}')
    sync_mpv_master(value)
    if alive(rpid(CAMPID),'camilladsp'):
        try:camilla_command({'SetVolume':0.0})
        except (OSError,ValueError) as error:raise RuntimeError(f'Could not set CamillaDSP Main to unity: {error}') from error

def normalize_audio_volumes():
    # Normalize every live ALSA/PipeWire gain stage to 0 dB, then restore the shared master.
    # The server never pauses media.
    errors=[]
    try:cards=Path('/proc/asound/cards').read_text()
    except OSError:cards=''
    for card in re.findall(r'^\s*(\d+)\s+\[',cards,re.M):
        listing=run([exe('amixer'),'-c',card,'scontrols'],False,8)
        if listing.returncode:
            errors.append(f'ALSA card {card}: {listing.stderr.strip() or "could not list controls"}')
            continue
        for name,index in re.findall(r"^Simple mixer control '([^']+)',(\d+)$",listing.stdout,re.M):
            control=f'{name},{index}'
            info=run([exe('amixer'),'-c',card,'sget',control],False,8)
            if info.returncode or not re.search(r'[-+]?\d+(?:\.\d+)?dB',info.stdout):continue
            for direction in ('playback','capture'):
                if not re.search(rf'^{direction} channels:',info.stdout,re.I|re.M):continue
                result=run([exe('amixer'),'-c',card,'sset',control,direction,'0dB'],False,8)
                if result.returncode:
                    errors.append(f'ALSA card {card} {control} {direction}: {result.stderr.strip() or "0dB unavailable"}')
    try:
        # During route changes the default sink may have just been recreated;
        # restore the persisted master, not that sink's fresh 100% default.
        try:value=float(MASTER_VOLUME.read_text().strip().rstrip('%'))
        except (OSError,ValueError):value=master_volume()
        # A route/profile switch may recreate sinks. Enumerate live objects now,
        # not at startup. PipeWire stream gains are distinct from device gains.
        for kind,command,key in (('sinks','set-sink-volume','name'),
                                 ('sources','set-source-volume','name'),
                                 ('sink-inputs','set-sink-input-volume','index'),
                                 ('source-outputs','set-source-output-volume','index')):
            for item in _pactl_json(kind):
                if not isinstance(item,dict):continue
                target=item.get(key)
                if target is None or target=='':continue
                result=run([exe('pactl'),command,str(target),'0dB'],False,8,media_env())
                if result.returncode:
                    errors.append(f'{kind} {target}: {result.stderr.strip() or "0dB unavailable"}')
        # Direct-ALSA MPV bypasses PipeWire; its gain is the same saved master.
        sync_mpv_master(max(0.0,min(100.0,value)))
        if alive(rpid(CAMPID),'camilladsp'):
            camilla_command({'SetVolume':0.0})
        default=run([exe('pactl'),'get-default-sink'],False,5,media_env())
        if default.returncode or not default.stdout.strip():
            raise RuntimeError(default.stderr.strip() or 'No default audio sink')
        result=run([exe('pactl'),'set-sink-volume',default.stdout.strip(),f'{max(0.0,min(100.0,value)):.2f}%'],False,8,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not restore saved master')
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as error:errors.append(str(error))
    if errors:
        raise RuntimeError('Audio normalization incomplete: '+'; '.join(errors[:4])+
                           (f'; {len(errors)-4} more' if len(errors)>4 else ''))
    return {'masterVolume':master_volume()}

def set_master_volume(value):
    global MASTER_WRITE_UNTIL
    try:value=float(value)
    except (TypeError,ValueError):raise ValueError('Invalid master volume')
    if not math.isfinite(value) or not 0<=value<=100:raise ValueError('Master volume must be between 0 and 100')
    with LOCK:
        MASTER_WRITE_UNTIL=time.monotonic()+2.0
        apply_master_volume(value,normalize_sinks=False)
        MASTER_VOLUME.parent.mkdir(parents=True,exist_ok=True)
        atomic(MASTER_VOLUME,f'{value:.2f}%\n')
    return {'volume':value}
def sonopids():
    out=set()
    r=run(['pgrep','-x','(sonobus|SonoBus)'],False)
    for x in r.stdout.split():
        try:out.add(int(x))
        except ValueError:pass
    return out
def stop_sonobus():
    global SONOBUS_PROCESS
    targets=set(sonopids())
    if SONOBUS_PROCESS is not None and SONOBUS_PROCESS.poll() is None:
        targets.add(SONOBUS_PROCESS.pid)
    for pid in targets:
        try:os.killpg(pid,signal.SIGTERM)
        except OSError:
            try:os.kill(pid,signal.SIGTERM)
            except OSError:pass
    deadline=time.monotonic()+4.0
    while any(alive(pid) for pid in targets) and time.monotonic()<deadline:
        time.sleep(.05)
    for pid in targets:
        if alive(pid):
            try:os.killpg(pid,signal.SIGKILL)
            except OSError:
                try:os.kill(pid,signal.SIGKILL)
                except OSError:pass
    deadline=time.monotonic()+1.0
    while any(alive(pid) for pid in targets) and time.monotonic()<deadline:
        time.sleep(.05)
    SONOBUS_PROCESS=None


def saved_mpv_volume():
    return master_volume()
def mpv_direct(command):
    request_id=random.randint(1,2_147_000_000)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(MPVSOCK))
        client.sendall((json.dumps({'command':command,'request_id':request_id})+'\n').encode())
        with client.makefile('rb') as stream:
            for line in stream:
                reply=json.loads(line)
                if reply.get('request_id')!=request_id:continue
                if reply.get('error')!='success':raise RuntimeError(reply.get('error') or 'mpv command failed')
                return reply.get('data')
    raise RuntimeError('mpv returned no response')

def save_mpv_queue(force=False,snapshot=None):
    global LAST_QUEUE_WRITE,LAST_QUEUE_DATA,LAST_QUEUE_RAW
    if not (alive(rpid(MPVPID),'mpv') and MPVSOCK.exists()):return
    with QUEUE_SAVE_LOCK:
        try:
            if snapshot is None:
                entries=mpv_direct(['get_property','playlist']) or []
                index=mpv_direct(['get_property','playlist-pos'])
                position=mpv_direct(['get_property','playback-time']) or 0
                paused=mpv_direct(['get_property','pause'])
                loop_file=mpv_direct(['get_property','loop-file'])
                loop_playlist=mpv_direct(['get_property','loop-playlist'])
            else:
                entries=snapshot.get('playlist') or []
                index=snapshot.get('playlist-pos')
                position=snapshot.get('playback-time') or 0
                paused=snapshot.get('pause')
                loop_file=snapshot.get('loop-file')
                loop_playlist=snapshot.get('loop-playlist')
            # Skip filesystem validation while the MPV queue and its playback
            # controls match the last persisted snapshot. Position still saves
            # on the existing five-second interval, and forced saves bypass this.
            raw=tuple(entry.get('filename') if isinstance(entry,dict) else None for entry in entries)
            now=time.monotonic()
            if (not force and LAST_QUEUE_DATA is not None and LAST_QUEUE_RAW is not None
                    and now-LAST_QUEUE_WRITE<5 and raw==LAST_QUEUE_RAW
                    and index==LAST_QUEUE_DATA['index']
                    and bool(paused)==LAST_QUEUE_DATA['paused']
                    and loop_file==LAST_QUEUE_DATA['loopFile']
                    and loop_playlist==LAST_QUEUE_DATA['loopPlaylist']):
                return
            paths=[str(song(entry.get('filename'))) for entry in entries if isinstance(entry,dict)]
            if len(paths)!=len(entries) or not paths:return
            index=int(index) if index is not None else 0
            index=max(0,min(index,len(paths)-1))
            data={'files':paths,'index':index,'position':max(0,float(position)),
                  'paused':bool(paused),'loopFile':loop_file,'loopPlaylist':loop_playlist}
            now=time.monotonic()
            if not force and LAST_QUEUE_DATA is not None:
                if (now-LAST_QUEUE_WRITE<5 and
                        all(data[key]==LAST_QUEUE_DATA.get(key) for key in ('files','index','paused','loopFile','loopPlaylist'))):
                    return
            _write_json(QUEUE_FILE,data)
            LAST_QUEUE_DATA=data;LAST_QUEUE_WRITE=now;LAST_QUEUE_RAW=raw
            if not data['paused']:remember_playlist_track(paths[index])
        except (OSError,ValueError,TypeError,RuntimeError,KeyError,subprocess.TimeoutExpired):
            return

def restore_mpv_queue():
    saved=_read_json(QUEUE_FILE,{})
    if not isinstance(saved,dict) or not isinstance(saved.get('files'),list):return
    paths=[];selected=None
    for i,filename in enumerate(saved['files'][:10000]):
        try:path=str(song(filename))
        except (OSError,ValueError,TypeError):continue
        if i==saved.get('index'):selected=len(paths)
        paths.append(path)
    if not paths:return
    # Build the exact saved ordering, including a shuffled queue. Keep playback
    # paused on restart; do not unexpectedly resume audio after a reboot.
    mpv_direct(['set_property','pause',True])
    # One loadlist transaction preserves queue order without per-track IPC.
    if all('\n' not in path and '\r' not in path for path in paths):
        atomic(RESTORE_LIST,'#EXTM3U\n'+'\n'.join(paths)+'\n')
        mpv_direct(['loadlist',str(RESTORE_LIST),'replace'])
    else:
        mpv_direct(['loadfile',paths[0],'replace'])
        for path in paths[1:]:mpv_direct(['loadfile',path,'append'])
    mpv_direct(['set_property','playlist-pos',selected if selected is not None else 0])
    try:
        position=float(saved.get('position') or 0)
        if 0<position<1e8:mpv_direct(['seek',position,'absolute+exact'])
    except (TypeError,ValueError):pass
    for key,value in (('loop-file',saved.get('loopFile')),('loop-playlist',saved.get('loopPlaylist'))):
        if value in ('no','inf'):mpv_direct(['set_property',key,value])
    mpv_direct(['set_property','pause',True])

def stop_mpv():
    save_mpv_queue(force=True)
    killpidfile(MPVPID,'mpv');MPVSOCK.unlink(missing_ok=True)
def ensure_mpv():
    if alive(rpid(MPVPID),'mpv') and MPVSOCK.exists():return
    stop_mpv();log=MPVLOG.open('ab',buffering=0)
    cmd=[exe('mpv'),'--idle=yes','--no-video','--no-terminal','--keep-open=no','--ao=alsa','--audio-device=alsa/camilladsp_input','--audio-samplerate=96000','--audio-channels=stereo','--audio-format=s32',f'--input-ipc-server={MPVSOCK}',f'--volume={master_volume():.2f}','--volume-max=100']
    try:q=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    finally:log.close()
    MPVPID.write_text(f'{q.pid}\n');deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if MPVSOCK.exists() and q.poll() is None:
            try:restore_mpv_queue()
            except (OSError,ValueError,RuntimeError):pass
            sync_mpv_master(master_volume())
            return
        if q.poll() is not None:break
        time.sleep(.05)
    raise RuntimeError('mpv failed to start; check '+str(MPVLOG))
def mpv_properties(names):
    with PLAYERLOCK:
        for attempt in range(2):
            try:
                ensure_mpv()
                requests=[]
                pending={}
                for index,name in enumerate(names,1):
                    request_id=random.randint(1,2_147_000_000)+index
                    pending[request_id]=name
                    requests.append(json.dumps({'command':['get_property',name],'request_id':request_id}))
                values={}
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(4);client.connect(str(MPVSOCK));client.sendall(('\n'.join(requests)+'\n').encode())
                    stream=client.makefile('rb')
                    while pending:
                        line=stream.readline()
                        if not line:break
                        response=json.loads(line)
                        request_id=response.get('request_id')
                        if request_id not in pending:continue
                        name=pending.pop(request_id)
                        values[name]=response.get('data') if response.get('error')=='success' else None
                if pending:raise RuntimeError('mpv state response was incomplete')
                return values
            except (OSError,ValueError,RuntimeError):
                if attempt:raise
                stop_mpv()

def set_repeat(m):
    if m not in ('off','all','one'):raise ValueError('Invalid repeat mode')
    mpv(['set_property','loop-file','inf' if m=='one' else 'no']);mpv(['set_property','loop-playlist','inf' if m=='all' else 'no']);return m

def player_state():
    # Read-only status: do not start MPV while opening or polling the library.
    if not (alive(rpid(MPVPID),'mpv') and MPVSOCK.exists()):
        return {'available':False,'playing':False,'title':'','path':'','artist':'','album':'','currentTime':None,'duration':None,'volume':saved_mpv_volume(),'repeat':'off','canShuffleQueue':False}
    try:
        with PLAYERLOCK:
            values=mpv_properties(['path','playback-time','duration','pause','loop-file','loop-playlist','media-title','metadata/by-key/Artist','metadata/by-key/Album','metadata/by-key/Album_Artist','playlist','playlist-pos'])
            save_mpv_queue(snapshot=values)
    except Exception:
        return {'available':False,'playing':False,'title':'','path':'','artist':'','album':'','currentTime':None,'duration':None,'volume':saved_mpv_volume(),'repeat':'off','canShuffleQueue':False}
    path=values.get('path') or ''
    loop_file=values.get('loop-file');loop_playlist=values.get('loop-playlist')
    repeat='one' if loop_file not in ('no',False,None,0) else ('all' if loop_playlist not in ('no',False,None,0) else 'off')
    def number(value):
        try:return float(value) if value is not None else None
        except (TypeError,ValueError):return None
    return {
        'available':bool(path),'playing':bool(path) and not bool(values.get('pause',True)),
        'title':values.get('media-title') or (Path(path).stem if path else ''),'path':path,
        'artist':values.get('metadata/by-key/Artist') or values.get('metadata/by-key/Album_Artist') or '',
        'album':values.get('metadata/by-key/Album') or '',
        'cover':cover_url(Path(path)) if path and Path(path).is_file() and MUSIC.resolve() in Path(path).resolve().parents else '',
        'playlist':playlist_for_track(path),
        'currentTime':number(values.get('playback-time')),'duration':number(values.get('duration')),
        'volume':master_volume(),
        'repeat':repeat,'canShuffleQueue':bool(queue_playlist_name(values.get('playlist'),validate_files=False)),
    }

def mpris_players():
    r=run([exe('playerctl'),'--list-all'],False,5,media_env())
    # PC Music belongs to its own controls, even if mpv is exposed over MPRIS.
    return [p for x in r.stdout.splitlines() if (p:=x.strip()) and p!='playerctld' and p.split('.',1)[0].casefold()!='mpv']
def mpris_status(p):
    r=run([exe('playerctl'),'--player',p,'status'],False,5,media_env());return r.stdout.strip() if r.returncode==0 else ''
def active_mpris(with_status=False):
    global LAST_MPRIS
    with MPRISLOCK:
        players=mpris_players()
        if not players:
            LAST_MPRIS=None
            return None
        statuses={player:mpris_status(player) for player in players}
        players=[player for player in players if statuses[player] in ('Playing','Paused','Stopped')]
        if not players:
            LAST_MPRIS=None
            return None
        playing=[player for player in players if statuses[player]=='Playing']
        if playing:
            LAST_MPRIS=LAST_MPRIS if LAST_MPRIS in playing else playing[0]
        elif LAST_MPRIS not in players:
            LAST_MPRIS=min(players,key=lambda player:{'Paused':0,'Stopped':1}.get(statuses[player],2))
        return (LAST_MPRIS,statuses[LAST_MPRIS]) if with_status else LAST_MPRIS
def pctl(p,*args,default=''):
    r=run([exe('playerctl'),'--player',p,*args],False,5,media_env());return r.stdout.strip() if r.returncode==0 else default
def system_volume():
    return master_volume()
def set_system_volume(v):
    return set_master_volume(v)

def system_art_file(raw):
    parsed=urlparse(raw)
    if parsed.scheme!='file' or parsed.netloc not in ('','localhost'):return None
    path=Path(unquote(parsed.path))
    try:
        if path.is_file() and path.stat().st_size<=10*1024*1024:return path
    except OSError:pass
    return None

def system_art_url(raw):
    if not raw or len(raw)>2048:return ''
    parsed=urlparse(raw)
    if parsed.scheme in ('https','http') and parsed.netloc:return raw
    path=system_art_file(raw)
    if path is None:return ''
    return '/api/system-cover?v='+hashlib.sha256((str(path)+str(path.stat().st_mtime_ns)).encode()).hexdigest()[:20]

def system_cover_bytes(token):
    player=active_mpris()
    if not player:return None
    raw=pctl(player,'metadata','mpris:artUrl')
    path=system_art_file(raw)
    if path is None or system_art_url(raw).split('=')[-1]!=token:return None
    try:
        result=subprocess.run([exe('ffmpeg'),'-hide_banner','-loglevel','error','-i',str(path),
            '-frames:v','1','-vf','scale=320:320:force_original_aspect_ratio=decrease',
            '-c:v','mjpeg','-q:v','5','-f','image2pipe','pipe:1'],
            capture_output=True,timeout=12,check=False)
        if result.returncode==0 and result.stdout.startswith(b'\xff\xd8\xff') and len(result.stdout)<1024*1024:
            return result.stdout
    except (OSError,subprocess.TimeoutExpired,FileNotFoundError):pass
    return None

def system_state():
    # A player can disappear between discovery and the separate MPRIS reads.
    base={'available':False,'player':'','status':'Stopped','playing':False,'title':'No system media','artist':'','position':None,'duration':0.0,'seekable':False,'trackId':'','volume':system_volume(),'cover':''}
    try:
        selected=active_mpris(with_status=True)
        if not selected:return base
        player,status=selected
        # Preserve empty fields: splitlines() would shift the artwork URL into
        # the duration field when the player has no length metadata.
        metadata=pctl(player,'metadata','--format','{{xesam:title}}\x1f{{xesam:artist}}\x1f{{mpris:length}}\x1f{{mpris:artUrl}}\x1f{{mpris:trackid}}')
        rows=(metadata.split('\x1f',4)+['','','','',''])[:5]
        title,artist,length,art,track_id=(item.strip() for item in rows)
        try:duration=float(length)/1_000_000 if length else 0.0
        except (ValueError,OverflowError):duration=0.0
        if not math.isfinite(duration) or duration<0:duration=0.0
        try:
            raw_position=pctl(player,'position')
            position=float(raw_position) if raw_position else None
        except (ValueError,OverflowError):position=None
        if position is not None and (not math.isfinite(position) or position<0):position=None
        # Do not publish a half-populated player after it vanishes mid-poll.
        status=mpris_status(player)
        if status not in ('Playing','Paused','Stopped'):return base
        base.update(available=True,player=player,status=status,playing=status=='Playing',title=title or player,artist=artist,position=position,duration=duration,seekable=duration>0,trackId=track_id,cover=system_art_url(art))
    except (OSError,ValueError,TypeError,RuntimeError,AttributeError,subprocess.TimeoutExpired):
        return base
    return base
def system_media(command):
    cmd={'previous':'previous','toggle':'play-pause','next':'next'}.get(command)
    if not cmd:raise ValueError('Invalid media command')
    p=active_mpris()
    if not p:raise RuntimeError('No controllable system media player')
    r=run([exe('playerctl'),'--player',p,cmd],False,10,media_env())
    if r.returncode:raise RuntimeError(r.stderr.strip() or f'{p} does not support {cmd}')
    return {'player':p,'command':command}
def system_seek(v):
    global LAST_MPRIS
    player=active_mpris()
    if not player:raise RuntimeError('No controllable media player')
    value=max(0,float(v))
    result=run([exe('playerctl'),'--player',player,'position',f'{value:.3f}'],False,10,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Player does not support seeking')
    with MPRISLOCK:LAST_MPRIS=player
    return {'player':player,'position':value}

def pname(n):
    if not isinstance(n,str):raise ValueError('Invalid playlist')
    n=n.strip()
    if not n or n in ('.','..') or '/' in n or '\\' in n or '\0' in n:raise ValueError('Invalid playlist')
    return n
def pdir(n):
    p=(MUSIC/pname(n)).resolve()
    if p.parent!=MUSIC.resolve():raise ValueError('Invalid playlist path')
    return p

def files(root=None):
    root=root or MUSIC
    cache_name='songs' if root==MUSIC else None
    def load():
        found={}
        if not root.exists():return []
        for item in root.rglob('*'):
            try:
                if item.is_file() and item.suffix.lower() in EXTS and not item.name.startswith('.'):
                    found[str(item.resolve())]=item
            except OSError:
                pass
        return sorted(found.values(),key=lambda item:(item.stem.casefold(),str(item).casefold()))
    return cached(cache_name,5.0,load) if cache_name else load()
def audio_tags(p):
    # Keyed by file identity, size and modification time so replacing a track
    # refreshes its tags without rescanning unchanged tracks on every browse.
    try:
        stat=p.stat()
        key=str(p); signature=(stat.st_size,stat.st_mtime_ns)
    except OSError:return {}
    with TAGLOCK:
        if key in TAGCACHE and TAGCACHE[key][0]==signature:
            TAGCACHE.move_to_end(key)
            return TAGCACHE[key][1]
    try:
        result=run([exe('ffprobe'),'-v','error',
                    '-show_entries','format_tags=title,artist,album,album_artist:stream=index,codec_type:stream_disposition=attached_pic:stream_tags=title,artist,album,album_artist',
                    '-of','json',str(p)],False,10)
        data=json.loads(result.stdout) if result.returncode==0 else {}
        tags={}
        # Container tags first; use audio-stream tags for fields absent there.
        streams=list(data.get('streams',[]) or [])
        for stream in streams:
            if stream.get('codec_type')=='video' and stream.get('disposition',{}).get('attached_pic')==1:
                tags['_cover_stream']=stream['index'];break
        sections=[data.get('format',{})]+[item for item in streams if item.get('codec_type')=='audio']
        for section in sections:
            for name,value in (section.get('tags') or {}).items():
                name=name.casefold()
                if name in ('title','artist','album','album_artist') and name not in tags and isinstance(value,str) and value.strip():
                    tags[name]=value.strip()
    except (OSError,ValueError,subprocess.TimeoutExpired,FileNotFoundError,RuntimeError,TypeError,AttributeError):
        tags={}
    with TAGLOCK:
        TAGCACHE[key]=(signature,tags)
        TAGCACHE.move_to_end(key)
        while len(TAGCACHE)>TAGCACHE_LIMIT:TAGCACHE.popitem(last=False)
    return tags

def cover_url(p,tags=None):
    tags=audio_tags(p) if tags is None else tags
    if '_cover_stream' not in tags:return ''
    try:stat=p.stat()
    except OSError:return ''
    from urllib.parse import quote
    return '/api/cover?path='+quote(str(p.resolve()),safe='')+'&v='+str(stat.st_mtime_ns)+'-'+str(stat.st_size)

def cover_bytes(p):
    tags=audio_tags(p)
    if '_cover_stream' not in tags:return None
    stat=p.stat();key=(str(p),stat.st_size,stat.st_mtime_ns)
    with COVERLOCK:
        if key in COVERCACHE:
            COVERCACHE.move_to_end(key)
            return COVERCACHE[key]
    try:
        result=subprocess.run([exe('ffmpeg'),'-hide_banner','-loglevel','error','-i',str(p),
            '-map',f"0:{tags['_cover_stream']}",'-frames:v','1',
            '-vf','scale=320:320:force_original_aspect_ratio=decrease',
            '-c:v','mjpeg','-q:v','5','-f','image2pipe','pipe:1'],
            capture_output=True,timeout=12,check=False)
        image=result.stdout if result.returncode==0 and result.stdout.startswith(b'\xff\xd8\xff') and len(result.stdout)<1024*1024 else None
    except (OSError,subprocess.TimeoutExpired,FileNotFoundError):image=None
    with COVERLOCK:
        COVERCACHE[key]=image
        COVERCACHE.move_to_end(key)
        while len(COVERCACHE)>COVERCACHE_LIMIT:COVERCACHE.popitem(last=False)
    return image

def sobj(p):
    try:r=str(p.relative_to(MUSIC))
    except ValueError:r=str(p)
    tags=audio_tags(p)
    return {'id':str(p.resolve()),'title':tags.get('title') or p.stem,
            'artist':tags.get('artist') or tags.get('album_artist') or '',
            'album':tags.get('album') or '', 'cover':cover_url(p,tags),
            'path':str(p.resolve()),'relative':r}

def song_objects(paths):
    # Bounded parallel probing avoids one process per song in series.
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(sobj,paths))

def playlist_cover(directory,tracks,saved=None):
    if saved is None:saved=_read_json(PLAYLIST_COVERS,{})
    last=saved.get(directory.name) if isinstance(saved,dict) else None
    if isinstance(last,str):
        match=next((p for p in tracks if str(p.resolve())==last),None)
        if match is not None:return cover_url(match)
    # An unplayed playlist has no representative track yet.
    return ''

def playlist_for_track(path):
    try:track=song(path)
    except (ValueError,OSError,TypeError):return ''
    return track.parent.name if track.parent.parent==MUSIC.resolve() else ''

def remember_playlist_track(path):
    try:track=song(path)
    except (ValueError,OSError,TypeError):return
    directory=track.parent
    if directory.parent!=MUSIC.resolve():return
    with QUEUE_SAVE_LOCK:
        data=_read_json(PLAYLIST_COVERS,{})
        if not isinstance(data,dict):data={}
        if data.get(directory.name)==str(track):return
        data[directory.name]=str(track)
        _write_json(PLAYLIST_COVERS,data)
        invalidate_cache('playlists')

def lists():
    def load():
        if not MUSIC.exists():return []
        directories=sorted(
            [item for item in MUSIC.iterdir() if item.is_dir() and not item.name.startswith('.')],
            key=lambda item:item.name.casefold(),
        )
        result=[]
        covers=_read_json(PLAYLIST_COVERS,{})
        for item in directories:
            tracks=files(item)
            result.append({'name':item.name,'count':len(tracks),
                           'cover':playlist_cover(item,tracks,covers)})
        return result
    return cached('playlists',5.0,load)
def atomic(path,text):
    t=path.with_name('.'+path.name+'.'+uuid.uuid4().hex);t.write_text(text);os.replace(t,path)

def song(v):
    p=Path(v).resolve()
    if MUSIC.resolve() not in p.parents or not p.is_file() or p.suffix.lower() not in EXTS:raise ValueError('Unsupported song')
    return p


GROUPS=STATE/'sonobus-groups.json'; LOCALMONPID=STATE/'local-monitor.pid'; LOCALSINK=STATE/'local-output-route.json'
MODES={
'ipad_external':('iPad → external','airplay',False,True),
'laptop_external':('Laptop → external','system',False,True),
'ipad_ipad':('iPad only','airplay',False,True),
'ipad_laptop':('iPad → laptop','airplay',True,False),
'ipad_both':('iPad → iPad + laptop','airplay',True,True),
'laptop_ipad':('Laptop → iPad','system',False,True),
'laptop_laptop':('Laptop only','system',True,False),
'laptop_both':('Laptop → iPad + laptop','system',True,True),
}
MODE_POLICIES={
'ipad_external': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'AirPlay through CamillaDSP to external SonoBus receivers',
},
'laptop_external': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to external SonoBus receivers',
},
'ipad_ipad': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'iPad AirPlay through CamillaDSP and SonoBus back to the iPad only',
},
'ipad_laptop': {
    'sendMute':True,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'AirPlay through CamillaDSP to laptop output only',
},
'ipad_both': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'AirPlay through CamillaDSP to iPad and the highest-priority physical laptop output',
},
'laptop_ipad': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to iPad',
},
'laptop_laptop': {
    'sendMute':True,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to laptop output only',
},
'laptop_both': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to iPad and the highest-priority physical laptop output',
},
}

def _read_json(path,default):
    try:return json.loads(path.read_text())
    except (OSError,ValueError,TypeError):return default
def _write_json(path,value):atomic(path,json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def groups_state():
    default={'active':'default','profiles':{'default':{'group':'rt4817-camilladsp','username':'rt4817','server':'aoo.sonobus.net:10998','passwordRequired':False}}}
    data=_read_json(GROUPS,default); profiles=data.get('profiles') if isinstance(data,dict) else None
    if not isinstance(profiles,dict) or not profiles:return default
    active=data.get('active');return {'active':active if active in profiles else next(iter(profiles)),'profiles':profiles}
def save_group(data):
    key=re.sub(r'[^A-Za-z0-9_.-]+','-',str(data.get('key','')).strip()).strip('-')
    group=str(data.get('group','')).strip();user=str(data.get('username','')).strip();server=str(data.get('server','aoo.sonobus.net:10998')).strip()
    if not key or not group or not user or not server:raise ValueError('Profile, group, username and server are required')
    state=groups_state();state['profiles'][key]={'group':group,'username':user,'server':server,'passwordRequired':bool(data.get('passwordRequired'))};state['active']=key;_write_json(GROUPS,state);return state
def audio_mode():
    try:value=MODE.read_text().strip()
    except OSError:value='ipad_external'
    if value=='airplay':value='ipad_external'
    if value=='system':value='laptop_external'
    if value=='external_roundtrip':value='laptop_external'
    return value if value in MODES else 'ipad_external'
def configure_sonobus(policy=None):
    if not SONOSET.is_file():raise FileNotFoundError(SONOSET)
    policy=dict(policy or MODE_POLICIES[audio_mode()])
    text=SONOSET.read_text(encoding='utf-8')
    device_match=re.search(r'<DEVICESETUP\b[^>]*>',text)
    if device_match is None:raise RuntimeError('SonoBus DEVICESETUP entry was not found')
    device_tag=device_match.group(0)
    attributes={
        'deviceType':'ALSA',
        'audioOutputDeviceName':policy['outputDevice'],
        'audioInputDeviceName':policy['inputDevice'],
        'audioDeviceRate':'96000.0',
        'audioDeviceBufferSize':'512',
    }
    for key,value in attributes.items():
        pattern=rf'\b{re.escape(key)}="[^"]*"';replacement=f'{key}="{value}"'
        if re.search(pattern,device_tag):device_tag=re.sub(pattern,replacement,device_tag)
        else:device_tag=device_tag[:-1]+f' {replacement}>'
    text=text[:device_match.start()]+device_tag+text[device_match.end():]
    parameters={
        'sendchannels':'2.0','defsendqual':'5.0','mastinmute':'0.0',
        'mastsendmute':'1.0' if policy.get('sendMute') else '0.0',
        'mastrecvmute':'1.0' if policy.get('receiveMute',True) else '0.0',
        'dry':'0.0','wet':'0.9999999403953552',
    }
    for key,value in parameters.items():
        pattern=rf'(<PARAM\s+id="{re.escape(key)}"\s+value=")[^"]*("\s*/>)'
        text,count=re.subn(pattern,rf'\g<1>{value}\g<2>',text)
        if count<1:raise RuntimeError('SonoBus settings are missing PARAM entry: '+key)
    temporary=SONOSET.with_name(SONOSET.name+'.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        handle.write(text);handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,SONOSET)
    written=SONOSET.read_text(encoding='utf-8')
    verification={}
    written_device=re.search(r'<DEVICESETUP\b[^>]*>',written)
    if written_device is None:
        raise RuntimeError('SonoBus DEVICESETUP entry disappeared after writing')
    for key,value in attributes.items():
        if not re.search(rf'\b{re.escape(key)}="{re.escape(value)}"',written_device.group(0)):
            raise RuntimeError(f'SonoBus device setting {key} did not persist as {value}')
        verification[key]=value
    for key,value in parameters.items():
        matches=re.findall(rf'<PARAM\s+id="{re.escape(key)}"\s+value="([^"]*)"\s*/>',written)
        if not matches or any(item!=value for item in matches):
            raise RuntimeError(f'SonoBus setting {key} did not persist as {value}')
        verification[key]=value
    return {'policy':policy,'settings':verification}
def sonobus_matches(policy):
    if not sonopids() or not SONOSET.is_file():return False
    try:
        text=SONOSET.read_text(encoding='utf-8')
        tag=re.search(r'<DEVICESETUP\b[^>]*>',text)
        if tag is None:return False
        for key,value in (('audioInputDeviceName',policy['inputDevice']),('audioOutputDeviceName',policy['outputDevice'])):
            if not re.search(rf'\b{key}="{re.escape(value)}"',tag.group(0)):return False
        for key,wanted in (('mastsendmute','1.0' if policy.get('sendMute') else '0.0'),
                           ('mastrecvmute','1.0' if policy.get('receiveMute',True) else '0.0')):
            if not re.search(rf'<PARAM\s+id="{key}"\s+value="{wanted}"',text):return False
        group=groups_state()['profiles'][groups_state()['active']]
        expected=['--group='+group['group'],'--username='+group['username'],'--connectionserver='+group['server']]
        for pid in sonopids():
            args=[x.decode('utf-8','replace') for x in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if x]
            if all(x in args for x in expected):return True
    except (OSError,KeyError,TypeError):pass
    return False
def restart_sonobus(password=None,policy=None,normalize=True):
    global SONOBUS_PROCESS
    with LOCK:
        stop_sonobus();alsa100();applied=configure_sonobus(policy)
        state=groups_state();item=state['profiles'][state['active']]
        # Keep the regular SonoBus application running detached from the web
        # request. Do not add -q/--headless: this build only behaves correctly
        # when the normal application process is running.
        command=[str(SONOBUS),'--group='+item['group'],'--username='+item['username'],'--connectionserver='+item['server']]
        if any(arg in ('-q','--headless') for arg in command):
            raise RuntimeError('Headless SonoBus startup is disabled')
        if item.get('passwordRequired'):
            if not password:raise ValueError('The selected SonoBus group requires a password')
            command.append('--group-password='+str(password))
        logpath=STATE/'sonobus.log'
        with logpath.open('ab',buffering=0) as log:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=media_env())
        SONOBUS_PROCESS=process
        deadline=time.monotonic()+3.0
        while time.monotonic()<deadline:
            code=process.poll()
            if code is not None:
                SONOBUS_PROCESS=None
                tail=log_tail(logpath)[-2500:]
                raise RuntimeError('SonoBus failed to start: '+tail)
            if process.pid in sonopids():break
            time.sleep(.05)
        if normalize:normalize_audio_volumes()
        return {'pid':process.pid,'profile':state['active'],'group':item['group'],**applied}

def stop_local_monitor():
    pid=rpid(LOCALMONPID)
    if alive(pid):
        try:os.killpg(pid,signal.SIGTERM)
        except OSError:pass
        deadline=time.monotonic()+2
        while alive(pid) and time.monotonic()<deadline:time.sleep(.05)
        if alive(pid):
            try:os.killpg(pid,signal.SIGKILL)
            except OSError:pass
    LOCALMONPID.unlink(missing_ok=True)
def _pactl_json(kind):
    result=run([exe('pactl'),'--format=json','list',kind],False,8,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or f'Could not list audio {kind}')
    try:data=json.loads(result.stdout or '[]')
    except json.JSONDecodeError as error:raise RuntimeError(f'Invalid pactl {kind} response: {error}')
    return data if isinstance(data,list) else []
def _profile_rows(card):
    raw=card.get('profiles') or []
    if isinstance(raw,dict):raw=[dict(value or {},name=name) for name,value in raw.items()]
    rows=[]
    for item in raw:
        if not isinstance(item,dict):continue
        name=str(item.get('name') or '').strip()
        if not name or name=='off':continue
        try:count=int(item.get('sinks',item.get('n_sinks',1)))
        except (TypeError,ValueError):count=0
        if count<1:continue
        availability=str(item.get('available','unknown')).lower()
        label=str(item.get('description') or name)
        rows.append({'name':name,'label':label,'priority':int(item.get('priority') or 0),
                     'available':availability})
    return sorted(rows,key=lambda item:(item['available'] in ('no','false'),-item['priority'],item['label'].casefold()))
def _port_rows(sink):
    raw=sink.get('ports') or []
    if isinstance(raw,dict):raw=[dict(value or {},name=name) for name,value in raw.items()]
    rows=[]
    for item in raw:
        if not isinstance(item,dict):continue
        name=str(item.get('name') or '').strip()
        if not name:continue
        available=str(item.get('availability',item.get('available','unknown'))).lower()
        rows.append({'name':name,'label':str(item.get('description') or name),'available':available,'priority':int(item.get('priority') or 0)})
    return sorted(rows,key=lambda item:(item['available'] not in ('yes','true'),-item['priority'],item['label'].casefold()))
def saved_output_route():
    data=_read_json(LOCALSINK,{})
    if isinstance(data,dict):return {key:str(data.get(key) or '') for key in ('card','profile','sink','port')}
    return {'card':'','profile':'','sink':'','port':''}
def pipewire_sink_owners():
    # Prefer the live PipeWire node -> device relationship over name guessing.
    try:
        result=run([exe('pw-dump')],False,8,media_env())
        if result.returncode:return {}
        graph=json.loads(result.stdout)
        devices={str(item.get('id')):str((item.get('info') or {}).get('props',{}).get('device.name') or '')
                 for item in graph if item.get('type')=='PipeWire:Interface:Device'}
        return {str(props['node.name']):devices.get(str(props.get('device.id')),'')
                for item in graph if item.get('type')=='PipeWire:Interface:Node'
                for props in [(item.get('info') or {}).get('props') or {}]
                if props.get('node.name') and props.get('media.class')=='Audio/Sink'}
    except (OSError,ValueError,TypeError,subprocess.TimeoutExpired):return {}

def sink_owner(sink,cards):
    # A live card index or PipeWire device.name is evidence of ownership.
    # Never infer ownership from the ALSA node name or selected UI card.
    index=sink.get('cardIndex')
    if index:
        owner=next((card for card in cards if card['index']==index),None)
        if owner:return owner
    name=sink.get('cardName')
    if name:
        return next((card for card in cards if card['name']==name),None)
    return None
def audio_topology():
    cards_raw=_pactl_json('cards');sinks_raw=_pactl_json('sinks');saved=saved_output_route();pw_owners=pipewire_sink_owners()
    sinks=[]
    for sink in sinks_raw:
        if not isinstance(sink,dict):continue
        name=str(sink.get('name') or '')
        props=sink.get('properties') or {}
        # Match audio-switch.sh's final sink stage: expose every real sink,
        # including HDMI and sinks created after a profile change. Hide only
        # this setup's custom DSP, loopback and silent-routing sinks.
        # Exclude the module-created desktop sink, not physical sinks whose
        # names happen to contain words such as 'loopback' or 'sonobus'.
        if name=='camilladsp' or (name.startswith('alsa_output.') and str(props.get('alsa.card_name') or '').lower()=='loopback'):continue
        card_index=str(sink.get('card') if sink.get('card') is not None else '')
        card_name=str(pw_owners.get(name) or props.get('device.name') or '')
        label=str(sink.get('description') or props.get('device.description') or name)
        ports=_port_rows(sink)
        if not ports:ports=[{'name':'','label':label,'available':'unknown','priority':0}]
        active_port=sink.get('active_port') or {}
        active_port=str(active_port.get('name') or '') if isinstance(active_port,dict) else str(active_port)
        sinks.append({'name':name,'label':label,'cardIndex':card_index,'cardName':card_name,'ports':ports,'activePort':active_port})
    cards=[]
    for card in cards_raw:
        if not isinstance(card,dict):continue
        name=str(card.get('name') or '');index=str(card.get('index',''));props=card.get('properties') or {}
        profiles=_profile_rows(card)
        if not name or not profiles:continue
        label=str(props.get('device.description') or props.get('alsa.card_name') or card.get('description') or name)
        active=card.get('active_profile') or {};active_name=str(active.get('name') if isinstance(active,dict) else active or '')
        # Keep the sink menu global; the selected sink's owner is recorded separately.
        cards.append({'name':name,'index':index,'label':label,'profiles':profiles,'activeProfile':active_name,'sinks':sinks[:]})
    cards.sort(key=lambda item:item['label'].casefold())
    for card in cards:
        card['sinks'].sort(key=lambda sink:(sink_owner(sink,cards)!=card,sink['label'].casefold()))
    return {'cards':cards,'saved':saved}
def set_output_profile(card,profile,normalize=True,topology=None):
    card=str(card or '').strip();profile=str(profile or '').strip()
    if topology is None:topology=audio_topology()
    item=next((x for x in topology['cards'] if x['name']==card),None)
    if item is None:raise ValueError('Selected audio card is unavailable')
    if profile not in {x['name'] for x in item['profiles']}:raise ValueError('Selected card profile is unavailable')
    if next(x for x in item['profiles'] if x['name']==profile)['available'] in ('no','false'):
        raise RuntimeError('Playback profile is reported unavailable by the audio device: '+profile)
    if item['activeProfile']==profile:
        updated=audio_topology()
        if normalize:normalize_audio_volumes()
        return updated
    result=run([exe('pactl'),'set-card-profile',card,profile],False,10,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not activate audio card profile')
    previous={x['name'] for x in item['sinks'] if sink_owner(x,topology['cards'])==item}
    deadline=time.monotonic()+5.0
    while time.monotonic()<deadline:
        for current in _pactl_json('cards'):
            if not isinstance(current,dict) or current.get('name')!=card:continue
            active=current.get('active_profile') or {}
            active_name=active.get('name') if isinstance(active,dict) else active
            if active_name==profile:
                updated=audio_topology()
                selected=next((x for x in updated['cards'] if x['name']==card),None)
                owned={x['name'] for x in selected['sinks'] if sink_owner(x,updated['cards'])==selected} if selected else set()
                if owned and (owned!=previous or not previous):
                    if normalize:normalize_audio_volumes()
                    return updated
            break
        time.sleep(.1)
    updated=audio_topology()
    current=next((x for x in updated['cards'] if x['name']==card),None)
    if current is None or current['activeProfile']!=profile:
        raise RuntimeError('Audio card profile did not become active: '+profile)
    # A cross-card output can still be selected; report the actual live sinks
    # instead of accepting an unrelated sink as proof of this card's readiness.
    if normalize:normalize_audio_volumes()
    return updated
def choose_output_route(requested=None):
    explicit=isinstance(requested,dict)
    remembered=bool(requested.get('_remembered')) if explicit else False
    topology=audio_topology();cards=topology['cards']
    requested=requested if explicit else topology['saved']
    if not cards:raise RuntimeError('No physical laptop audio card was found')
    card_name=str(requested.get('card') or '')
    selected_card=next((x for x in cards if x['name']==card_name),None)
    if selected_card is None and explicit and card_name:raise RuntimeError('Selected audio card is unavailable: '+card_name)
    sink_name=str(requested.get('sink') or '')
    if selected_card is None:
        # A route copied from another laptop must not force its old card/profile.
        sinks=cards[0]['sinks']
        default=run([exe('pactl'),'get-default-sink'],False,5,media_env()).stdout.strip()
        candidate=next((x for x in sinks if x['name']==default and sink_owner(x,cards)),None)
        if candidate is None:candidate=next((x for x in sinks if x['name']==sink_name and sink_owner(x,cards)),None)
        if candidate is None:candidate=next((x for x in sinks if sink_owner(x,cards) and x['name']!=sink_name),None)
        if candidate is None:candidate=next((x for x in sinks if sink_owner(x,cards)),None)
        if candidate is None:raise RuntimeError('No playback sink with a verified card owner is available')
        selected_card=sink_owner(candidate,cards)
        sink_name=candidate['name']
    profile_name=str(requested.get('profile') or '')
    if profile_name not in {x['name'] for x in selected_card['profiles']}:
        if explicit and profile_name:raise RuntimeError('Selected card profile is unavailable: '+profile_name)
        profile_name=next((x['name'] for x in selected_card['profiles'] if x['name']==selected_card['activeProfile'] and x['available'] not in ('no','false')),None) or next((x['name'] for x in selected_card['profiles'] if x['available'] not in ('no','false')),None)
        if not profile_name:raise RuntimeError('No available playback profile on '+selected_card['label'])
    if next(x for x in selected_card['profiles'] if x['name']==profile_name)['available'] in ('no','false'):
        raise RuntimeError('Selected playback profile is reported unavailable: '+profile_name)
    if selected_card['activeProfile']!=profile_name:
        topology=set_output_profile(selected_card['name'],profile_name,normalize=False,topology=topology)
        cards=topology['cards']
        selected_card=next(x for x in cards if x['name']==selected_card['name'])
        if sink_name and not any(x['name']==sink_name for x in selected_card['sinks']):
            deadline=time.monotonic()+2.0
            while time.monotonic()<deadline:
                topology=audio_topology();cards=topology['cards']
                selected_card=next(x for x in cards if x['name']==selected_card['name'])
                if any(x['name']==sink_name for x in selected_card['sinks']):break
                time.sleep(.1)
    sink=next((x for x in selected_card['sinks'] if x['name']==sink_name),None)
    if sink is None:
        if explicit and sink_name and not remembered:raise RuntimeError('Selected playback sink is unavailable: '+sink_name)
        sink=next((x for x in selected_card['sinks'] if sink_owner(x,cards)==selected_card and any(p['available'] not in ('no','false') for p in x['ports'])),None)
        if sink is None:raise RuntimeError('The selected card profile exposes no available physical sink; choose an explicit sink on another card or another playback profile')
    port_name=str(requested.get('port') or '') if sink['name']==str(requested.get('sink') or '') else ''
    port=next((x for x in sink['ports'] if x['name']==port_name),None)
    if port is None:
        if explicit and port_name and not remembered:raise RuntimeError('Selected playback port is unavailable: '+port_name)
        port=next((x for x in sink['ports'] if x['name']==sink.get('activePort') and x['available'] not in ('no','false')),None)
        if port is None:port=next((x for x in sink['ports'] if x['available'] not in ('no','false')),None)
        if port is None:raise RuntimeError('No currently available port on '+sink['label'])
    if port['available'] in ('no','false'):
        if not remembered:raise RuntimeError('Selected output port is reported unavailable: '+port['name'])
        port=next((x for x in sink['ports'] if x['name']==sink.get('activePort') and x['available'] not in ('no','false')),None) or next((x for x in sink['ports'] if x['available'] not in ('no','false')),None)
        if port is None:raise RuntimeError('No currently available port on '+sink['label'])
    if port['name'] and port['name']!=sink.get('activePort'):
        result=run([exe('pactl'),'set-sink-port',sink['name'],port['name']],False,10,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not activate the selected output port')
    fresh=audio_topology();cards=fresh['cards']
    live=next((x for x in (cards[0]['sinks'] if cards else []) if x['name']==sink['name']),None)
    if live is None or not any(x['name']==port['name'] and x['available'] not in ('no','false') for x in live['ports']) or (port['name'] and live['activePort']!=port['name']):
        raise RuntimeError('Selected output or port disappeared; reopen the output picker')
    sink=live
    owner=sink_owner(sink,cards)
    if owner is None:owner_card='';owner_profile=''
    else:owner_card=owner['name'];owner_profile=owner['activeProfile']
    route={'card':selected_card['name'],'profile':selected_card['activeProfile'],
           'sink':sink['name'],'port':port['name'],'label':sink['label']+((' · '+port['label']) if port['name'] else ''),
           'sinkCard':owner_card,'sinkProfile':owner_profile}
    return route
OUTPUT_CHOICES=STATE/'output-choices.json'
CARD_CHOICES=STATE/'output-card-profiles.json'
def remembered_card_profile(card):
    saved=_read_json(CARD_CHOICES,{})
    return str(saved.get(card) or '') if isinstance(saved,dict) else ''

def remembered_output(card,profile):
    choices=_read_json(OUTPUT_CHOICES,{})
    if not isinstance(choices,dict):return {}
    entry=choices.get(card+'\n'+profile,{})
    return entry if isinstance(entry,dict) else {}
def remember_output(route):
    choices=_read_json(OUTPUT_CHOICES,{})
    if not isinstance(choices,dict):choices={}
    choices[route['card']+'\n'+route['profile']]={key:route.get(key,'') for key in ('card','profile','sink','port')}
    _write_json(OUTPUT_CHOICES,choices)
    card_profiles=_read_json(CARD_CHOICES,{})
    if not isinstance(card_profiles,dict):card_profiles={}
    card_profiles[route['card']]=route['profile']
    _write_json(CARD_CHOICES,card_profiles)
def select_output_step(mode,card,profile=None,sink=None,port=None,password=None):
    with LOCK:
        if mode not in MODES or not MODES[mode][2]:raise ValueError('Select a local playback mode')
        topology=audio_topology()
        selected=next((item for item in topology['cards'] if item['name']==card),None)
        if selected is None:raise ValueError('Selected audio card is unavailable')
        profile=profile or remembered_card_profile(card) or selected['activeProfile']
        if not profile:raise ValueError('Choose a playback profile')
        if profile not in {item['name'] for item in selected['profiles']}:raise ValueError('Playback profile is unavailable')
        previous=remembered_output(card,profile)
        request={'card':card,'profile':profile}
        if sink is not None:
            request['sink']=sink
            request['port']=port or ''
        elif previous.get('sink'):
            # The remembered sink may only appear after activating its profile.
            # The resolver validates it against the post-profile graph.
            request.update(sink=previous['sink'],port=previous.get('port',''),_remembered=True)
        result=set_mode(mode,password=password,output=request)
        return {'mode':result,'topology':output_state()}
def output_state():
    topology=audio_topology();topology['selected']=topology['saved'].copy()
    choices=_read_json(OUTPUT_CHOICES,{})
    topology['remembered']=choices if isinstance(choices,dict) else {}
    topology['rememberedProfiles']=_read_json(CARD_CHOICES,{})
    return topology
def start_local_monitor(route=None,resolved=False):
    # A pre-resolved route was selected under the mode lock; do not apply it twice.
    selected=route if resolved else choose_output_route(route)
    sink=selected['sink']
    stop_local_monitor()
    command='set -o pipefail; "$1" -q -D camilladsp_output_shared -r 96000 -f S32_LE -c 2 -t raw | "$2" --playback --device="$3" --rate=96000 --format=s32le --channels=2'
    args=['/run/current-system/sw/bin/bash','-c',command,'monitor',exe('arecord'),exe('pacat'),sink]
    log=(STATE/'local-monitor.log').open('ab',buffering=0)
    try:process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=media_env())
    finally:log.close()
    LOCALMONPID.write_text(str(process.pid)+'\n')
    # Confirm that this monitor's pacat stream is on the requested sink.
    deadline=time.monotonic()+4.0
    while time.monotonic()<deadline:
        if process.poll() is not None:break
        try:
            target=next((row.get('index') for row in _pactl_json('sinks') if row.get('name')==sink),None)
            if target is not None:
                for row in _pactl_json('sink-inputs'):
                    props=row.get('properties') or {}
                    pid=str(props.get('application.process.id') or '')
                    if row.get('sink')==target and pid.isdecimal():
                        try:
                            if os.getpgid(int(pid))==process.pid:
                                _write_json(LOCALSINK,selected)
                                remember_output(selected)
                                return {'pid':process.pid,**selected}
                        except (OSError,ProcessLookupError):pass
        except (OSError,RuntimeError,subprocess.TimeoutExpired):pass
        time.sleep(.1)
    stop_local_monitor()
    raise RuntimeError('Local playback stream did not appear on '+sink+'; check '+str(STATE/'local-monitor.log'))
def mode_state():
    if STOPPED.exists() and not alive(rpid(CAMPID),'camilladsp'):
        return {'mode':'stopped','label':'No source selected','source':'',
            'localWanted':False,'sonobusWanted':False,'camilla':False,
            'sonobus':False,'localMonitor':False,'systemAudio':False,
            'airplay':False,'localOutput':saved_output_route(),
            'localOutputLabel':'','sonobusPolicy':{}}
    name=audio_mode();label,source,local,sono=MODES[name];policy=MODE_POLICIES[name]
    local_pid=rpid(LOCALMONPID)
    route=_read_json(LOCALSINK,{})
    if not isinstance(route,dict):route={}
    selected={key:str(route.get(key) or '') for key in ('card','profile','sink','port')}
    return {
        'mode':name,'label':label,'source':source,'localWanted':local,'sonobusWanted':sono,
        'camilla':alive(rpid(CAMPID),'camilladsp'),'sonobus':bool(sonopids()),'localMonitor':alive(local_pid),
        'systemAudio':run(['systemctl','--user','is-active',SYSTEM_AUDIO_SERVICE],False,5).stdout.strip()=='active',
        'airplay':run(['systemctl','is-active','shairport-sync.service'],False,5).stdout.strip()=='active',
        'localOutput':selected,'localOutputLabel':(route.get('label') or selected['sink'] or 'Automatic'),'sonobusPolicy':policy,
    }
def apply_mode(name,password=None,restore_camilla=True,output=None,normalize=True,output_resolved=False):
    if name not in MODES:raise ValueError('Invalid audio mode')
    label,source,local,sono=MODES[name];policy=MODE_POLICIES[name]
    if STOPPED.exists():
        user_service('start','pipewire.socket')
        user_service('start','pipewire-pulse.socket')
        user_service('start','wireplumber.service')
    if restore_camilla and not alive(rpid(CAMPID),'camilladsp'):
        try:saved=ACTIVE.read_text().strip()
        except OSError:saved=''
        available=profiles()
        selected=profile(saved) if saved else available[0]
        start_camilla(selected)
    apply_source_services(source)
    if local:
        selected=output if output_resolved else choose_output_route(output)
        current=saved_output_route()
        wanted={key:selected[key] for key in ('card','profile','sink','port')}
        if not alive(rpid(LOCALMONPID)) or current!=wanted:
            start_local_monitor(selected,resolved=True)
    elif alive(rpid(LOCALMONPID)):stop_local_monitor()
    if sono:
        if not sonobus_matches(policy):restart_sonobus(password,policy,normalize=False)
    elif sonopids():stop_sonobus()
    MODE.write_text(name+'\n')
    if normalize:normalize_audio_volumes()
    STOPPED.unlink(missing_ok=True)
    if source=='system' and QUEUE_FILE.is_file() and not alive(rpid(MPVPID),'mpv'):
        try:ensure_mpv()
        except (OSError,RuntimeError):pass
    return mode_state()
def set_mode(name,password=None,output=None):
    with LOCK:
        if name not in MODES:raise ValueError('Invalid audio mode')
        selected=choose_output_route(output) if MODES[name][2] else None
        if STOPPED.exists() and alive(rpid(CAMPID),'camilladsp'):
            stop_local_monitor();stop_camilla(include_stale=True)
        # Do not release CamillaDSP or SonoBus just because the source mode changed.
        return apply_mode(name,password,output=selected,output_resolved=selected is not None)
def mpv(command):
    with PLAYERLOCK:
        for attempt in range(2):
            try:
                ensure_mpv();request_id=random.randint(1,2147483647);payload={'command':command,'request_id':request_id}
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(4);client.connect(str(MPVSOCK));client.sendall((json.dumps(payload)+'\n').encode());stream=client.makefile('rb')
                    for line in stream:
                        response=json.loads(line)
                        if response.get('request_id')!=request_id:continue
                        if response.get('error')!='success':raise RuntimeError(response.get('error') or 'mpv command failed')
                        return response.get('data')
                raise RuntimeError('mpv returned no matching response')
            except (OSError,ValueError,RuntimeError):
                if attempt:raise
                stop_mpv()

def queue_playlist_name(entries=None,validate_files=True):
    data=_read_json(QUEUE_SOURCE,{})
    if not isinstance(data,dict):return ''
    name=data.get('playlist')
    if not isinstance(name,str) or not name:return ''
    try:directory=pdir(name)
    except ValueError:return ''
    if not directory.is_dir():return ''
    # Reject stale markers if another client replaced the MPV queue.
    if entries is None:
        try:entries=mpv_direct(['get_property','playlist']) or []
        except (OSError,RuntimeError,ValueError):return ''
    if not isinstance(entries,list) or not entries:return ''
    expected=data.get('files')
    if not isinstance(expected,list) or not expected:return ''
    try:
        raw=[item['filename'] for item in entries]
        if raw==expected and not validate_files:return name
        actual=[str(song(path)) for path in raw]
    except (KeyError,TypeError,ValueError,OSError):return ''
    return name if actual==expected else ''

def shuffle_current_playlist():
    with PLAYERLOCK:
        name=queue_playlist_name()
        if not name:raise ValueError('Shuffle Queue requires a playlist queue, not an individual song')
        source=_read_json(QUEUE_SOURCE,{})
        paths=list(source['files'])
        if len(paths)<2:raise ValueError('At least two playlist tracks are needed to shuffle')
        random.SystemRandom().shuffle(paths)
        playlist=MUSIC/f'{name}-shuffled.m3u'
        atomic(playlist,'#EXTM3U\n'+'\n'.join(os.path.relpath(path,MUSIC) for path in paths)+'\n')
        # Update the saved shuffle for the next playlist-icon click only.
        # Never touch MPV's current queue, track, position or pause state.
        return {'playlist':name,'shuffled':True,'count':len(paths)}

def play_song(path):
    track=song(path)
    # A restored queue deliberately starts paused. Replace it, then clear
    # pause after MPV has actually selected the new file, not just when the
    # loadfile IPC command has been accepted.
    with PLAYERLOCK:
        QUEUE_SOURCE.unlink(missing_ok=True)
        mpv(['loadfile',str(track),'replace'])
        mpv(['set_property','pause',False])
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try:
                current=mpv_direct(['get_property','path'])
                if current==str(track):
                    mpv(['set_property','pause',False])
                    if mpv_direct(['get_property','pause']) is False:
                        remember_playlist_track(str(track))
                        save_mpv_queue(force=True)
                        return sobj(track)
            except (OSError,ValueError,RuntimeError):pass
            time.sleep(.05)
    raise RuntimeError('MPV did not start the selected song; check '+str(MPVLOG))

def play_list(name,shuffle=False):
    name=pname(name)
    directory=pdir(name)
    if not directory.is_dir():raise FileNotFoundError('Playlist not found: '+name)
    if shuffle:
        playlist=MUSIC/f'{name}-shuffled.m3u'
        current={str(track.resolve()) for track in files(directory)}
        if not current:raise ValueError('Playlist is empty')
        saved=[]
        if playlist.is_file():
            for line in playlist.read_text().splitlines():
                if not line or line.startswith('#'):continue
                item=Path(line)
                saved.append(str((playlist.parent/item).resolve()))
        if len(saved)!=len(current) or len(set(saved))!=len(saved) or set(saved)!=current:
            raise ValueError('Saved shuffle is missing or out of date. Play the regular playlist, then use Shuffle for next play to update it.')
    else:
        tracks=files(directory)
        if not tracks:raise ValueError('Playlist is empty')
        playlist=MUSIC/f'{name}.m3u'
        atomic(playlist,'#EXTM3U\n'+'\n'.join(os.path.relpath(str(track.resolve()),MUSIC) for track in tracks)+'\n')
    if not playlist.is_file():raise FileNotFoundError(str(playlist))
    # Read the selected M3U in its stored order, never reshuffle on icon click.
    paths=[]
    for line in playlist.read_text().splitlines():
        if not line or line.startswith('#'):continue
        target=Path(line)
        if not target.is_absolute():target=(playlist.parent/target)
        paths.append(str(song(target)))
    if not paths:raise ValueError('Playlist is empty')
    with PLAYERLOCK:
        QUEUE_SOURCE.unlink(missing_ok=True)
        mpv(['loadlist',str(playlist),'replace'])
        mpv(['set_property','pause',False])
        _write_json(QUEUE_SOURCE,{'playlist':name,'files':paths})
        save_mpv_queue(force=True)
    deadline=time.monotonic()+5
    last={}
    while time.monotonic()<deadline:
        last=player_state()
        if last.get('available'):
            return {'playlist':name,'shuffled':shuffle,'state':last}
        time.sleep(.05)
    return {'playlist':name,'shuffled':shuffle,'state':last,'pending':True}
DISPLAY_FILE=STATE/'display-output.json'
DISPLAY_LOCK=threading.RLock()
def display_state():
    saved=_read_json(DISPLAY_FILE,{})
    return {'off':bool(isinstance(saved,dict) and saved.get('output')),
            'output':saved.get('output','') if isinstance(saved,dict) else ''}
def sway_outputs():
    result=run([exe('swaymsg'),'-r','-t','get_outputs'],False,5,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Sway outputs unavailable')
    outputs=json.loads(result.stdout)
    if not isinstance(outputs,list):raise RuntimeError('Invalid Sway output list')
    return outputs
def display_power(on):
    with DISPLAY_LOCK:
        saved=display_state()
        if on:
            name=saved['output']
            if not name:return saved
            # Never turn on another output when the saved display was unplugged.
            if not any(row.get('name')==name for row in sway_outputs()):
                raise RuntimeError('Saved display is disconnected: '+name)
        else:
            if saved['off']:return saved
            focused=next((row for row in sway_outputs()
                          if row.get('focused') and row.get('active') and row.get('name')),None)
            if focused is None:raise RuntimeError('No focused active Sway output')
            name=focused['name']
        # Quote the live output name as one Sway argument; no output is hardcoded.
        command='output '+json.dumps(name)+' power '+('on' if on else 'off')
        result=run([exe('swaymsg'),'-r',command],False,5,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not change display power')
        replies=json.loads(result.stdout)
        if not isinstance(replies,list) or not replies or not all(row.get('success') for row in replies):
            raise RuntimeError('Sway rejected display power change: '+str(replies))
        if on:DISPLAY_FILE.unlink(missing_ok=True)
        else:_write_json(DISPLAY_FILE,{'output':name})
        return display_state()
AWAY_FILE=STATE/'away-state.json'
AWAY_LOCK=threading.RLock()
# Only freeze user-facing Sway application trees, never the session or audio graph.
AWAY_PROTECTED={'sway','swaybar','swaybg','swayidle','swaylock','swaynag',
    'pipewire','pipewire-pulse','wireplumber','camilladsp','sonobus','SonoBus',
    'shairport-sync','nqptp','arecord','pacat','mpv','systemd','dbus-daemon',
    'dbus-broker','Xwayland','wayvnc','tailscaled','nm-applet'}
def away_identity(pid):
    try:
        text=Path(f'/proc/{pid}/stat').read_text()
        rest=text.rsplit(') ',1)[1].split()
        if rest[0]=='Z':return None
        return (int(rest[1]),rest[19],Path(f'/proc/{pid}/comm').read_text().strip())
    except (OSError,ValueError,IndexError):return None
def away_state():
    data=_read_json(AWAY_FILE,{})
    return {'active':bool(isinstance(data,dict) and data.get('active')),
            'pausedWindows':len(data.get('processes',[])) if isinstance(data,dict) else 0}
def away_window_pids():
    result=run([exe('swaymsg'),'-r','-t','get_tree'],False,5,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Sway tree unavailable')
    tree=json.loads(result.stdout);found=set();stack=[tree]
    while stack:
        node=stack.pop()
        if not isinstance(node,dict):continue
        stack.extend(node.get('nodes') or []);stack.extend(node.get('floating_nodes') or [])
        if node.get('type')=='con' and isinstance(node.get('pid'),int):found.add(node['pid'])
    return found
def away_processes():
    entries={};uid=os.getuid()
    for item in Path('/proc').iterdir():
        if not item.name.isdecimal():continue
        try:
            if item.stat().st_uid!=uid:continue
            pid=int(item.name);identity=away_identity(pid)
            if identity:entries[pid]=identity
        except OSError:continue
    children={}
    for pid,(parent,_,_) in entries.items():children.setdefault(parent,[]).append(pid)
    protected={os.getpid(),os.getppid()}
    protected.update(filter(None,(rpid(SERVERPID),rpid(CAMPID),rpid(MPVPID),rpid(LOCALMONPID))))
    protected.update(sonopids())
    for pid,(_,_,name) in entries.items():
        if name in AWAY_PROTECTED:protected.add(pid)
    # Never freeze an ancestor of a protected process (including the terminal
    # from which the server or audio-switch was launched).
    for pid in list(protected):
        seen=set()
        while pid in entries and pid not in seen:
            seen.add(pid);protected.add(pid);pid=entries[pid][0]
    candidates=set()
    for root in away_window_pids():
        stack=[root]
        while stack:
            pid=stack.pop()
            if pid in candidates or pid not in entries:continue
            candidates.add(pid);stack.extend(children.get(pid,[]))
    # Exclude any tree containing an audio or control-plane process.
    for pid in list(protected):
        while pid in entries and pid in candidates:
            candidates.discard(pid);pid=entries[pid][0]
    return [(pid,entries[pid][1]) for pid in sorted(candidates)
            if pid not in protected and entries[pid][2] not in AWAY_PROTECTED]
def set_away(active):
    with AWAY_LOCK:
        old=_read_json(AWAY_FILE,{})
        was=bool(isinstance(old,dict) and old.get('active'))
        if bool(active)==was:return away_state()
        if active:
            # Discover before pausing anything: if Sway IPC is unavailable,
            # do not leave media half-paused or create an unrecoverable state.
            targets=away_processes()
            playing=[]
            try:
                for player in mpris_players():
                    if mpris_status(player)=='Playing':
                        result=run([exe('playerctl'),'--player',player,'pause'],False,5,media_env())
                        if result.returncode==0:playing.append(player)
            except (OSError,RuntimeError,subprocess.TimeoutExpired):pass
            frozen=[]
            try:
                for pid,start in targets:
                    identity=away_identity(pid)
                    if identity and identity[1]==start and not Path(f'/proc/{pid}/status').read_text().split('State:',1)[1].lstrip().startswith('T'):
                        try:os.kill(pid,signal.SIGSTOP);frozen.append([pid,start])
                        except (OSError,PermissionError):pass
                _write_json(AWAY_FILE,{'active':True,'processes':frozen,'playing':playing})
            except Exception:
                for pid,start in reversed(frozen):
                    identity=away_identity(pid)
                    if identity and identity[1]==start:
                        try:os.kill(pid,signal.SIGCONT)
                        except (OSError,PermissionError):pass
                for player in playing:
                    run([exe('playerctl'),'--player',player,'play'],False,5,media_env())
                raise
        else:
            # Thaw recorded processes before MPRIS Play, since browser media
            # players cannot handle D-Bus commands while frozen.
            for pid,start in reversed(old.get('processes',[])):
                identity=away_identity(pid)
                if identity and identity[1]==start:
                    try:os.kill(pid,signal.SIGCONT)
                    except (OSError,PermissionError):pass
            AWAY_FILE.unlink(missing_ok=True)
            for player in old.get('playing',[]):
                if player in mpris_players():
                    run([exe('playerctl'),'--player',player,'play'],False,5,media_env())
        return away_state()
COMBINED_LOCK=threading.RLock()
COMBINED_FILE=STATE/'away-display.lock'
def away_display_state():
    away=away_state();display=display_state()
    return {'active':away['active'] or display['off'],
            'away':away['active'],'displayOff':display['off'],
            'pausedWindows':away['pausedWindows']}
def set_away_display(active):
    # The CLI is a one-shot process: this lock serializes it with HTTP requests.
    STATE.mkdir(parents=True,exist_ok=True)
    with COMBINED_LOCK, COMBINED_FILE.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        previous=away_display_state()
        if active:
            if not previous['away']:set_away(True)
            try:
                if not previous['displayOff']:display_power(False)
            except Exception:
                if not previous['away']:set_away(False)
                raise
        else:
            # Always thaw even if the recorded display was unplugged.
            display_error=None
            if previous['displayOff']:
                try:display_power(True)
                except Exception as error:display_error=error
            if previous['away']:set_away(False)
            if display_error:raise display_error
        return away_display_state()
def toggle_away_display():
    return set_away_display(not away_display_state()['active'])
def full_state():
    return {
        'mode':mode_state(),'awayDisplay':away_display_state(),
        'profiles':{'profiles':[item.name for item in profiles()],'filters':listening_filters(),'active':active(),'running':alive(rpid(CAMPID),'camilladsp')},
        'player':player_state(),'systemMedia':system_state(),'groups':groups_state(),'playlists':lists(),
    }

def volatile_state():
    return {
        'audioStopSignal':True,
        'mode':mode_state(),'awayDisplay':away_display_state(),
        'profiles':{'active':active(),'filters':listening_filters(),'running':alive(rpid(CAMPID),'camilladsp')},
        'player':player_state(),'groups':groups_state(),
    }

PAGE='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>CamillaDSP Studio</title><style>\n:root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif;--v:#865dff;--b:#3f8eff;--g:#35d6a0}*{box-sizing:border-box}body{margin:0;min-height:100svh;padding:22px 16px 36px;color:#fff;background:radial-gradient(900px 520px at 50% -150px,#6542a8,#211a38 44%,#070910);background-attachment:fixed}main{max-width:540px;margin:auto}.hidden{display:none!important}.hero,.card{border:1px solid #ffffff22;background:linear-gradient(145deg,#ffffff1c,#ffffff0d);box-shadow:inset 0 1px #ffffff22,0 20px 50px #0005;backdrop-filter:blur(22px)}.hero{padding:25px 22px;border-radius:29px}.card{padding:18px;border-radius:24px;margin:14px 0}.card+.grid{margin-top:14px}.eyebrow,.label,.section-title{color:#bcb7cb;text-transform:uppercase;letter-spacing:.1em;font-size:12px;font-weight:800}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--g);box-shadow:0 0 15px var(--g);margin-right:8px}h1{margin:10px 0 5px;font-size:32px;letter-spacing:-.04em}.subtitle,.details,.meta{color:#b9b4c5;font-size:13px}.section-title{margin:27px 3px 10px}.title{font-size:20px;font-weight:800;margin-top:6px;overflow-wrap:anywhere}.grid{display:grid;gap:10px}.two{grid-template-columns:1fr 1fr}.three{grid-template-columns:1fr 1.2fr 1fr}.pc-transport{margin-bottom:18px}.pc-library-actions{margin-top:18px}.wide{grid-column:1/-1}button,input{font:inherit}button{border:0;color:#fff;cursor:pointer}.action,.item,.transport,.back{width:100%;transition:transform .12s,filter .15s}.action:active,.item:active,.transport:active,.back:active{transform:scale(.96);filter:brightness(1.15)}.action{min-height:54px;border-radius:18px;background:#ffffff1b;font-weight:780}.primary{background:linear-gradient(135deg,var(--b),var(--v))}.green{background:linear-gradient(135deg,#24ae7c,#287d95)}.danger{background:linear-gradient(135deg,#e64e74,#8e2b4b)}.active{outline:2px solid #a783ff;background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important}.search{width:100%;min-height:49px;border:1px solid #ffffff22;border-radius:17px;padding:12px 15px;color:#fff;background:#ffffff12;outline:0}.search:focus{border-color:#9b7aff;box-shadow:0 0 0 4px #825dff2e}.list{display:grid;gap:10px;margin-top:10px}.cover-art{width:48px;height:48px;flex:none;object-fit:cover;border-radius:11px;background:#ffffff18}.cover-art.large{width:100%;max-width:220px;height:auto;aspect-ratio:1;display:block;margin:0 auto 16px;border-radius:20px}.cover-fallback{display:grid;place-items:center;color:#c3b6f3;font-size:24px}.cover-fallback.large{display:grid;font-size:68px}.item-cover{display:flex;align-items:center;gap:12px;min-width:0}.item-cover .copy{min-width:0;flex:1;overflow-wrap:anywhere;font-size:16px;line-height:1.3}.item-cover .name{font-size:16px;line-height:1.3;font-weight:780}.item-cover .meta{font-size:13px;line-height:1.35;font-weight:400}.item{text-align:left;min-height:64px;padding:13px 15px;border-radius:19px;background:#ffffff16}.name{display:block;font-weight:780}.meta{display:block;margin-top:4px}.playlist-row{position:relative}\n.playlist-open{display:block;padding-right:120px;min-height:76px}\n.playlist-controls{position:absolute;right:12px;top:50%;transform:translateY(-50%);display:flex;gap:8px;z-index:1}\n.playlist-icon{display:grid;place-items:center;width:44px;height:44px;border:1px solid #ffffff38;border-radius:14px;background:#242036e8;box-shadow:0 4px 14px #0005}\n.playlist-icon:hover{background:#564282}\n.playlist-icon:active{transform:scale(.94)}\n.playlist-icon .media-icon{width:20px;height:20px}\n.playlist-icon.busy{font-size:20px}.header{display:grid;grid-template-columns:72px 1fr 72px;align-items:center}.header h1{text-align:center;font-size:23px}.back{min-height:43px;border-radius:15px;background:#ffffff18}.range{--fill:0%;width:100%;height:36px;background:transparent;appearance:none}.range::-webkit-slider-runnable-track{height:6px;border-radius:99px;background:linear-gradient(to right,#fff var(--fill),#ffffff2d var(--fill))}.range::-webkit-slider-thumb{appearance:none;width:21px;height:21px;margin-top:-7.5px;border-radius:50%;background:#fff;box-shadow:0 3px 10px #0008}.times{display:flex;justify-content:space-between;color:#aaa5b5;font-size:12px}.range-row{display:grid;grid-template-columns:24px 1fr 24px;align-items:center;gap:7px}.transport{display:grid;place-items:center;min-height:78px;border-radius:999px;background:#ffffff19}.transport.main{min-height:98px;background:linear-gradient(145deg,#9e68ff,#583ad2)}.media-icon{display:block;width:34px;height:34px;fill:none;stroke:#fff;stroke-width:2.15;stroke-linecap:round;stroke-linejoin:round;pointer-events:none}.transport.main .media-icon{width:42px;height:42px}.icon-fill{fill:#fff;stroke:#fff}.range{touch-action:none}.range.dragging::-webkit-slider-thumb{transform:scale(1.08)}.source-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}\n\n/* Final interaction and consistency pass */\nbutton{position:relative;overflow:hidden;-webkit-user-select:none;user-select:none;touch-action:manipulation}\nbutton:focus-visible,.search:focus-visible,.range:focus-visible{outline:2px solid #b79cff;outline-offset:3px}\nbutton:disabled{opacity:.58;cursor:default}\n.action,.item,.transport,.back{will-change:transform}\n.action.busy::after,.item.busy::after{content:"";position:absolute;inset:0;background:linear-gradient(105deg,transparent 25%,#ffffff2e 50%,transparent 75%);animation:ui-shine .8s linear infinite}\n@keyframes ui-shine{from{transform:translateX(-100%)}to{transform:translateX(100%)}}\n.transport{transition:transform .12s ease,filter .15s ease,box-shadow .2s ease}\n.transport.main.playing{box-shadow:0 18px 42px #6b45dd77,inset 0 1px #ffffff35}\n.media-icon{transition:transform .16s ease}.transport:active .media-icon{transform:scale(.9)}\n.range{cursor:pointer}.range.dragging::-webkit-slider-thumb{transform:scale(1.1)}\n.range::-webkit-slider-runnable-track{transition:background .08s linear}\n.mode-active{outline:2px solid #72e5bc;background:linear-gradient(135deg,#24ae7c,#287d95)!important}\n#repeat.active,#shuffle.active{outline:2px solid #a783ff;background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important}\n.output-modal{position:fixed;inset:0;z-index:50;display:grid;place-items:end center;padding:18px;background:#05060bbb;backdrop-filter:blur(12px)}.output-sheet{width:min(100%,540px);max-height:82svh;overflow:auto;padding:20px;border:1px solid #ffffff2b;border-radius:26px;background:linear-gradient(145deg,#272139,#11131c);box-shadow:0 28px 80px #000b}.output-options{display:grid;gap:10px;margin:16px 0}.output-choice{display:grid;grid-template-columns:24px 1fr;gap:10px;align-items:center;padding:14px;border-radius:18px;background:#ffffff12}.output-choice input{width:20px;height:20px;accent-color:#865dff}.output-choice strong,.output-choice span{display:block}.output-choice span{margin-top:3px;color:#aaa5b5;font-size:11px;overflow-wrap:anywhere}.modal-actions{display:grid;grid-template-columns:1fr 1.4fr;gap:10px}\n\n@media (prefers-reduced-motion:reduce){*{animation-duration:.001ms!important;transition-duration:.001ms!important;scroll-behavior:auto!important}}\n\n/* Page rhythm and accessible navigation. */\n#profiles>.hero{margin-bottom:20px}\n#profiles>.action{display:block;margin:14px 0 20px}\n#profiles>.grid{margin-top:12px}\n#profiles>.section-title{margin-top:26px;margin-bottom:12px}\n#groups-page .header,#listening-page .header,#songs .header,#playlist .header,#pc .header{margin-bottom:18px}\n#groups-page .card{display:grid;gap:12px}\n#groups-page .card .search,#groups-page .card .action{margin:0}\n#groups-page .card label{display:flex;align-items:center;gap:9px}\n.card>.details{margin-top:6px}\n#pc .card>.label{margin-bottom:8px}\n#listening-page .search{margin-bottom:16px}\n#song-search,#playlist-search{display:block;margin:0 0 16px}\n#song-list,#playlist-songs{margin-top:0}\n.system-media-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}\n.card .range{display:block;margin-top:12px}\n.card .times{margin-top:2px}\n.card .range-row{margin:14px 0 16px}\n.card .grid{margin-top:14px}\n.range-row{grid-template-columns:24px minmax(0,1fr) 24px;gap:10px}\n.volume-icon{display:grid;place-items:center;color:#d6d3de;pointer-events:none}\n.volume-icon svg{display:block;width:20px;height:20px}\n.local-now-header{display:flex;align-items:center;gap:14px;min-width:0;margin:10px 0 16px}\n.local-now-copy{flex:1;min-width:0}\n.local-now-copy .details{margin-top:6px;overflow-wrap:anywhere}\n.local-now-artwork{flex:0 0 72px;width:72px;height:72px;position:relative;display:grid;place-items:center;overflow:hidden;border-radius:15px;background:linear-gradient(145deg,#433b68,#222d42);color:#c8c1e9;font-size:32px;line-height:1}\n.local-now-artwork::before{content:\'♫\'}\n.local-now-artwork img{position:absolute;inset:0;display:block;width:100%;height:100%;object-fit:cover}\n.back-to-top{position:fixed;z-index:40;top:max(14px,env(safe-area-inset-top));left:50%;transform:translateX(-50%);width:auto;min-height:44px;padding:9px 17px;border:1px solid #ffffff38;border-radius:999px;background:#262039f2;box-shadow:0 8px 28px #0009;white-space:nowrap;font-size:14px;font-weight:750;backdrop-filter:blur(14px)}\n.back-to-top:active{transform:translateX(-50%) scale(.96)}\n\n/* Playlist actions: clear affordance without changing the page layout. */\n.playlist-row{border-radius:19px;transition:background .16s ease}\n.playlist-row:hover{background:#ffffff08}\n.playlist-icon{transition:background .16s ease,border-color .16s ease,transform .12s ease}\n.playlist-icon:hover{border-color:#aa90ff;background:#493572}\n#shuffle:disabled{opacity:.42;cursor:not-allowed}\n#shuffle:not(:disabled){background:linear-gradient(135deg,#6252a7,#373b77)}\n\n/* Unified indigo, blue and mint palette. */\n:root{--v:#8574f5;--b:#6c9cff;--g:#55d9ae}\nbody{background:radial-gradient(900px 520px at 50% -150px,#514784,#1d2039 48%,#0b101b)}\n.hero,.card{border-color:#a8b7ef25;background:linear-gradient(145deg,#b8c8ff18,#a8b8ee0a)}\n.action:not(.primary):not(.green):not(.danger):not(.active){background:#a8b7ef1b}\n#shuffle:not(:disabled){background:linear-gradient(135deg,#6257ae,#465b99)}\n.playlist-icon{background:#242941e8;border-color:#b7c7ff38}\n.playlist-icon:hover{background:#414a77;border-color:#9caeff}\n.output-sheet{background:linear-gradient(145deg,#252b47,#111827)}\n\n/* Shared home palette and compact destination layout. */\n:root{--v:#8574f5;--b:#6c9cff;--g:#55d9ae}\n#profiles{max-width:480px;margin-inline:auto}\n#profiles>.hero{padding:17px 18px;border-radius:23px;margin-bottom:12px}\n#profiles>.hero h1{font-size:27px;margin:6px 0 3px}\n#profiles>.card{padding:16px;margin:10px 0;border-radius:21px}\n#profiles>.section-title{margin:18px 3px 8px}\n#profiles>.action{min-height:48px;margin:10px 0 12px;border-radius:16px}\n#profiles .source-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:8px}\n#profiles .source-grid .source-heading{grid-column:1/-1;color:#bcb7cb;font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;margin:9px 2px 0}\n#profiles .source-grid .action{min-height:50px;padding:8px 5px;border-radius:15px;font-size:14px;line-height:1.2}\n#profiles .card .grid{margin-top:10px}\n#profiles .transport{min-height:64px}\n#profiles .transport.main{min-height:80px}\n#profiles .pc-transport{margin-bottom:12px}\n</style></head><body><main>\n<section id="profiles"><div class="hero"><div class="eyebrow"><span class="dot"></span><span id="engine-status">Audio engine offline</span></div><h1>CamillaDSP Studio</h1><div class="subtitle">System audio, AirPlay and PC Music through CamillaDSP and SonoBus.</div></div><div class="section-title">System media</div><div class="card"><div class="system-media-heading"><div class="label">Active desktop player</div></div><div id="system-title" class="title">No system media</div><div id="system-details" class="details"></div><input id="system-seek" class="range" type="range" aria-label="System media playback position" min="0" max="1" step=".1"><div class="times"><span id="system-elapsed">0:00</span><span id="system-duration">0:00</span></div><div class="range-row"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 4V5L7 9H3z"/></svg></span><input id="system-volume" class="range" type="range" aria-label="Master volume" min="0" max="100" value="100"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 9v6h4l5 4V5L6 9H2z"/><path d="M14 8.2a5 5 0 0 1 0 7.6l1.4 1.4a7 7 0 0 0 0-10.4L14 8.2z"/><path d="M17 5.3a9 9 0 0 1 0 13.4l1.4 1.4a11 11 0 0 0 0-16.2L17 5.3z"/></svg></span></div><div class="grid three"><button id="system-previous" class="transport" aria-label="Previous"><svg class="media-icon" viewBox="0 0 24 24"><path d="M6 5v14"></path><path d="M18 6.5 8.5 12 18 17.5z"></path></svg></button><button id="system-toggle" class="transport main" aria-label="Play"><svg class="media-icon" viewBox="0 0 24 24"><path id="system-play-shape" class="icon-fill" d="M8 5.5 19 12 8 18.5z"></path></svg></button><button id="system-next" class="transport" aria-label="Next"><svg class="media-icon" viewBox="0 0 24 24"><path d="M18 5v14"></path><path d="M6 6.5 15.5 12 6 17.5z"></path></svg></button></div></div><button id="open-pc" class="action green">Local Music Library</button><button id="away-display-toggle" class="action" type="button" aria-pressed="false" style="margin-top:10px">Away and display off</button><div class="section-title">Audio source</div><div class="card"><div id="source-title" class="title">Loading…</div><div id="source-details" class="details"></div><div id="mode-grid" class="source-grid"></div></div><div class="section-title">SonoBus Group</div><div class="card"><div class="label">Selected group</div><div id="sonobus-group" class="title">Loading…</div><div id="sonobus-status" class="details">Checking SonoBus…</div></div><button id="open-groups" class="action">Manage SonoBus Group</button><div class="section-title">Active profile</div><div class="card"><div id="active-profile" class="title">Loading…</div><div id="active-state" class="details"></div></div><button id="open-listening" class="action">Select Listening Profile</button><div class="section-title" id="restart-heading">Restart services</div><div class="grid two" aria-labelledby="restart-heading"><button id="restart-camilla" class="action">Restart CamillaDSP</button><button id="restart-sonobus" class="action">Restart SonoBus</button><button id="restart-airplay" class="action">Restart AirPlay</button><button id="restart-vnc" class="action">Restart VNC</button></div></section>\n<section id="groups-page" class="hidden"><div class="header"><button data-back="profiles" class="back">Back</button><h1>SonoBus Group</h1><div></div></div><div class="card"><select id="group-select" class="search"></select><input id="group-key" class="search" placeholder="Profile name"><input id="group-name" class="search" placeholder="Group name"><input id="group-user" class="search" placeholder="Username"><input id="group-server" class="search" value="aoo.sonobus.net:10998" placeholder="Connection server"><label class="details"><input id="group-required" type="checkbox"> Password required</label><input id="group-password" class="search" type="password" placeholder="Password (never saved)"><button id="save-group" class="action primary">Save Group Profile</button></div></section>\n<section id="listening-page" class="hidden"><div class="header"><button data-back="profiles" class="back">Back</button><h1>Listening Profiles</h1><div></div></div><div class="section-title">Listening profiles</div><input id="profile-search" class="search" placeholder="Search profiles"><div id="profile-list"></div></section>\n<section id="pc" class="hidden"><div class="header"><button id="pc-back" class="back">Back</button><h1>PC Music</h1><div></div></div><div class="card"><div class="label">Now playing</div><div class="local-now-header"><span id="now-cover" class="local-now-artwork" aria-hidden="true"></span><div class="local-now-copy"><div id="now-title" class="title">Nothing playing</div><div id="now-details" class="details"></div></div></div><input id="seek" class="range" type="range" aria-label="Local music playback position" min="0" max="1" step=".1"><div class="times"><span id="elapsed">0:00</span><span id="duration">0:00</span></div><div class="range-row"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 4V5L7 9H3z"/></svg></span><input id="volume" class="range" type="range" aria-label="Master volume" min="0" max="100" value="100"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 9v6h4l5 4V5L6 9H2z"/><path d="M14 8.2a5 5 0 0 1 0 7.6l1.4 1.4a7 7 0 0 0 0-10.4L14 8.2z"/><path d="M17 5.3a9 9 0 0 1 0 13.4l1.4 1.4a11 11 0 0 0 0-16.2L17 5.3z"/></svg></span></div><div class="grid two"><button id="repeat" class="action">Repeat Off</button><button id="shuffle" class="action" title="Update the saved shuffled playlist without changing what is playing">Shuffle for next play</button></div></div><div class="grid three pc-transport"><button data-cmd="previous" class="transport" aria-label="Previous"><svg class="media-icon" viewBox="0 0 24 24"><path d="M6 5v14"></path><path d="M18 6.5 8.5 12 18 17.5z"></path></svg></button><button data-cmd="toggle" class="transport main" aria-label="Play"><svg class="media-icon" viewBox="0 0 24 24"><path id="local-play-shape" class="icon-fill" d="M8 5.5 19 12 8 18.5z"></path></svg></button><button data-cmd="next" class="transport" aria-label="Next"><svg class="media-icon" viewBox="0 0 24 24"><path d="M18 5v14"></path><path d="M6 6.5 15.5 12 6 17.5z"></path></svg></button></div><div class="grid pc-library-actions"><button id="all-songs" class="action primary">All Songs</button></div><div class="section-title">Playlists</div><div id="playlists" class="list"></div></section>\n<section id="songs" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1>All Songs</h1><div></div></div><input id="song-search" class="search" type="search" aria-label="Search all songs" placeholder="Search all songs"><div id="song-list" class="list"></div></section>\n<section id="playlist" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1 id="playlist-title">Playlist</h1><div></div></div><input id="playlist-search" class="search" type="search" aria-label="Search this playlist" placeholder="Search this playlist"><div id="playlist-songs" class="list"></div></section><button id="back-to-top" class="back-to-top hidden" type="button">Go back to top of page ↑</button><div id="output-modal" class="output-modal hidden"><div class="output-sheet"><div class="label">Laptop playback device</div><div id="output-mode-title" class="title">Choose output</div><div class="details">Choose a card and profile, then a sink and port. Every card, profile, sink and port choice applies immediately. Back to home keeps the last successful choice.</div><div class="output-options"><label class="details">Card<select id="output-card" class="search"></select></label><label class="details">Profile<select id="output-profile" class="search"></select></label><label class="details">Sink / port<select id="output-sink" class="search"></select></label></div><div class="modal-actions"><button id="output-cancel" class="action">Back to home</button></div></div></div>\n</main><script>const $=selector=>document.querySelector(selector);\nconst screens=[\'profiles\',\'pc\',\'songs\',\'playlist\',\'groups-page\',\'listening-page\'].map(id=>$(\'#\'+id));\nlet all=[],inside=[],current=\'\';\n\nconst state={\n  system:{slider:$(\'#system-seek\'),elapsed:$(\'#system-elapsed\'),durationLabel:$(\'#system-duration\'),volume:$(\'#system-volume\'),duration:0,dragging:false,polling:false,repoll:false,clockPosition:0,clockAt:0,clockPlaying:false,lastAvailableAt:0,trackKey:\'\',skipPending:false,skipFrom:\'\',skipStarted:0,skipWarned:false,volumeHold:0,volumeTimer:null,volumePending:null},\n  local:{slider:$(\'#seek\'),elapsed:$(\'#elapsed\'),durationLabel:$(\'#duration\'),volume:$(\'#volume\'),duration:0,dragging:false,polling:false,volumeHold:0,volumeTimer:null,volumePending:null}\n};\n\nconst fmt=value=>{const v=Math.max(0,Number(value)||0);return Math.floor(v/60)+\':\'+String(Math.floor(v)%60).padStart(2,\'0\')};\nfunction show(id){screens.forEach(screen=>screen.classList.toggle(\'hidden\',screen.id!==id));scrollTo({top:0,behavior:\'smooth\'});updateBackToTop()}\nfunction updateBackToTop(){const visible=[\'songs\',\'playlist\'].some(id=>!$(\'#\'+id).classList.contains(\'hidden\'));$(\'#back-to-top\').classList.toggle(\'hidden\',!visible||window.scrollY<320)}\nwindow.addEventListener(\'scroll\',updateBackToTop,{passive:true});$(\'#back-to-top\').onclick=()=>window.scrollTo({top:0,behavior:\'smooth\'});$(\'#open-groups\').onclick=()=>show(\'groups-page\');$(\'#open-listening\').onclick=()=>show(\'listening-page\');\nfunction notificationBox(){const banners=[...document.querySelectorAll(\'#global-task-feedback\')];let banner=banners.shift()||null;banners.forEach(item=>item.remove());if(!banner){banner=document.createElement(\'div\');banner.id=\'global-task-feedback\';banner.setAttribute(\'role\',\'status\');banner.setAttribute(\'aria-live\',\'polite\');banner.style.cssText=\'position:fixed;left:12px;right:12px;top:12px;z-index:5000;box-sizing:border-box;padding:14px 64px 14px 18px;border-radius:14px;background:#152033;color:#eef5ff;border:1px solid #43638f;box-shadow:0 12px 32px rgba(0,0,0,.4);font-weight:700;text-align:center;\';const message=document.createElement(\'span\');message.className=\'task-feedback-message\';const close=document.createElement(\'button\');close.type=\'button\';close.className=\'task-feedback-close\';close.textContent=\'×\';close.setAttribute(\'aria-label\',\'Close notification\');close.style.cssText=\'position:absolute;right:8px;top:50%;transform:translateY(-50%);width:48px;height:48px;border:0;border-radius:12px;background:transparent;color:inherit;font-size:38px;font-weight:400;line-height:42px;cursor:pointer;\';close.addEventListener(\'click\',()=>{clearTimeout(showTaskFeedback.timer);banner.classList.add(\'hidden\')});banner.append(message,close);document.body.appendChild(banner)}return banner}function showTaskFeedback(message,kind=\'working\'){const banner=notificationBox(),messageNode=banner.querySelector(\'.task-feedback-message\');if(messageNode)messageNode.textContent=String(message||\'Working…\');banner.style.background=kind===\'done\'?\'#163d2b\':(kind===\'error\'?\'#4a2025\':\'#152033\');banner.style.borderColor=kind===\'done\'?\'#2f7654\':(kind===\'error\'?\'#a64b56\':\'#43638f\');banner.classList.remove(\'hidden\');clearTimeout(showTaskFeedback.timer);if(kind!==\'working\')showTaskFeedback.timer=setTimeout(()=>banner.classList.add(\'hidden\'),10000)}function note(message,error=false){if(error&&message)showTaskFeedback(message,\'error\')}\nasync function api(url,options={}){const response=await fetch(url,{cache:\'no-store\',...options});const data=await response.json().catch(()=>null);if(!data||typeof data!==\'object\'||Array.isArray(data))throw Error(\'Invalid server response\');if(!response.ok||data.ok===false)throw Error(data.error||\'Request failed\');return data}\nconst post=(url,data={})=>api(url,{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(data)});\nfunction fill(element,value,maximum){const max=Number(maximum),v=Number(value);const percent=Number.isFinite(max)&&max>0&&Number.isFinite(v)?Math.max(0,Math.min(100,v/max*100)):0;element.style.setProperty(\'--fill\',percent+\'%\')}\nfunction renderTimeline(media,position,duration){\n  const total=Number.isFinite(Number(duration))?Math.max(0,Number(duration)):0;\n  const current=Number.isFinite(Number(position))?Math.max(0,Math.min(Number(position),total>0?total:Number(position))):0;\n  media.duration=total;\n  media.slider.max=String(total>0?total:1);\n  if(!media.dragging)media.slider.value=String(current);\n  const shown=media.dragging?Number(media.slider.value):current;\n  fill(media.slider,shown,total);\n  media.elapsed.textContent=fmt(shown);\n  media.durationLabel.textContent=fmt(total);\n}\n\nfunction renderActiveProfile(data){\n  const info=data.filters?.[data.active];\n  $(\'#active-profile\').textContent=data.active?(info?.group||\'Other\'):\'No profile\';\n  $(\'#active-state\').textContent=data.active?((info?.label||data.active)+\' · \'+(data.running?\'Running\':\'Stopped\')):\'Stopped\'\n}\nfunction renderSonobusStatus(mode,groups){\n  const selected=groups?.profiles?.[groups.active];\n  $(\'#sonobus-group\').textContent=selected?.group||\'No group selected\';\n  $(\'#sonobus-status\').textContent=(mode.sonobus?\'Process running\':\'Process stopped\')+\n    (selected?\' • Profile: \'+groups.active+\' • User: \'+selected.username:\'\')\n}\nasync function busy(button,work,label=\'Working…\',doneLabel=\'Done\',feedback=false){if(button.disabled)return;const original=button.innerHTML;button.disabled=true;button.classList.add(\'busy\');if(!button.classList.contains(\'transport\'))button.textContent=label;if(feedback)showTaskFeedback(label,\'working\');try{const result=await work();if(feedback&&doneLabel)showTaskFeedback(doneLabel,\'done\');return result}catch(error){showTaskFeedback(error?.message||String(error),\'error\');throw error}finally{button.disabled=false;button.classList.remove(\'busy\');button.innerHTML=original}}\nfunction setPlayIcon(shape,button,playing){if(!shape||!button)return;shape.setAttribute(\'d\',playing?\'M8 5h3v14H8z M14 5h3v14h-3z\':\'M8 5.5 19 12 8 18.5z\');button.setAttribute(\'aria-label\',playing?\'Pause\':\'Play\');button.classList.toggle(\'playing\',playing)}\n\nfunction setSystemAvailability(data){for(const id of [\'system-previous\',\'system-toggle\',\'system-next\'])$(\'#\'+id).disabled=!data.available;$(\'#system-seek\').disabled=!data.available||!data.seekable}\nfunction systemTrackKey(data){\n  if(!data||!data.available)return \'\';\n  return [data.player||\'\',data.trackId||\'\',data.title||\'\',data.artist||\'\',data.duration||\'\'].join(\'\\x1f\')\n}\nfunction resetSystemPosition(){\n  const media=state.system;\n  media.clockPlaying=false;media.clockAt=0;media.clockPosition=0;\n  media.dragging=false;media.slider.classList.remove(\'dragging\');\n  renderTimeline(media,0,0)\n}\nfunction systemPositionError(message){\n  const media=state.system;\n  if(!media.skipWarned){media.skipWarned=true;showTaskFeedback(message,\'error\')}\n}\nfunction renderSystem(data){\n  const media=state.system,now=performance.now();\n  if(!data||typeof data!==\'object\')return;\n  if(!data.available&&media.lastAvailableAt&&now-media.lastAvailableAt<1500){\n    if(media.skipPending)resetSystemPosition();\n    return\n  }\n  if(data.available)media.lastAvailableAt=now;\n  else media.lastAvailableAt=0;\n  $(\'#system-title\').textContent=data.title||\'No system media\';\n  $(\'#system-details\').textContent=[data.artist,data.player,data.status].filter(Boolean).join(\' • \');\n  const key=systemTrackKey(data),duration=Number(data.duration),position=data.position===null||data.position===undefined||data.position===\'\'?NaN:Number(data.position);\n  const valid=!!data.available&&Number.isFinite(duration)&&duration>0&&Number.isFinite(position)&&position>=0&&position<=duration+2;\n  if(media.skipPending){\n    // Ignore snapshots from the old track after the skip command.\n    const changed=key&&key!==media.skipFrom;\n    const restarted=key&&key===media.skipFrom&&valid&&position<=3;\n    if(!changed&&!restarted){\n      resetSystemPosition();\n      if(now-media.skipStarted>2000)systemPositionError(\'Could not confirm the new track position. Marker held at 0:00.\');\n      return\n    }\n    media.skipPending=false;media.skipWarned=false\n  }\n  media.trackKey=key;\n  if(!valid){\n    resetSystemPosition();\n    if(data.available)systemPositionError(\'System media position is unavailable or invalid. Marker held at 0:00.\');\n  }else{\n    media.skipWarned=false;\n    media.clockPosition=Math.min(position,duration);\n    media.clockAt=now;\n    media.clockPlaying=!!data.playing;\n    renderTimeline(media,media.clockPosition,duration)\n  }\n  setSystemAvailability(data);\n  if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1)media.volumePending=null;\n  if(media.volumePending===null&&now>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){\n    displayMasterVolume(data.volume)\n  }\n  setPlayIcon($(\'#system-play-shape\'),$(\'#system-toggle\'),!!data.playing)\n}\nfunction tickSystem(){\n  const media=state.system;\n  if(document.hidden||media.skipPending||!media.clockPlaying||media.dragging||!media.clockAt||media.duration<=0)return;\n  renderTimeline(media,Math.min(media.duration,media.clockPosition+(performance.now()-media.clockAt)/1000),media.duration)\n}\nasync function pollSystem(force=false){\n  const media=state.system;\n  if(media.polling){if(force)media.repoll=true;return}\n  media.polling=true;\n  try{renderSystem(await api(\'/api/system-media\'))}\n  catch(error){resetSystemPosition();systemPositionError(\'System media update failed: \'+(error?.message||String(error)))}\n  finally{media.polling=false;if(media.repoll){media.repoll=false;pollSystem()}}\n}\nasync function pollLocal(){const media=state.local;if(media.polling)return;media.polling=true;try{if(!$(\'#pc\').classList.contains(\'hidden\')){const data=await api(\'/api/player\');$(\'#now-title\').textContent=data.title||\'Nothing playing\';updateLocalNowArtwork(data.cover||\'\');$(\'#now-details\').textContent=[data.artist,data.album].filter(Boolean).join(\' • \')||(data.path||\'\');renderTimeline(media,data.currentTime,data.duration);if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1){media.volumePending=null}if(media.volumePending===null&&performance.now()>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){displayMasterVolume(data.volume)}$(\'#shuffle\').disabled=!data.canShuffleQueue;$(\'#repeat\').textContent=\'Repeat \'+({off:\'Off\',all:\'All\',one:\'1\'}[data.repeat]||\'Off\');$(\'#repeat\').classList.toggle(\'active\',data.repeat!==\'off\');setPlayIcon($(\'#local-play-shape\'),document.querySelector(\'[data-cmd="toggle"]\'),data.playing)}}catch(error){note(error.message,true)}finally{media.polling=false}}\n\nfunction bindSeek(media,url){\n  const slider=media.slider;\n  const begin=()=>{media.dragging=true;slider.classList.add(\'dragging\')};\n  const end=()=>{media.dragging=false;slider.classList.remove(\'dragging\')};\n  slider.addEventListener(\'pointerdown\',begin);\n  slider.addEventListener(\'pointercancel\',end);\n  slider.addEventListener(\'touchstart\',begin,{passive:true});\n  slider.oninput=()=>{media.dragging=true;const value=Number(slider.value);fill(slider,value,media.duration);media.elapsed.textContent=fmt(value)};\n  slider.onchange=async()=>{const target=Math.max(0,Number(slider.value)||0);end();try{await post(url,{seconds:target});if(media===state.system)await pollSystem(true)}catch(error){note(error.message,true);if(media===state.system)await pollSystem(true)}};\n}\nfunction displayMasterVolume(value,origin=null){\n  const v=Number(value);\n  if(!Number.isFinite(v))return;\n  for(const media of [state.system,state.local]){\n    if(media!==origin && (document.activeElement===media.volume || media.volumePending!==null))continue;\n    media.volume.value=String(v);fill(media.volume,v,100)\n  }\n}\nfunction bindVolume(media,url){\n  const control=media.volume;\n  const send=()=>{\n    const value=Math.max(0,Math.min(100,Number(control.value)||0));\n    for(const item of [state.system,state.local]){\n      item.volumeHold=performance.now()+1500;item.volumePending=value;\n    }\n    for(const item of [state.system,state.local]){item.volume.value=String(value);fill(item.volume,value,100)}\n    post(url,{volume:value}).then(result=>{\n      const actual=Number(result.volume);\n      if(Number.isFinite(actual)){\n        for(const item of [state.system,state.local])item.volumePending=null;\n        displayMasterVolume(actual)\n      }\n    }).catch(error=>{\n      for(const item of [state.system,state.local])item.volumePending=null;\n      note(error.message,true);pollSystem(true);pollLocal()\n    })\n  };\n  control.oninput=()=>{\n    const value=Number(control.value);\n    media.volumeHold=performance.now()+1500;\n    for(const item of [state.system,state.local]){item.volume.value=String(value);fill(item.volume,value,100)}\n    clearTimeout(media.volumeTimer);media.volumeTimer=setTimeout(send,90)\n  };\n  control.onchange=()=>{clearTimeout(media.volumeTimer);send()}\n}\n\nfunction coverNode(url,large=false){const node=document.createElement(url?\'img\':\'div\');node.className=\'cover-art\'+(large?\' large\':\'\')+(url?\'\':\' cover-fallback\');if(url){node.src=url;node.loading=large?\'eager\':\'lazy\';node.alt=\'Album cover\';node.onerror=()=>{const fallback=coverNode(\'\',large);fallback.id=node.id;fallback.dataset.failedCover=url;node.replaceWith(fallback)}}else{node.textContent=\'♫\';node.setAttribute(\'aria-label\',\'No album art\')}return node}\nlet localNowArtworkURL=\'\';\nfunction updateLocalNowArtwork(url){const next=url||\'\';if(next===localNowArtworkURL)return;localNowArtworkURL=next;const tile=$(\'#now-cover\');tile.replaceChildren();if(!next)return;const image=document.createElement(\'img\');image.alt=\'\';image.decoding=\'async\';image.addEventListener(\'error\',()=>image.remove(),{once:true});image.src=next;tile.appendChild(image)}\nfunction updateCover(id,url){const current=$(\'#\'+id);if(!current)return;if(url&&((current.tagName===\'IMG\'&&current.getAttribute(\'src\')===url)||current.dataset.failedCover===url))return;if(!url&&current.classList.contains(\'cover-fallback\'))return;const next=coverNode(url,true);next.id=id;current.replaceWith(next)}\nfunction render(sel,data,query){const list=$(sel);list.replaceChildren();const term=query.toLowerCase();const filtered=data.filter(song=>!term||[song.title,song.artist,song.album,song.relative].join(\' \').toLowerCase().includes(term));if(!filtered.length){const empty=document.createElement(\'div\');empty.className=\'card details\';empty.textContent=query?\'No matches\':\'Nothing here yet\';list.append(empty);return}const fragment=document.createDocumentFragment();for(const song of filtered){const button=document.createElement(\'button\');button.className=\'item\';button.innerHTML=\'<span class="name"></span><span class="meta"></span>\';button.children[0].textContent=song.title;button.children[1].textContent=[song.artist,song.album].filter(Boolean).join(\' • \')||song.relative;const wrap=document.createElement(\'div\');wrap.className=\'item-cover\';const copy=document.createElement(\'div\');copy.className=\'copy\';copy.append(...button.children);wrap.append(coverNode(song.cover),copy);button.append(wrap);button.onclick=async()=>{try{await busy(button,()=>post(\'/api/play/song\',{path:song.path}),\'Playing…\');note(\'Playing \'+song.title);await refreshVolatile()}catch(error){note(error.message,true)}};fragment.append(button)}list.append(fragment)}\nconst MODE_LABELS={ipad_ipad:\'External\',laptop_laptop:\'Laptop\',ipad_laptop:\'Laptop\',ipad_both:\'Both\',laptop_ipad:\'External\',laptop_both:\'Both\'};const LOCAL_MODES=new Set([\'ipad_laptop\',\'ipad_both\',\'laptop_laptop\',\'laptop_both\']);let pendingMode=null,outputTopology=null,lastOutputRefresh=0,outputProfileBusy=false;const outputCard=$(\'#output-card\'),outputProfile=$(\'#output-profile\'),outputSink=$(\'#output-sink\');function selectedCard(){return outputTopology?.cards.find(item=>item.name===outputCard.value)}function fillSinks(preferred=\'\'){const card=selectedCard();outputSink.replaceChildren();for(const sink of card?.sinks||[])for(const port of sink.ports){const value=JSON.stringify({card:card.name,profile:outputProfile.value,sink:sink.name,port:port.name});const option=new Option(sink.label+(port.name?\' · \'+port.label:\'\')+(port.available===\'no\'||port.available===\'false\'?\' (unavailable)\':\'\'),value);option.disabled=port.available===\'no\'||port.available===\'false\';outputSink.add(option)}const saved=outputTopology?.remembered?.[card?.name+\'\\n\'+outputProfile.value]||{};const wanted=preferred||JSON.stringify({card:card?.name,profile:outputProfile.value,sink:saved.sink,port:saved.port});const match=[...outputSink.options].find(option=>!option.disabled&&option.value===wanted);outputSink.value=match?match.value:\'\'}\nfunction fillProfiles(preferred=\'\'){const card=selectedCard();outputProfile.replaceChildren();for(const item of card?.profiles||[]){const option=new Option(item.label+(item.available===\'no\'||item.available===\'false\'?\' (unavailable)\':\'\'),item.name);option.disabled=item.available===\'no\'||item.available===\'false\';outputProfile.add(option)}outputProfile.value=card?.profiles?.some(item=>item.name===preferred&&item.available!==\'no\'&&item.available!==\'false\')?preferred:(card?.activeProfile||\'\');if(outputProfile.selectedOptions[0]?.disabled)outputProfile.value=card?.profiles?.find(item=>item.available!==\'no\'&&item.available!==\'false\')?.name||\'\';fillSinks()}\nasync function applyMode(mode,output=null){const b=document.querySelector(`[data-mode="${mode}"]`);try{await busy(b,()=>post(\'/api/mode\',{mode,password:$(\'#group-password\').value,output}),\'Applying audio route…\',\'Audio route activated\',true);await refreshAll()}catch(error){note(error.message,true)}}\nasync function chooseOutput(mode){pendingMode=mode;outputTopology=await api(\'/api/outputs\');lastOutputRefresh=performance.now();const saved=outputTopology.saved||{};outputCard.replaceChildren(...outputTopology.cards.map(item=>new Option(item.label,item.name)));if(!outputTopology.cards.length)throw Error(\'No playback cards available\');outputCard.value=outputTopology.cards.some(item=>item.name===saved.card)?saved.card:outputCard.value;fillProfiles(outputTopology.rememberedProfiles?.[outputCard.value]||(outputCard.value===saved.card?saved.profile:\'\'));$(\'#output-mode-title\').textContent=MODE_LABELS[mode];$(\'#output-modal\').classList.remove(\'hidden\')}\nasync function applyOutputStep(kind){if(outputProfileBusy||!pendingMode)return;const card=selectedCard();if(!card||!outputProfile.value)return;const previous=outputTopology,oldCard=outputCard.value,oldProfile=card.activeProfile,oldSink=outputSink.value,chosenProfile=outputProfile.value;outputProfileBusy=true;outputCard.disabled=true;outputProfile.disabled=true;outputSink.disabled=true;$(\'#output-cancel\').disabled=true;try{let route=null;if(kind===\'sink\'){if(!outputSink.value||outputSink.selectedOptions[0]?.disabled)return;route=JSON.parse(outputSink.value)}const request={mode:pendingMode,card:card.name,profile:outputProfile.value,password:$(\'#group-password\').value};if(route){request.sink=route.sink;request.port=route.port}showTaskFeedback(\'Applying audio output…\',\'working\');const result=await post(\'/api/output-step\',request);outputTopology=result.topology;lastOutputRefresh=performance.now();outputCard.replaceChildren(...outputTopology.cards.map(item=>new Option(item.label,item.name)));outputCard.value=card.name;fillProfiles(chosenProfile);if(route)fillSinks(JSON.stringify(route));showTaskFeedback(\'Audio output applied\',\'done\');await refreshVolatile()}catch(error){try{outputTopology=await api(\'/api/outputs\');outputCard.replaceChildren(...outputTopology.cards.map(item=>new Option(item.label,item.name)));outputCard.value=outputTopology.cards.some(item=>item.name===oldCard)?oldCard:outputCard.value;fillProfiles();}catch(_){outputTopology=previous;outputCard.value=oldCard;fillProfiles(oldProfile)}showTaskFeedback(error?.message||String(error),\'error\')}finally{outputProfileBusy=false;outputCard.disabled=false;outputProfile.disabled=false;outputSink.disabled=false;$(\'#output-cancel\').disabled=false}}\noutputCard.onchange=()=>{fillProfiles(outputTopology?.rememberedProfiles?.[outputCard.value]||\'\');applyOutputStep(\'card\')};outputProfile.onchange=()=>{fillSinks();applyOutputStep(\'profile\')};outputSink.onchange=()=>applyOutputStep(\'sink\');for(const [source,items] of [[\'External\',[\'ipad_ipad\',\'ipad_laptop\',\'ipad_both\']],[\'Laptop\',[\'laptop_ipad\',\'laptop_laptop\',\'laptop_both\']]]){const heading=document.createElement(\'div\');heading.className=\'source-heading\';heading.textContent=\'Playing from \'+source;$(\'#mode-grid\').append(heading);for(const key of items){const b=document.createElement(\'button\');b.className=\'action\';b.dataset.mode=key;b.textContent=MODE_LABELS[key];b.onclick=async()=>{try{if(LOCAL_MODES.has(key))await chooseOutput(key);else await applyMode(key)}catch(error){note(error.message,true)}};$(\'#mode-grid\').append(b)}}$(\'#output-cancel\').onclick=()=>{if(outputProfileBusy)return;$(\'#output-modal\').classList.add(\'hidden\');pendingMode=null};function loadGroups(data){const state=data.groups,select=$(\'#group-select\');select.replaceChildren(...Object.entries(state.profiles).map(([key,g])=>new Option(key+\' • \'+g.group,key,key===state.active,key===state.active)));const show=()=>{const key=select.value||state.active,g=state.profiles[key];if(!g)return;$(\'#group-key\').value=key;$(\'#group-name\').value=g.group;$(\'#group-user\').value=g.username;$(\'#group-server\').value=g.server;$(\'#group-required\').checked=!!g.passwordRequired};select.onchange=show;show()}$(\'#save-group\').onclick=async()=>{try{await busy($(\'#save-group\'),()=>post(\'/api/groups/save\',{key:$(\'#group-key\').value,group:$(\'#group-name\').value,username:$(\'#group-user\').value,server:$(\'#group-server\').value,passwordRequired:$(\'#group-required\').checked}),\'Saving group…\',null,false);await refreshAll();note(\'Group profile saved\')}catch(error){note(error.message,true)}};$(\'#open-pc\').onclick=async()=>{try{show(\'pc\');await refreshStatic()}catch(error){note(error.message,true)}};\n$(\'#away-display-toggle\').onclick=async()=>{try{await busy($(\'#away-display-toggle\'),()=>post(\'/api/away-display\'),\'Switching away/display…\',null,false);await refreshVolatile()}catch(error){note(error.message,true)}};\n$(\'#pc-back\').onclick=()=>show(\'profiles\');\n$(\'#all-songs\').onclick=async()=>{try{all=(await api(\'/api/songs\')).songs;show(\'songs\');render(\'#song-list\',all,\'\');}catch(error){note(error.message,true)}};\n$(\'#song-search\').oninput=()=>render(\'#song-list\',all,$(\'#song-search\').value);\n$(\'#playlist-search\').oninput=()=>render(\'#playlist-songs\',inside,$(\'#playlist-search\').value);\ndocument.querySelectorAll(\'[data-back]\').forEach(button=>button.onclick=()=>show(button.dataset.back));\ndocument.querySelectorAll(\'[data-cmd]\').forEach(button=>button.onclick=async()=>{try{await busy(button,()=>post(\'/api/player/command\',{command:button.dataset.cmd}),\'…\',null,false);await pollLocal()}catch(error){note(error.message,true)}});\n$(\'#repeat\').onclick=async()=>{try{const data=await api(\'/api/player\');await post(\'/api/player/repeat\',{mode:{off:\'all\',all:\'one\',one:\'off\'}[data.repeat]||\'off\'});await pollLocal()}catch(error){note(error.message,true)}};\n$(\'#shuffle\').onclick=async()=>{try{await busy($(\'#shuffle\'),()=>post(\'/api/player/shuffle\'),\'Updating saved shuffle…\',\'Saved shuffle updated for next play\',true)}catch(error){note(error.message,true)}};\nfor(const [id,command] of [[\'system-previous\',\'previous\'],[\'system-toggle\',\'toggle\'],[\'system-next\',\'next\']])$(\'#\'+id).onclick=async()=>{if(command!==\'toggle\'){const media=state.system;media.skipPending=true;media.skipFrom=media.trackKey;media.skipStarted=performance.now();media.skipWarned=false;resetSystemPosition()}else{state.system.skipPending=false}try{await busy($(\'#\'+id),()=>post(\'/api/system-media\',{command}),\'…\',null,false);await pollSystem(true)}catch(error){if(command!==\'toggle\')systemPositionError(\'System media skip failed: \'+(error?.message||String(error)));else note(error.message,true)}};\n$(\'#restart-camilla\').onclick=()=>busy($(\'#restart-camilla\'),()=>post(\'/api/restart-camilladsp\'),\'Restarting CamillaDSP…\',\'CamillaDSP restarted\',true).then(refreshStatic).catch(error=>note(error.message,true));\n$(\'#restart-sonobus\').onclick=()=>busy($(\'#restart-sonobus\'),()=>post(\'/api/restart-sonobus\'),\'Restarting SonoBus…\',\'SonoBus restarted\',true).then(refreshVolatile).catch(error=>note(error.message,true));\n$(\'#restart-airplay\').onclick=()=>busy($(\'#restart-airplay\'),()=>post(\'/api/restart-airplay\'),\'Restarting AirPlay…\',\'AirPlay restarted\',true).then(refreshVolatile).catch(error=>note(error.message,true));\n$(\'#restart-vnc\').onclick=()=>busy($(\'#restart-vnc\'),()=>post(\'/api/restart-vnc\'),\'Restarting VNC…\',\'VNC restarted\').catch(error=>note(error.message,true));\n$(\'#profile-search\').oninput=()=>{if(profileSnapshot)renderProfileSnapshot(profileSnapshot);else refreshStatic()};\nbindSeek(state.local,\'/api/player/seek\');bindSeek(state.system,\'/api/system-media/seek\');bindVolume(state.local,\'/api/player/volume\');bindVolume(state.system,\'/api/system-volume\');\nlet staticRefreshRunning=false,volatileRefreshRunning=false,profileSnapshot=null;\nfunction renderProfileSnapshot(data){\n  const query=$(\'#profile-search\').value.toLowerCase(),root=$(\'#profile-list\');\n  renderActiveProfile(data);root.replaceChildren();\n  const groups=new Map();\n  for(const name of data.profiles||[]){\n    const info=data.filters?.[name]||{group:\'Other\',label:name};\n    if(![name,info.group,info.label].some(value=>value.toLowerCase().includes(query)))continue;\n    if(!groups.has(info.group))groups.set(info.group,[]);\n    groups.get(info.group).push({name,label:info.label});\n  }\n  for(const [group,items] of groups){\n    const heading=document.createElement(\'div\');heading.className=\'section-title\';heading.textContent=group;root.append(heading);\n    const list=document.createElement(\'div\');list.className=\'list\';\n    for(const {name,label} of items){\n      const button=document.createElement(\'button\');\n      button.className=\'item\'+(name===data.active&&data.running?\' active\':\'\');\n      const title=document.createElement(\'span\');title.className=\'name\';title.textContent=label;\n      button.append(title);\n      button.onclick=async()=>{try{await busy(button,()=>post(\'/api/select\',{profile:name}),\'Switching profile…\',\'Profile activated\',true);await refreshStatic()}catch(error){note(error.message,true)}};\n      list.append(button);\n    }\n    root.append(list);\n  }\n}\nfunction playlistAction(playlist,shuffle){\n  const button=document.createElement(\'button\');button.type=\'button\';button.className=\'playlist-icon\';\n  button.setAttribute(\'aria-label\',(shuffle?\'Play saved shuffle of \':\'Play \')+playlist.name);\n  button.title=(shuffle?\'Play saved shuffle\':\'Play playlist\')+\' · \'+playlist.name;\n  button.innerHTML=shuffle\n    ? \'<svg class="media-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h3c5 0 7 10 12 10h3m-4-4 4 4-4 4M3 17h3c2 0 3-1 4-3m3-4c1-2 2-3 5-3h3m-4-4 4 4-4 4"/></svg>\'\n    : \'<svg class="media-icon" viewBox="0 0 24 24" aria-hidden="true"><path class="icon-fill" d="M8 5.5 19 12 8 18.5z"/></svg>\';\n  button.onclick=()=>busy(button,()=>post(\'/api/play/playlist\',{name:playlist.name,shuffle}),\'…\',null,false).then(refreshVolatile).catch(error=>note(error.message,true));\n  return button\n}\nfunction renderPlaylistSnapshot(playlists){\n  const list=$(\'#playlists\');list.replaceChildren();\n  for(const playlist of playlists){\n    const row=document.createElement(\'div\');row.className=\'playlist-row\';\n    const open=document.createElement(\'button\');open.className=\'item playlist-open\';open.dataset.playlistName=playlist.name;open.innerHTML=\'<span class="name"></span><span class="meta"></span>\';open.children[0].textContent=playlist.name;open.children[1].textContent=playlist.count+(playlist.count===1?\' song\':\' songs\');const wrap=document.createElement(\'div\');wrap.className=\'item-cover\';const copy=document.createElement(\'div\');copy.className=\'copy\';copy.append(...open.children);wrap.append(coverNode(playlist.cover),copy);open.append(wrap);\n    open.onclick=async()=>{current=playlist.name;inside=(await api(\'/api/playlist?name=\'+encodeURIComponent(playlist.name))).songs;$(\'#playlist-title\').textContent=playlist.name;show(\'playlist\');render(\'#playlist-songs\',inside,\'\')};\n    const controls=document.createElement(\'div\');controls.className=\'playlist-controls\';\n    const play=playlistAction(playlist,false),shuffle=playlistAction(playlist,true);\n    controls.append(play,shuffle);row.append(open,controls);list.append(row)\n  }\n}\nfunction updatePlaylistCover(player){if(!player?.playlist||!player.playing)return;for(const button of document.querySelectorAll(\'#playlists [data-playlist-name]\')){if(button.dataset.playlistName!==player.playlist)continue;const old=button.querySelector(\'.item-cover .cover-art\');if(!old)continue;const url=player.cover||\'\';if(url&&old.tagName===\'IMG\'&&old.getAttribute(\'src\')===url)return;if(!url&&old.classList.contains(\'cover-fallback\'))return;old.replaceWith(coverNode(url))}}\nfunction renderVolatile(data){\n  if(data.awayDisplay){const button=$(\'#away-display-toggle\');button.textContent=data.awayDisplay.active?\'Return to desktop\':\'Away and display off\';button.setAttribute(\'aria-pressed\',String(!!data.awayDisplay.active));button.classList.toggle(\'mode-active\',!!data.awayDisplay.active)}\n  if(data.profiles)renderActiveProfile(data.profiles);\n  renderSonobusStatus(data.mode,data.groups);\n  $(\'#engine-status\').textContent=data.mode.camilla?\'Audio engine online\':\'Audio engine offline\';$(\'.dot\').style.opacity=data.mode.camilla?\'1\':\'.25\';\n  $(\'#source-title\').textContent=({ipad_external:\'External\',laptop_external:\'Laptop\',ipad_ipad:\'External\',ipad_laptop:\'Laptop\',ipad_both:\'Both\',laptop_ipad:\'External\',laptop_laptop:\'Laptop\',laptop_both:\'Both\'})[data.mode.mode]||data.mode.label;$(\'#source-details\').textContent=\'CamillaDSP \'+(data.mode.camilla?\'running\':\'stopped\')+\' • SonoBus \'+(data.mode.sonobus?\'on\':\'off\')+\' • AirPlay \'+(data.mode.airplay?\'on\':\'off\')+(data.mode.localWanted?\' • Output \'+(data.mode.localOutputLabel||\'Automatic\'):\'\');\n  document.querySelectorAll(\'[data-mode]\').forEach(button=>button.classList.toggle(\'mode-active\',button.dataset.mode===data.mode.mode));\n  updatePlaylistCover(data.player);\n  const local=data.player,lm=state.local;$(\'#now-title\').textContent=local.title||\'Nothing playing\';updateLocalNowArtwork(local.cover||\'\');$(\'#now-details\').textContent=[local.artist,local.album].filter(Boolean).join(\' • \')||(local.path||\'\');renderTimeline(lm,local.currentTime,local.duration);if(lm.volumePending===null&&document.activeElement!==lm.volume){displayMasterVolume(local.volume)}$(\'#shuffle\').disabled=!local.canShuffleQueue;$(\'#repeat\').textContent=\'Repeat \'+({off:\'Off\',all:\'All\',one:\'1\'}[local.repeat]||\'Off\');$(\'#repeat\').classList.toggle(\'active\',local.repeat!==\'off\');setPlayIcon($(\'#local-play-shape\'),document.querySelector(\'[data-cmd="toggle"]\'),local.playing)\n}\nasync function refreshStatic(){if(staticRefreshRunning)return;staticRefreshRunning=true;try{const data=await api(\'/api/state\');profileSnapshot=data.profiles;renderProfileSnapshot(data.profiles);renderPlaylistSnapshot(data.playlists);loadGroups(data);renderVolatile(data);renderSystem(data.systemMedia);return data}catch(error){note(error.message,true)}finally{staticRefreshRunning=false}}\nasync function refreshOutputPicker(){if($(\'#output-modal\').classList.contains(\'hidden\')||!pendingMode||outputProfileBusy||outputProfile.disabled||performance.now()-lastOutputRefresh<5000)return;const cardValue=outputCard.value,profileValue=outputProfile.value,sinkValue=outputSink.value;try{const fresh=await api(\'/api/outputs\');lastOutputRefresh=performance.now();outputTopology=fresh;outputCard.replaceChildren(...fresh.cards.map(item=>new Option(item.label,item.name)));if(!fresh.cards.length){outputProfile.replaceChildren();outputSink.replaceChildren();return}outputCard.value=fresh.cards.some(item=>item.name===cardValue)?cardValue:(fresh.saved?.card||outputCard.value);fillProfiles(profileValue);if([...outputSink.options].some(option=>option.value===sinkValue&&!option.disabled))outputSink.value=sinkValue}catch(error){console.warn(\'output picker refresh failed\',error)}}\nasync function refreshVolatile(){if(volatileRefreshRunning||document.hidden)return;volatileRefreshRunning=true;try{renderVolatile(await api(\'/api/volatile\'));await refreshOutputPicker()}catch(error){console.warn(\'volatile refresh failed\',error)}finally{volatileRefreshRunning=false}}\nrefreshAll=async()=>refreshStatic();\nrefreshStatic();window.addEventListener(\'pageshow\',event=>{if(event.persisted){refreshStatic();refreshVolatile()}});document.addEventListener(\'visibilitychange\',()=>{if(!document.hidden){refreshStatic();refreshVolatile()}});setInterval(refreshVolatile,1000);setInterval(()=>{if(!document.hidden)pollSystem()},1000);setInterval(tickSystem,250);\n</script></body></html>'
class H(BaseHTTPRequestHandler):
    def data(self,n,t,b):self.send_response(n);self.send_header('Content-Type',t);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','private, max-age=3600' if n==200 and t=='image/jpeg' else 'no-store');self.end_headers();self.wfile.write(b)
    def out(self,n,d):self.data(n,'application/json; charset=utf-8',json.dumps(d,separators=(',',':')).encode())
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n<0 or n>65536:raise ValueError('Request body must be 64 KiB or less')
        d=json.loads(self.rfile.read(n)) if n else {}
        if not isinstance(d,dict):raise ValueError('Body must be object')
        return d
    def do_GET(self):
        u=urlparse(self.path);p=u.path;q=parse_qs(u.query)
        try:
            if p=='/':self.data(200,'text/html; charset=utf-8',PAGE.encode());return
            if p=='/api/system-cover':
                image=system_cover_bytes(q.get('v',[''])[0])
                if not image:self.data(404,'text/plain; charset=utf-8',b'No cover');return
                self.data(200,'image/jpeg',image);return
            if p=='/api/cover':
                try:track=song(q.get('path',[''])[0])
                except (ValueError,OSError):self.data(404,'text/plain; charset=utf-8',b'Not found');return
                image=cover_bytes(track)
                if not image:self.data(404,'text/plain; charset=utf-8',b'No cover');return
                self.data(200,'image/jpeg',image);return
            if p=='/api/state':r=full_state()
            elif p=='/api/volatile':r=volatile_state()
            elif p=='/api/profiles':r={'profiles':[x.name for x in profiles()],'filters':listening_filters(),'active':active(),'running':alive(rpid(CAMPID),'camilladsp')}
            elif p=='/api/mode':r=mode_state()
            elif p=='/api/away-display':r=away_display_state()
            elif p=='/api/outputs':r=output_state()
            elif p=='/api/system-media':r=system_state()
            elif p=='/api/player':r=player_state()
            elif p=='/api/playlists':r={'playlists':lists()}
            elif p=='/api/songs':r={'songs':song_objects(files())}
            elif p=='/api/playlist':n=q.get('name',[''])[0];r={'name':n,'songs':song_objects(files(pdir(n)))}
            else:self.out(404,{'ok':False,'error':'Not found'});return
            self.out(200,{'ok':True,**r})
        except Exception as e:self.out(500,{'ok':False,'error':str(e)})
    def do_POST(self):
        p=urlparse(self.path).path
        try:
            d=self.body()
            if p=='/api/away-display':r=toggle_away_display()
            elif p=='/api/mode':r=set_mode(d.get('mode'),d.get('password'),d.get('output'))
            elif p=='/api/output-step':r=select_output_step(d.get('mode'),d.get('card'),d.get('profile'),d.get('sink'),d.get('port'),d.get('password'))
            elif p=='/api/output-profile':
                with LOCK:r=set_output_profile(d.get('card'),d.get('profile'))
            elif p=='/api/groups/save':r=save_group(d)
            elif p=='/api/select':r=switch_profile(d.get('profile'))
            elif p=='/api/restart-camilladsp':r=restart_camilla()
            elif p=='/api/restart-sonobus':r=restart_sonobus(d.get('password'),MODE_POLICIES[audio_mode()])
            elif p=='/api/restart-airplay':airplay(True);r={'restarted':True}
            elif p=='/api/restart-vnc':r=restart_vnc()
            elif p=='/api/system-media':r=system_media(d.get('command'))
            elif p=='/api/system-media/seek':r=system_seek(d.get('seconds'))
            elif p=='/api/system-volume':r=set_system_volume(d.get('volume'))
            elif p=='/api/mode/pc':r={'player':'mpv','mode':mode_state()}
            elif p=='/api/mode/system':r=set_mode('laptop_external',d.get('password'))
            elif p=='/api/mode/airplay':r=set_mode('ipad_external',d.get('password'))
            elif p=='/api/player/command':
                c=d.get('command');cmd={'toggle':['cycle','pause'],'next':['playlist-next','force'],'previous':['playlist-prev','force']}.get(c)
                if not cmd:raise ValueError('Invalid command')
                mpv(cmd);save_mpv_queue(force=True);r={'command':c}
            elif p=='/api/player/shuffle':r=shuffle_current_playlist()
            elif p=='/api/player/seek':v=max(0,float(d.get('seconds')));mpv(['seek',v,'absolute+exact']);save_mpv_queue(force=True);r={'seconds':v}
            elif p=='/api/player/volume':r=set_master_volume(d.get('volume'))
            elif p=='/api/player/repeat':r={'mode':set_repeat(d.get('mode'))};save_mpv_queue(force=True)
            elif p=='/api/play/song':r=play_song(d.get('path'))
            elif p=='/api/play/playlist':r=play_list(d.get('name'),bool(d.get('shuffle')))
            else:self.out(404,{'ok':False,'error':'Not found'});return
            self.out(200,{'ok':True,**r})
        except (ValueError,TypeError,KeyError,FileNotFoundError,json.JSONDecodeError) as e:self.out(400,{'ok':False,'error':str(e)})
        except Exception as e:self.out(500,{'ok':False,'error':str(e)})
    def log_message(self,*a):pass
class S(ThreadingHTTPServer):allow_reuse_address=True;daemon_threads=True

def start_runtime():
    ensure()
    if STOPPED.exists():
        run(['systemctl','--user','stop',SYSTEM_AUDIO_SERVICE],False,10)
        run(['systemctl','stop','shairport-sync.service','nqptp.service'],False,10)
        return
    alsa100()
    # Keep persisted master until the route has been restored; a newly
    # created default sink may temporarily report 100%.
    available=profiles()
    if not available:raise RuntimeError('No CamillaDSP profiles found in '+str(PROFILES))
    saved=active();selected=None
    if saved:
        try:selected=profile(saved)
        except (ValueError,FileNotFoundError):pass
    if selected is None:
        selected=available[0]
    # Reuse a verified surviving engine; never start a competing instance.
    if not alive(rpid(CAMPID),'camilladsp'):
        stop_camilla(include_stale=True)
        start_camilla(selected)
    mode=audio_mode()
    try:
        apply_mode(mode,restore_camilla=False)
    except RuntimeError as error:
        if 'exposes no available physical sink' not in str(error):raise
        label,source,local,sono=MODES[mode]
        apply_source_services(source);stop_local_monitor()
        if sono:restart_sonobus(None,MODE_POLICIES[mode],normalize=False)
        else:stop_sonobus()
        MODE.write_text(mode+'\n')
        normalize_audio_volumes()
    except ValueError as error:
        # A saved password-protected group cannot be joined unattended. Keep
        # CamillaDSP and the web UI alive so the password can be entered there.
        if 'requires a password' not in str(error):raise
        label,source,local,sono=MODES[mode]
        apply_source_services(source)
        if local:start_local_monitor()
        else:stop_local_monitor()
        stop_sonobus();MODE.write_text(mode+'\n')
        normalize_audio_volumes()
    if MODES[audio_mode()][1]=='system' and QUEUE_FILE.is_file():
        try:ensure_mpv()
        except (OSError,RuntimeError):pass
def stop_audio_from_switch():
    with LOCK:
        STOPPED.touch()
        STOP_ACK.unlink(missing_ok=True)
        stop_local_monitor()
        stop_mpv()
        stop_sonobus()
        stop_camilla(include_stale=True)
        run(['systemctl','--user','stop',SYSTEM_AUDIO_SERVICE],False,10)
        run(['systemctl','stop','shairport-sync.service','nqptp.service'],False,10)
        STOP_ACK.touch()

def cleanup():
    # A terminal/server-process exit is a full audio shutdown. An audio-switch
    # owned engine is left to the switch's explicit handoff protocol.
    stop_local_monitor()
    stop_mpv()
    stop_sonobus()
    tracked=rpid(CAMPID)
    if CAMILLA_PROCESS is not None or (tracked and reusable_camilla(tracked)):
        stop_camilla(include_stale=True)
    run(['systemctl','--user','stop',SYSTEM_AUDIO_SERVICE],False,10)
    run(['systemctl','stop','shairport-sync.service','nqptp.service'],False,10)
def sonobus_sway_windows(tree):
    # The live Sway tree reports title="SonoBus", X11 class="SonoBus", app_id=null.
    stack=[(tree,None)]
    while stack:
        node,workspace=stack.pop()
        if not isinstance(node,dict):continue
        if node.get('type')=='workspace':workspace=node.get('name')
        for child in (node.get('nodes') or [])+(node.get('floating_nodes') or []):
            stack.append((child,workspace))
        if (node.get('type')!='con' or not isinstance(node.get('id'),int)
                or node.get('name')!='SonoBus'
                or (node.get('window_properties') or {}).get('class')!='SonoBus'):
            continue
        rect=node.get('rect') or {}
        if rect.get('width',0)>0 and rect.get('height',0)>0:
            yield node['id'],workspace

def watch_sonobus_workspace(stop):
    if not os.environ.get('SWAYSOCK'):return
    seen={}
    moved=set()
    while not stop.is_set():
        try:
            result=run([exe('swaymsg'),'-r','-t','get_tree'],False,4)
            if result.returncode:raise RuntimeError(result.stderr.strip() or 'Sway tree unavailable')
            windows=dict(sonobus_sway_windows(json.loads(result.stdout)))
            seen={wid:seen.get(wid,0)+1 for wid in windows}
            moved.intersection_update(windows)
            for wid,workspace in windows.items():
                if wid in moved or seen[wid]<2:continue
                if workspace=='1':moved.add(wid);continue
                reply=run([exe('swaymsg'),'-r',f'[con_id={wid}] move container to workspace number 1'],False,4)
                answers=json.loads(reply.stdout)
                if reply.returncode or not isinstance(answers,list) or not answers or not all(x.get('success') for x in answers):
                    raise RuntimeError(reply.stderr.strip() or f'Sway move failed: {answers}')
                moved.add(wid)
                print(f'Moved SonoBus window {wid} to workspace 1',flush=True)
        except (OSError,ValueError,TypeError,RuntimeError,subprocess.TimeoutExpired) as error:
            print(f'SonoBus workspace watcher: {error}',flush=True)
        stop.wait(.5)

def main():
    global LOCAL_ENGINE_OWNED
    me=os.getpid()
    SWITCH_STATE.mkdir(parents=True,exist_ok=True)
    startup_lock=(SWITCH_STATE/'audio-switch.lock').open('a+')
    fcntl.flock(startup_lock,fcntl.LOCK_EX)
    legacy_pid=HOME/'.local/state/sway/audio/camilladsp-webremote/web-server.pid'
    previous=rpid(legacy_pid)
    if previous and previous!=me and alive(previous):
        try:os.kill(previous,signal.SIGTERM)
        except OSError:pass
        deadline=time.monotonic()+10
        while alive(previous) and time.monotonic()<deadline:time.sleep(.05)
        if alive(previous):raise RuntimeError('Previous legacy webremote instance did not exit')
    ensure();old=rpid(SERVERPID)
    if old and old!=me and alive(old):
        try:os.kill(old,signal.SIGTERM)
        except OSError:pass
        deadline=time.monotonic()+10
        while alive(old) and time.monotonic()<deadline:time.sleep(.05)
        if alive(old):
            try:os.kill(old,signal.SIGKILL)
            except OSError:pass
            deadline=time.monotonic()+2
            while alive(old) and time.monotonic()<deadline:time.sleep(.05)
        if alive(old):raise RuntimeError('Previous webremote instance did not exit')
    SERVERPID.unlink(missing_ok=True)
    # First replacement of an older server may leave its tracked engine alive.
    tracked=rpid(CAMPID)
    if tracked and reusable_camilla(tracked):
        stop_local_monitor();stop_camilla(include_stale=True)
    # A running engine may have survived a previous server. Never restart it
    # just because the monitor PID is absent or stale.
    running=other_camilla_processes()
    tracked=rpid(CAMPID)
    if running:
        verified=reusable_camilla(running[0]) if len(running)==1 else False
        if verified and tracked!=running[0] and not alive(tracked,'camilladsp'):
            CAMPID.write_text(str(running[0])+'\n')
            ACTIVE.write_text(verified.name+'\n')
            tracked=running[0]
            print('Recovered existing CamillaDSP PID '+str(tracked),flush=True)
        if verified and tracked in running:
            try:recorded=ACTIVE.read_text().strip()
            except OSError:recorded=''
            if recorded!=verified.name:ACTIVE.write_text(verified.name+'\n')
        LOCAL_ENGINE_OWNED=tracked in running and alive(rpid(LOCALMONPID))
        if not LOCAL_ENGINE_OWNED and tracked in running and verified:
            # A previous server died after starting CamillaDSP but before its
            # monitor. Finish its route without starting another engine.
            start_runtime()
            LOCAL_ENGINE_OWNED=False
        elif not LOCAL_ENGINE_OWNED:
            print('Existing CamillaDSP is not owned by this pair; leaving it untouched: '+str(running),flush=True)
            LOCAL_ENGINE_OWNED=True
    else:
        LOCAL_ENGINE_OWNED=False
        ensure()
    watcher_stop=threading.Event()
    threading.Thread(target=watch_sonobus_workspace,args=(watcher_stop,),daemon=True).start()
    if not running:start_runtime()
    threading.Thread(target=watch_master_volume,args=(watcher_stop,),daemon=True).start()
    server=S(('0.0.0.0',PORT),H);SERVERPID.write_text(str(me)+'\n');STOP_CAP.write_text(str(me)+'\n')
    fcntl.flock(startup_lock,fcntl.LOCK_UN)
    startup_lock.close()
    def shut(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shut);signal.signal(signal.SIGHUP,shut)
    signal.signal(signal.SIGUSR1,lambda *_:threading.Thread(target=stop_audio_from_switch,daemon=True).start())
    print(f'CamillaDSP web remote listening on 0.0.0.0:{PORT}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        watcher_stop.set()
        server.server_close();cleanup()
        if rpid(SERVERPID)==me:SERVERPID.unlink(missing_ok=True)
        if rpid(STOP_CAP)==me:STOP_CAP.unlink(missing_ok=True)
def topology_cli():
    import sys
    try:
        if len(sys.argv)==2 and sys.argv[1]=='--toggle-away-display':
            print(json.dumps(toggle_away_display()));return
        if len(sys.argv)==2 and sys.argv[1]=='--away-display-status':
            print(json.dumps(away_display_state()));return
        if len(sys.argv)==2 and sys.argv[1]=='--dsp-profiles':
            print(json.dumps([item.name for item in profiles()],ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--dsp-filter-info':
            print(json.dumps(listening_filters(),ensure_ascii=False));return
        if len(sys.argv)==3 and sys.argv[1]=='--remember-output':
            route=json.loads(sys.argv[2]);
            if not isinstance(route,dict) or not all(isinstance(route.get(key),str) and route[key] for key in ('card','profile','sink')):raise ValueError('Invalid output route')
            remember_output(route);print(json.dumps(route));return
        if len(sys.argv)==3 and sys.argv[1]=='--remembered-profile':
            print(remembered_card_profile(sys.argv[2]));return
        if len(sys.argv)==4 and sys.argv[1]=='--remembered-sink':
            route=remembered_output(sys.argv[2],sys.argv[3])
            print(route.get('sink',''))
            return
        if len(sys.argv)==5 and sys.argv[1]=='--remembered-port':
            route=remembered_output(sys.argv[2],sys.argv[3])
            print(route.get('port','') if route.get('sink')==sys.argv[4] else '')
            return
        if len(sys.argv)==2 and sys.argv[1]=='--output-topology':
            print(json.dumps(audio_topology(),ensure_ascii=False));return
        if len(sys.argv)==4 and sys.argv[1]=='--output-profile':
            print(json.dumps(set_output_profile(sys.argv[2],sys.argv[3],normalize=False),ensure_ascii=False));return
        raise ValueError('Invalid topology command')
    except Exception as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
if __name__=='__main__':
    import sys
    if len(sys.argv)>1:topology_cli()
    else:main()
