#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote
import base64, fcntl, hashlib, json, math, os, random, re, select, shutil, signal, socket, struct, subprocess, threading, time, uuid
from collections import OrderedDict
from functools import lru_cache, wraps
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor

HOME=Path.home(); PROFILES=HOME/'Documents/prefs/audio-filters'; MUSIC=HOME/'Downloads/Music'
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
PAUSED_MEDIA=STATE/'media-paused-for-audio-stop.json'
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
MASTER_RESTORING=True

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
def run(a,check=True,timeout=30,env=None):
    if a and a[0]=='__wp_control__':
        try:
            result=wp_control(a[1:],timeout=timeout)
        except (RuntimeError,ValueError,OSError,subprocess.TimeoutExpired) as error:
            result=subprocess.CompletedProcess(a,1,'',str(error))
        if check and result.returncode:raise subprocess.CalledProcessError(result.returncode,a,result.stdout,result.stderr)
        return result
    return subprocess.run(a,text=True,capture_output=True,check=check,timeout=timeout,env=env)

def pw_graph():
    result=subprocess.run([exe('pw-dump')],text=True,capture_output=True,timeout=8,env=media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'PipeWire graph unavailable')
    data=json.loads(result.stdout)
    if not isinstance(data,list):raise RuntimeError('Invalid PipeWire graph')
    return data

def pw_objects(kind,graph=None):
    graph=pw_graph() if graph is None else graph
    return [item for item in graph if item.get('type')=='PipeWire:Interface:'+kind]

def pw_props(item):return ((item or {}).get('info') or {}).get('props') or {}

def pw_sink(name,graph=None):
    nodes=pw_objects('Node',graph)
    found=next((x for x in nodes if pw_props(x).get('node.name')==name and pw_props(x).get('media.class')=='Audio/Sink'),None)
    if found is None:raise RuntimeError('Playback sink unavailable: '+str(name))
    return found

def pw_default(graph=None):
    graph=pw_graph() if graph is None else graph
    metadata=next((x for x in pw_objects('Metadata',graph) if pw_props(x).get('metadata.name')=='default'),None)
    if metadata:
        for row in (metadata.get('info') or {}).get('metadata') or []:
            if row.get('key') in ('default.configured.audio.sink','default.audio.sink'):
                try:
                    name=json.loads(row.get('value') or '{}').get('name')
                    if name and any(pw_props(x).get('node.name')==name for x in pw_objects('Node',graph)):return name
                except (ValueError,TypeError):pass
    inspect=subprocess.run([exe('wpctl'),'inspect','@DEFAULT_SINK@'],text=True,capture_output=True,timeout=5,env=media_env())
    if inspect.returncode==0:
        match=re.search(r'(?m)^\s*node\.name\s*=\s*\"?([^\"\n]+)',inspect.stdout)
        if match:
            name=match.group(1).strip()
            if any(pw_props(x).get('node.name')==name for x in pw_objects('Node',graph)):return name
    status=subprocess.run([exe('wpctl'),'status','-n'],text=True,capture_output=True,timeout=5,env=media_env())
    if status.returncode==0:
        match=re.search(r'(?m)^\s*\*\s+\d+\.\s+([^\n]+)',status.stdout)
        if match:
            name=match.group(1).strip().split(' [vol:')[0].strip()
            if any(pw_props(x).get('node.name')==name for x in pw_objects('Node',graph)):return name
    sinks=[x for x in pw_objects('Node',graph) if pw_props(x).get('media.class')=='Audio/Sink']
    if not sinks:raise RuntimeError('No playback sink in PipeWire graph')
    saved=saved_output_route().get('sink')
    for candidate in (('camilladsp' if selected_filter()!=NO_FILTER else ''),saved):
        if candidate and any(pw_props(x).get('node.name')==candidate for x in sinks):return candidate
    if len(sinks)==1:return str(pw_props(sinks[0]).get('node.name'))
    raise RuntimeError('Could not identify the current default sink')

def pw_move_streams(sink):
    target=pw_sink(sink)
    graph=pw_graph()
    for item in pw_objects('Node',graph):
        props=pw_props(item)
        if props.get('media.class')!='Stream/Output/Audio':continue
        if str(props.get('application.name') or '').lower() in ('sonobus','pacat'):continue
        command=[exe('pw-metadata'),'-n','default',str(item['id']),'target.node',str(target['id']),'Spa:Id']
        result=subprocess.run(command,text=True,capture_output=True,timeout=8,env=media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not move playback stream')
        # WirePlumber may reconnect asynchronously; verify only streams that
        # remain present, since clients may exit during an output switch.
        initial_links=[link for link in pw_objects('Link',graph)
                       if str((link.get('info') or {}).get('output-node-id'))==str(item['id'])]
        if not initial_links:
            # A paused or idle stream may have no links until playback resumes.
            # The target metadata is set, but a link cannot be verified yet.
            continue
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            current=pw_graph()
            stream=next((x for x in pw_objects('Node',current) if x['id']==item['id']),None)
            if stream is None:break
            destination=next((x for x in pw_objects('Node',current)
                              if pw_props(x).get('node.name')==sink),None)
            if destination is not None and any(
                str((link.get('info') or {}).get('output-node-id'))==str(stream['id'])
                and str((link.get('info') or {}).get('input-node-id'))==str(destination['id'])
                for link in pw_objects('Link',current)):
                break
            time.sleep(.1)
        else:raise RuntimeError('Playback stream did not reconnect to '+sink)

def wp_control(args,timeout=10):
    action=args[0] if args else ''
    def invoke(*parts):
        return subprocess.run([exe('wpctl'),*map(str,parts)],text=True,capture_output=True,timeout=timeout,env=media_env())
    if action=='info':return invoke('status')
    if action=='get-default-sink':return subprocess.CompletedProcess(args,0,pw_default()+'\n','')
    if action=='set-default-sink':
        node=pw_sink(args[1]);result=invoke('set-default',node['id'])
        if result.returncode==0:pw_move_streams(args[1])
        return result
    if action=='move-sink-input':
        graph=pw_graph();node=next((x for x in pw_objects('Node',graph) if str(x['id'])==str(args[1])),None)
        if node is None:return subprocess.CompletedProcess(args,0,'','')
        target=pw_sink(args[2],graph)
        return subprocess.run([exe('pw-metadata'),'-n','default',str(node['id']),'target.node',str(target['id']),'Spa:Id'],text=True,capture_output=True,timeout=timeout,env=media_env())
    if action=='get-sink-volume':
        target=args[1];node=pw_sink(pw_default() if target=='@DEFAULT_SINK@' else target)
        result=invoke('get-volume',node['id'])
        if result.returncode:return result
        match=re.search(r'Volume:\s*([0-9.]+)',result.stdout)
        if not match:raise RuntimeError('Cannot parse wpctl volume')
        return subprocess.CompletedProcess(args,0,f'Volume: {float(match.group(1))*100:.2f}%\n','')
    if action in ('set-sink-volume','set-source-volume','set-sink-input-volume','set-source-output-volume'):
        kind=action.split('-')[1];target=args[1]
        if target=='@DEFAULT_SINK@':target=pw_default()
        if str(target).isdigit():ident=target
        else:
            graph=pw_graph();klass='Audio/Sink' if kind=='sink' else 'Audio/Source'
            node=next((x for x in pw_objects('Node',graph) if pw_props(x).get('node.name')==target and pw_props(x).get('media.class')==klass),None)
            if node is None:raise RuntimeError('Audio node unavailable: '+str(target))
            ident=str(node['id'])
        gain='1.0' if args[2]=='0dB' else args[2]
        return invoke('set-volume',ident,gain)
    if action=='set-card-profile':
        raise RuntimeError('Legacy card-profile command disabled; select a live sink instead')
    if action=='set-sink-port':
        raise RuntimeError('Legacy sink-port command disabled; select a live sink instead')
    raise RuntimeError('Unsupported PipeWire control: '+action)

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
            if source==legacy and item.name not in ('master-volume','media-control.log','audio-switch.lock','audio-cards-last.txt','camilladsp-local.log'):continue
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
def apply_source_services(source,direct=False):
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
        if not direct and run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode:
            user_service('start',SYSTEM_AUDIO_SERVICE)
    else:raise ValueError('Invalid audio source')


def profiles():
    def load():
        found=[]
        if not PROFILES.is_dir():return found
        for pattern in ('*.yml','*.yaml'):
            for item in PROFILES.rglob(pattern):
                if not item.is_file():continue
                if item.parent==PROFILES or re.match(r'^\(\d+ms\)',item.parent.name):found.append(item)
        return sorted(found,key=lambda item:(item.name.casefold(),str(item).casefold()))
    return cached('profiles',5.0,load)
BRIR_DIR=PROFILES
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
    result={NO_FILTER:{'group':'Other','label':'No filter (bypass CamillaDSP)'}}
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
        label=item.parent.name if item.parent!=PROFILES else (matches[0] if len(matches)==1 else stem)
        result[item.name]={'group':group,'label':label}
    return result
def profile(name):
    if not isinstance(name,str) or Path(name).name!=name:raise ValueError('Invalid profile')
    matches=[item.resolve() for item in profiles() if item.name==name]
    if len(matches)!=1:raise FileNotFoundError('Missing or ambiguous profile: '+name)
    return matches[0]
def selected_filter():
    try:return ACTIVE.read_text().strip()
    except OSError:return ''

def stop_bypass():
    pid=rpid(BYPASSPID)
    if alive(pid):
        try:
            args=Path(f'/proc/{pid}/cmdline').read_bytes()
            if b'camilladsp_input' in args and b'camilladsp_output_shared' not in args:
                os.killpg(pid,signal.SIGTERM)
                deadline=time.monotonic()+2
                while alive(pid) and time.monotonic()<deadline:time.sleep(.05)
                if alive(pid):os.killpg(pid,signal.SIGKILL)
        except OSError:pass
    BYPASSPID.unlink(missing_ok=True)

def select_desktop_sink(name):
    target=pw_sink(name)
    result=run([exe('wpctl'),'set-default',str(target['id'])],False,10,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not select '+name)
    pw_move_streams(name)

def direct_no_filter(route):
    sink=route.get('sink') if isinstance(route,dict) else None
    if not sink or sink=='camilladsp':raise RuntimeError('Select a physical output for No filter')
    select_desktop_sink(sink)
    return route

def restore_dsp_desktop_sink():
    if run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode:
        user_service('start',SYSTEM_AUDIO_SERVICE)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:pw_sink('camilladsp');break
        except RuntimeError:time.sleep(.1)
    else:raise RuntimeError('CamillaDSP desktop sink did not appear')
    select_desktop_sink('camilladsp')

def restore_selected_local_output():
    route=choose_output_route(dict(saved_output_route(),_remembered=True))
    start_local_monitor(route,resolved=True)
    return route

def start_bypass():
    if alive(rpid(BYPASSPID)):
        args=Path(f'/proc/{rpid(BYPASSPID)}/cmdline').read_bytes()
        if b'camilladsp_input' in args:return
        raise RuntimeError('No-filter bridge PID belongs to another process')
    BYPASSPID.unlink(missing_ok=True)
    # The existing AirPlay, desktop and MPV inputs write camilladsp_input.
    # Its paired capture endpoint feeds the same output loopback consumed by
    # SonoBus and the physical-output monitor. No CamillaDSP or YAML is used.
    command='set -o pipefail; "$1" -q -D hw:Loopback,1,0 -r 96000 -f S32_LE -c 2 -t raw | "$2" -q -D hw:Loopback,0,1 -r 96000 -f S32_LE -c 2 -t raw'
    log=(STATE/'no-filter-bridge.log').open('ab',buffering=0)
    try:proc=subprocess.Popen([exe('bash'),'-c',command,'camilladsp_input',exe('arecord'),exe('aplay')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    finally:log.close()
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        if proc.poll() is not None:
            raise RuntimeError('No-filter bridge exited: '+log_tail(STATE/'no-filter-bridge.log')[-1500:])
        time.sleep(.05)
    BYPASSPID.write_text(str(proc.pid)+'\n')

def active():
    if selected_filter()==NO_FILTER:return NO_FILTER
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
        if PROFILES.resolve() not in selected.parents or not selected.is_file():return False
        return selected
    except (OSError,ValueError,RuntimeError,socket.timeout):return False

def start_camilla(p):
    global CAMILLA_PROCESS
    target=profile(p.name if isinstance(p,Path) else str(p))
    validate_camilla_profile(target)
    stop_bypass()
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
def wait_for_camilla_config(expected,timeout=10):
    """Wait for the tracked engine to report exactly the selected YAML."""
    expected=Path(expected).resolve()
    deadline=time.monotonic()+timeout
    last_error='WebSocket not listening'
    while time.monotonic()<deadline:
        pid=rpid(CAMPID)
        if not alive(pid,'camilladsp'):
            raise RuntimeError('CamillaDSP exited before readiness: '+log_tail(STATE/'camilladsp.log')[-2000:])
        try:
            loaded=camilla_command('GetConfigFilePath')
            if isinstance(loaded,str) and loaded and Path(loaded).resolve()==expected:
                return
            last_error=f'loaded {loaded!r}, expected {str(expected)!r}'
        except (OSError,RuntimeError,ValueError) as error:
            last_error=str(error)
        time.sleep(.1)
    raise RuntimeError('CamillaDSP selected profile not ready: '+last_error+'; '+log_tail(STATE/'camilladsp.log')[-2000:])

@contextmanager
def media_change(resume=True):
    if os.environ.get('AUDIO_MEDIA_TRANSACTION')=='1':
        yield
        return
    if getattr(MEDIA_TRANSACTION,'active',False):
        yield
        return
    # A stale stop snapshot must never suppress Pause while audio is running.
    already_stopped=STOPPED.exists()
    snapshot={'mpv':None,'mpris':[],'external':None} if already_stopped else pause_for_normalization()
    MEDIA_TRANSACTION.active=True
    completed=False
    try:
        yield
        # The transaction owns its final gain pass. Nothing can resume until
        # every route/service operation and master restoration has completed.
        if not STOPPED.exists():normalize_audio_volumes()
        completed=True
    finally:
        MEDIA_TRANSACTION.active=False
        if resume and completed and not already_stopped:
            # Last step, after the action and its normalization succeeded.
            # A failed action keeps its paused playback rather than resuming
            # onto a potentially incomplete output graph.
            resume_after_normalization(snapshot)

def media_transaction(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        with media_change():return function(*args,**kwargs)
    return wrapped

@media_transaction
def restart_vnc():
    user_service('restart','camilladsp-wayvnc.service')
    if not STOPPED.exists():normalize_with_media()
    return {'restarted':True}

@media_transaction
def restart_airplay():
    if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before restarting AirPlay')
    airplay(True)
    if not STOPPED.exists():normalize_with_media()
    return {'restarted':True}

@media_transaction
def switch_profile(name):
    with LOCK:
        if name==NO_FILTER:
            if STOPPED.exists():
                ACTIVE.write_text(NO_FILTER+'\n')
                return {'profile':NO_FILTER,'pid':None,'mode':mode_state()}
            local=MODES[audio_mode()][2]
            route=choose_output_route(dict(saved_output_route(),_remembered=True)) if local else None
            stop_local_monitor()
            stop_camilla(include_stale=True)
            try:
                start_bypass()
                if audio_mode()=='laptop_laptop':
                    user_service('stop',SYSTEM_AUDIO_SERVICE)
                    direct_no_filter(route)
                elif MODES[audio_mode()][1]=='system':
                    restore_dsp_desktop_sink()
                if local:start_local_monitor(route,resolved=True)
                ACTIVE.write_text(NO_FILTER+'\n')
                normalize_with_media()
            except Exception:
                # Do not report No filter if its bridge/monitor never came up.
                stop_local_monitor()
                stop_bypass()
                raise
            return {'profile':NO_FILTER,'pid':None,'mode':mode_state()}
        target=profile(name)
        validate_camilla_profile(target)
        pid=rpid(CAMPID)
        was_bypass=selected_filter()==NO_FILTER
        if alive(rpid(BYPASSPID)):
            stop_local_monitor();stop_bypass()
        if was_bypass and not STOPPED.exists():
            stop_local_monitor()
            started=start_camilla(target)
            if MODES[audio_mode()][1]=='system':restore_dsp_desktop_sink()
            if MODES[audio_mode()][2]:restore_selected_local_output()
            ACTIVE.write_text(target.name+'\n')
            normalize_with_media()
            return {'profile':target.name,'pid':started,'mode':mode_state()}
        if not alive(pid,'camilladsp'):
            # Selecting a filter while stopped must not start or reroute audio.
            ACTIVE.write_text(target.name+'\n')
            invalidate_cache('profiles')
            return {'profile':target.name,'pid':None}
        old_path=camilla_command('GetConfigFilePath')
        if not isinstance(old_path,str) or not old_path:
            raise RuntimeError('CamillaDSP did not report its active config path')
        if Path(old_path).resolve()!=target.resolve():
            camilla_command({'SetConfigFilePath':str(target)})
            try:
                camilla_command('Reload')
                if Path(camilla_command('GetConfigFilePath')).resolve()!=target.resolve():
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
        if MODES[audio_mode()][1]=='system':restore_dsp_desktop_sink()
        if MODES[audio_mode()][2]:restore_selected_local_output()
        normalize_with_media()
        return {'profile':target.name,'pid':pid,'mode':mode_state()}
@media_transaction
def restart_camilla():
    with LOCK:
        if selected_filter()==NO_FILTER:
            normalize_with_media()
            return {'profile':NO_FILTER,'pid':None,'restarted':False}
        if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before restarting CamillaDSP')
        name=active()
        if not name:raise RuntimeError('No active profile')
        target=profile(name)
        route=saved_output_route()
        local=alive(rpid(LOCALMONPID))
        stop_local_monitor()
        stop_camilla(include_stale=True)
        pid=start_camilla(target)
        if MODES[audio_mode()][1]=='system':restore_dsp_desktop_sink()
        if MODES[audio_mode()][2]:restore_selected_local_output()
        normalize_with_media()
        return {'profile':name,'pid':pid,'restarted':True}

def alsa100():
    r=run(['amixer','-c','Loopback','sset','PCM','0dB'],False)
    if r.returncode:raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'Could not set Loopback PCM')

# This is the single saved master shared by the server and Audio Control.
def master_sink():
    if selected_filter()==NO_FILTER and audio_mode()=='laptop_laptop':
        name=saved_output_route().get('sink')
        if name and any(row.get('name')==name for row in _pipewire_json('sinks')):return name
    if selected_filter()!=NO_FILTER:
        try:pw_sink('camilladsp');return 'camilladsp'
        except RuntimeError:pass
    saved=saved_output_route().get('sink')
    if saved:
        try:pw_sink(saved);return saved
        except RuntimeError:pass
    return '@DEFAULT_SINK@'

def master_volume():
    # A recreated sink starts at 100%. Until route restoration completes,
    # only the persisted master is authoritative.
    if MASTER_RESTORING or STOPPED.exists() or (STATE/'output-preview').exists():
        try:return max(0.0,min(100.0,float(MASTER_VOLUME.read_text().strip().rstrip('%'))))
        except (OSError,ValueError):return 50.0
    # The Sway volume keys and both web sliders share the DEFAULT PipeWire sink.
    # Read the live value so external pipewire key presses appear in both sliders.
    try:
        result=run(['__wp_control__','get-sink-volume',master_sink()],False,5,media_env())
        match=re.search(r'(\d+(?:\.\d+)?)%',result.stdout)
        if result.returncode==0 and match:
            value=max(0.0,min(100.0,float(match.group(1))))
            MASTER_VOLUME.parent.mkdir(parents=True,exist_ok=True)
            with LOCK:
                try:saved=float(MASTER_VOLUME.read_text().strip().rstrip('%'))
                except (OSError,ValueError):saved=None
                # Recheck under the lock: a poll may have read 100% just
                # before stop/restart acquired it.
                if MASTER_RESTORING or STOPPED.exists() or (STATE/'output-preview').exists():
                    return max(0.0,min(100.0,saved)) if saved is not None else 50.0
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
    try:mpv_direct(['set_property','volume',100.0 if selected_filter()==NO_FILTER and not STOPPED.exists() else float(value)])
    except (OSError,RuntimeError,ValueError) as error:
        raise RuntimeError(f'Could not sync MPV to saved master: {error}') from error

def watch_master_volume(stop):
    # Sway volume keys use pipewire directly, even when the web page is closed.
    while not stop.wait(.6):
        # The terminal switch holds this marker while a profile can recreate
        # the default sink at 100%. Do not overwrite the pre-switch master.
        owner=SWITCH_STATE/'media-control-owner'
        try:
            switch_pid=int(owner.read_text().split()[0])
        except (OSError,ValueError,IndexError):switch_pid=None
        if alive(switch_pid) or MASTER_RESTORING:continue
        try:master_volume()
        except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired):pass

def apply_master_volume(value,normalize_sinks=True):
    # Normalize other sinks on route changes, not on each slider update.
    # CamillaDSP Main remains at unity.
    name=master_sink()
    if name=='@DEFAULT_SINK@':
        name=pw_default()
    if not name:raise RuntimeError('No playback sink for master volume')
    if normalize_sinks:
        for sink in _pipewire_json('sinks'):
            target=str(sink.get('name') or '') if isinstance(sink,dict) else ''
            if not target or target==name:continue
            result=run(['__wp_control__','set-sink-volume',target,'100%'],False,8,media_env())
            if result.returncode:raise RuntimeError(f'Could not set {target} to 100%: {result.stderr.strip()}')
    result=run(['__wp_control__','set-sink-volume',name,f'{value:.2f}%'],False,8,media_env())
    if result.returncode:raise RuntimeError(f'Could not set {name} to {value:.2f}%: {result.stderr.strip()}')
    sync_mpv_master(value)
    if alive(rpid(CAMPID),'camilladsp'):
        if selected_filter()!=NO_FILTER:wait_for_camilla_config(profile(selected_filter()))
        try:camilla_command({'SetVolume':0.0})
        except (OSError,ValueError) as error:raise RuntimeError(f'Could not set CamillaDSP Main to unity: {error}') from error

def pause_for_normalization():
    snapshot={'mpv':None,'mpris':[],'external':None}
    # Only resume players that were actually playing and accepted Pause.
    if alive(rpid(MPVPID),'mpv') and MPVSOCK.exists():
        try:
            if mpv_direct(['get_property','pause']) is False:
                mpv_direct(['set_property','pause',True])
                if mpv_direct(['get_property','pause']) is True:
                    snapshot['mpv']=rpid(MPVPID)
        except (OSError,ValueError,RuntimeError):pass
    external=Path('/tmp/mpvsocket')
    external_owner=None
    if external.is_socket() and external!=MPVSOCK:
        try:external_owner=external.stat().st_ino
        except OSError:pass
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(1);client.connect(str(external))
                client.sendall(b'{"command":["get_property","pause"]}\n')
                reply=json.loads(client.makefile('rb').readline())
            if reply.get('error')=='success' and reply.get('data') is False:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(1);client.connect(str(external))
                    client.sendall(b'{"command":["set_property","pause",true]}\n')
                    if json.loads(client.makefile('rb').readline()).get('error')=='success':
                        snapshot['external']={'socket':str(external),'inode':external_owner} if external_owner else None
        except (OSError,ValueError,RuntimeError):pass
    try:players=mpris_players()
    except (OSError,RuntimeError,subprocess.TimeoutExpired):players=[]
    for player in players:
        try:
            if mpris_status(player)!='Playing':continue
            result=run([exe('playerctl'),'--player',player,'pause'],False,5,media_env())
            if result.returncode==0:
                # MPRIS Pause is a request. PlaybackStatus can lag the reply;
                # never reject a route change because an immediate read still
                # says Playing. Resume checks Paused at the very end.
                snapshot['mpris'].append(player)
        except (OSError,RuntimeError,subprocess.TimeoutExpired):
            # An unsupported or vanished player must not abort a working route.
            continue
    return snapshot
def resume_after_normalization(snapshot):
    # Resume only players paused by this normalization transaction.
    if snapshot['mpv'] and rpid(MPVPID)==snapshot['mpv'] and alive(snapshot['mpv'],'mpv'):
        try:
            if mpv_direct(['get_property','pause']) is True:
                mpv_direct(['set_property','pause',False])
        except (OSError,RuntimeError,ValueError):pass
    external=snapshot.get('external')
    if isinstance(external,dict) and isinstance(external.get('inode'),int):
        try:
            if Path(external['socket']).stat().st_ino==external['inode']:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(1);client.connect(external['socket'])
                    client.sendall(b'{"command":["get_property","pause"]}\n')
                    reply=json.loads(client.makefile('rb').readline())
                if reply.get('error')=='success' and reply.get('data') is True:
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                        client.settimeout(1);client.connect(external['socket'])
                        client.sendall(b'{"command":["set_property","pause",false]}\n')
                        json.loads(client.makefile('rb').readline())
        except (OSError,ValueError,RuntimeError):pass
    for player in snapshot['mpris']:
        try:
            if mpris_status(player)=='Paused':
                run([exe('playerctl'),'--player',player,'play'],False,5,media_env())
        except (OSError,RuntimeError,subprocess.TimeoutExpired):pass
def pause_for_audio_stop():
    ensure()
    if not PAUSED_MEDIA.exists():
        snapshot=pause_for_normalization()
        # Preserve playback intent in the pause snapshot; queue saving is
        # deliberately after Pause so it cannot race with route teardown.
        save_mpv_queue(force=True)
        if snapshot.get('mpv'):
            queue=_read_json(QUEUE_FILE,{})
            if isinstance(queue,dict) and queue.get('files'):
                queue['paused']=False
                _write_json(QUEUE_FILE,queue)
        _write_json(PAUSED_MEDIA,snapshot)

@contextmanager
def audio_start_change():
    # Start Audio is intentionally pause-only: establish services and routes,
    # normalize after they are ready, then leave all playback paused.
    pause_for_audio_stop()
    MEDIA_TRANSACTION.starting=True
    try:
        yield
        if not STOPPED.exists():
            normalize_audio_volumes()
            PAUSED_MEDIA.unlink(missing_ok=True)
    except Exception:
        # Preserve the snapshot for a later successful start attempt.
        raise
    finally:
        MEDIA_TRANSACTION.starting=False

def normalize_with_media():
    # An existing transaction keeps its one snapshot. Standalone normalize
    # owns pause -> gains -> saved master -> resume in the same server process.
    with LOCK:
        if getattr(MEDIA_TRANSACTION,'active',False) or getattr(MEDIA_TRANSACTION,'starting',False) or PAUSED_MEDIA.exists():
            # The owning output/filter/start transaction runs the final pass.
            return {'deferred':'normalization belongs to active transaction'}
        with media_change():
            # media_change runs normalization before its final resume.
            return {'normalized':True}

def normalize_audio_volumes():
    # Normalize live gain stages to 0 dB, then restore the saved master.
    # Media pausing is handled by normalize_with_media, not this gain routine.
    errors=[]
    try:cards=Path('/proc/asound/cards').read_text()
    except OSError as error:raise RuntimeError('Could not enumerate ALSA cards: '+str(error)) from error
    for card in re.findall(r'^\s*(\d+)\s+\[',cards,re.M):
        listing=run([exe('amixer'),'-c',card,'scontrols'],False,8)
        if listing.returncode:
            errors.append(f'ALSA card {card}: {listing.stderr.strip() or "could not list controls"}')
            continue
        for name,index in re.findall(r"^Simple mixer control '([^']+)',(\d+)$",listing.stdout,re.M):
            control=f'{name},{index}'
            info=run([exe('amixer'),'-c',card,'sget',control],False,8)
            if info.returncode:
                errors.append(f'ALSA card {card} {control}: {info.stderr.strip() or "could not inspect control"}')
                continue
            if not re.search(r'[-+]?\d+(?:\.\d+)?dB',info.stdout):continue
            for direction in ('playback','capture'):
                if not re.search(rf'^{direction} channels:',info.stdout,re.I|re.M):continue
                result=run([exe('amixer'),'-c',card,'sset',control,direction,'0dB'],False,8)
                if result.returncode:
                    errors.append(f'ALSA card {card} {control} {direction}: {result.stderr.strip() or "0dB unavailable"}')
                    continue
                check=run([exe('amixer'),'-c',card,'sget',control],False,8)
                if check.returncode:
                    errors.append(f'ALSA card {card} {control} {direction}: could not verify gain')
                    continue
                readings=[db for line in check.stdout.splitlines() if direction in line.lower() for db in re.findall(r'\[([-+]?\d+(?:\.\d+)?)dB\]',line)]
                if readings and any(abs(float(db))>0.11 for db in readings):
                    errors.append(f'ALSA card {card} {control} {direction}: gain not at 0dB ({", ".join(readings)})')
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
            for item in _pipewire_json(kind):
                if not isinstance(item,dict):continue
                target=item.get(key)
                if target is None or target=='':continue
                result=run(['__wp_control__',command,str(target),'0dB'],False,8,media_env())
                if result.returncode:
                    errors.append(f'{kind} {target}: {result.stderr.strip() or "0dB unavailable"}')
                    continue
        # Direct-ALSA MPV bypasses PipeWire; its gain is the same saved master.
        sync_mpv_master(max(0.0,min(100.0,value)))
        if alive(rpid(CAMPID),'camilladsp'):
            if selected_filter()!=NO_FILTER:wait_for_camilla_config(profile(selected_filter()))
            camilla_command({'SetVolume':0.0})
        target=master_sink()
        if target=='@DEFAULT_SINK@':target=pw_default()
        result=run(['__wp_control__','set-sink-volume',target,f'{max(0.0,min(100.0,value)):.2f}%'],False,8,media_env())
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
    if not PAUSED_MEDIA.exists():save_mpv_queue(force=True)
    killpidfile(MPVPID,'mpv');MPVSOCK.unlink(missing_ok=True)
def ensure_mpv():
    if alive(rpid(MPVPID),'mpv') and MPVSOCK.exists():return
    stop_mpv();log=MPVLOG.open('ab',buffering=0)
    cmd=[exe('mpv'),'--idle=yes','--no-video','--no-terminal','--keep-open=no','--ao=alsa','--audio-device=alsa/camilladsp_input','--audio-samplerate=96000','--audio-channels=stereo','--audio-format=s32',f'--input-ipc-server={MPVSOCK}',f'--volume={(100.0 if selected_filter()==NO_FILTER and not STOPPED.exists() else master_volume()):.2f}','--volume-max=100']
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
    if r.returncode != 0:
        # No MPRIS players (or a transient D-Bus error) is not an audio
        # routing failure. Players controlled by MPV IPC were handled above.
        return []
    # Include external MPV MPRIS instances. The server-owned MPV is already
    # paused above, so its MPRIS status will no longer be Playing.
    return [p for x in r.stdout.splitlines() if (p:=x.strip()) and p!='playerctld']
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
def restart_sonobus(password=None,policy=None,normalize=False):
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
        if normalize and not STOPPED.exists():normalize_with_media()
        return {'pid':process.pid,'profile':state['active'],'group':item['group'],**applied}

@media_transaction
def restart_sonobus_action(password=None):
    if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before restarting SonoBus')
    return restart_sonobus(password,MODE_POLICIES[audio_mode()],normalize=True)

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
def _pipewire_json(kind):
    graph=pw_graph();nodes=pw_objects('Node',graph);devices={str(x['id']):x for x in pw_objects('Device',graph)}
    if kind=='cards':
        return [{'index':x['id'],'name':pw_props(x).get('device.name',''),
                 'properties':pw_props(x),'profiles':[{'name':'current','description':'Current live profile','sinks':1,'available':'yes'}],
                 'active_profile':{'name':'current'}} for x in devices.values() if pw_props(x).get('device.name','').startswith('alsa_card.')]
    if kind in ('sinks','sources'):
        klass='Audio/Sink' if kind=='sinks' else 'Audio/Source'
        return [{'index':x['id'],'name':pw_props(x).get('node.name',''),
                 'description':pw_props(x).get('node.description') or pw_props(x).get('node.nick') or pw_props(x).get('node.name',''),
                 'card':pw_props(x).get('device.id'),'properties':pw_props(x),
                 'ports':[{'name':'','description':'Current live route','available':'yes'}],
                 'active_port':{'name':''}} for x in nodes if pw_props(x).get('media.class')==klass]
    klass='Stream/Output/Audio' if kind=='sink-inputs' else 'Stream/Input/Audio'
    return [{'index':x['id'],'properties':pw_props(x),'sink':None} for x in nodes if pw_props(x).get('media.class')==klass]

def saved_output_route():
    data=_read_json(LOCALSINK,{})
    if not isinstance(data,dict):data={}
    return {key:str(data.get(key) or '') for key in ('card','profile','sink','port')}

def _physical_devices(graph):
    return {str(item['id']):item for item in pw_objects('Device',graph)
            if pw_props(item).get('media.class')=='Audio/Device'
            and str(pw_props(item).get('device.name','')).startswith('alsa_card.')
            and str(pw_props(item).get('alsa.card_name','')).lower()!='loopback'}

def _device_options(item):
    params=(item.get('info') or {}).get('params') or {}
    def rows(key):
        value=params.get(key) or []
        return value if isinstance(value,list) else []
    profiles=[{'index':int(row['index']),'name':str(row.get('name') or ''),
               'label':str(row.get('description') or row.get('name') or ''),
               'available':str(row.get('available','unknown'))}
              for row in rows('EnumProfile') if isinstance(row,dict) and isinstance(row.get('index'),int)
              and str(row.get('name') or '').strip().lower()!='off']
    routes=[{'index':int(row['index']),'name':str(row.get('name') or ''),
             'label':str(row.get('description') or row.get('name') or ''),
             'available':str(row.get('available','unknown')),
             'profiles':row.get('profiles') if isinstance(row.get('profiles'),list) else [],
             'devices':row.get('devices') if isinstance(row.get('devices'),list) else []}
            for row in rows('EnumRoute') if isinstance(row,dict)
            and isinstance(row.get('index'),int) and str(row.get('direction')).lower()=='output'
            and str(row.get('name') or '').strip().lower() not in ('off','[out] off')]
    current=next((row for row in rows('Profile') if isinstance(row,dict)),{})
    active_routes=[int(row['index']) for row in rows('Route')
                   if isinstance(row,dict) and isinstance(row.get('index'),int)
                   and str(row.get('direction')).lower()=='output']
    return profiles,routes,current.get('index'),active_routes

def _wait_card(card, predicate, timeout=6):
    deadline=time.monotonic()+timeout
    while True:
        try:
            item=_find_card(audio_topology(),card)
            if predicate(item):return item
        except RuntimeError:pass
        if time.monotonic()>=deadline:raise RuntimeError('Playback device did not settle after profile/route change: '+card)
        time.sleep(.1)

def _route_node(card, choice):
    graph=pw_graph()
    device=next((x for x in pw_objects('Device',graph) if pw_props(x).get('device.name')==card),None)
    if device is None:raise RuntimeError('Playback device disappeared: '+card)
    nodes=[x for x in pw_objects('Node',graph) if str(pw_props(x).get('device.id'))==str(device['id'])
           and pw_props(x).get('media.class')=='Audio/Sink']
    # wpctl set-route needs the playback node with card.profile.device, not the card ID.
    devices=choice.get('devices') or []
    matched=[x for x in nodes if any(str(pw_props(x).get('card.profile.device',''))==str(d) for d in devices)]
    candidates=matched if devices else nodes
    if len(candidates)!=1:
        raise RuntimeError('Cannot uniquely identify a playback node for route '+str(choice['index'])+' on '+card)
    return candidates[0]['id']
def audio_topology():
    graph=pw_graph();devices=_physical_devices(graph);cards=[]
    for key,item in devices.items():
        props=pw_props(item);name=str(props.get('device.name') or '')
        profiles,routes,active,active_routes=_device_options(item)
        sinks=[]
        for node in pw_objects('Node',graph):
            data=pw_props(node)
            if data.get('media.class')!='Audio/Sink' or str(data.get('device.id'))!=key:continue
            sink_name=str(data.get('node.name') or '')
            if not sink_name:continue
            sinks.append({'name':sink_name,'label':str(data.get('node.description') or data.get('node.nick') or sink_name),
                          'cardName':name,'cardIndex':key,'deviceProfile':str(data.get('device.profile.name') or ''),
                          'ports':[], 'profileDevice':data.get('card.profile.device')})
        cards.append({'name':name,'index':key,'label':str(props.get('device.description') or name),
                      'profiles':profiles,'routes':routes,'activeProfile':active,
                      'activeRoutes':active_routes,'sinks':sinks})
    cards.sort(key=lambda row:row['label'].casefold())
    return {'cards':cards,'saved':saved_output_route()}

def sink_owner(sink,cards):
    return next((card for card in cards if card['name']==sink.get('cardName')),None)

def _find_card(topology,name):
    card=next((item for item in topology['cards'] if item['name']==name),None)
    if card is None:raise RuntimeError('Playback device unavailable: '+str(name))
    return card

def _profile_choice(card,value):
    try:index=int(value)
    except (TypeError,ValueError):raise ValueError('Select a valid device profile')
    row=next((p for p in card['profiles'] if p['index']==index and p['available']!='no'),None)
    if row is None or row['name'].strip().lower()=='off':raise ValueError('Playback profile is unavailable for this device')
    return row

def _route_choice(card,value,profile_index=None):
    try:index=int(value)
    except (TypeError,ValueError):raise ValueError('Select a valid output route')
    row=next((r for r in card['routes'] if r['index']==index and r['available']!='no'
              and (profile_index is None or not r['profiles'] or profile_index in r['profiles'])),None)
    if row is None:raise ValueError('Route is unavailable for this profile')
    return row

def set_output_profile(card,profile,normalize=False,topology=None):
    topology=topology or audio_topology();item=_find_card(topology,card)
    choice=_profile_choice(item,profile)
    if item['activeProfile']!=choice['index']:
        result=run([exe('wpctl'),'set-profile',str(item['index']),str(choice['index'])],False,12,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not change output profile')
    _wait_card(card,lambda row:row['activeProfile']==choice['index'])
    return audio_topology()
def set_output_route(card,route):
    item=_find_card(audio_topology(),card)
    choice=_route_choice(item,route,item['activeProfile'])
    if choice['index'] in item['activeRoutes']:
        return audio_topology()
    node=_route_node(card,choice)
    result=run([exe('wpctl'),'set-route',str(node),str(choice['index'])],False,12,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not change output route')
    _wait_card(card,lambda row:choice['index'] in row['activeRoutes'])
    return audio_topology()
def choose_output_route(requested=None):
    topology=audio_topology();cards=topology['cards']
    if not cards:raise RuntimeError('No physical playback device in PipeWire graph')
    explicit=isinstance(requested,dict) and not requested.get('_remembered')
    requested=requested if isinstance(requested,dict) else dict(topology['saved'],_remembered=True)
    wanted=str(requested.get('sink') or '')
    sinks=[sink for card in cards for sink in card['sinks']]
    selected=next((sink for sink in sinks if sink['name']==wanted and
                   (not requested.get('card') or sink['cardName']==requested['card'])),None)
    if selected is None:
        if wanted and explicit:raise RuntimeError('Selected output unavailable: '+wanted)
        try:default=pw_default()
        except RuntimeError:default=''
        selected=next((sink for sink in sinks if sink['name']==default and (not requested.get('card') or sink['cardName']==requested['card'])),None)
        if selected is None:
            # Startup may have several HDMI/speaker sinks while the configured
            # default is the virtual CamillaDSP sink. Prefer the saved device;
            # never silently choose an unrelated card.
            preferred=str(requested.get('card') or '')
            candidates=[x for x in sinks if x.get('cardName')==preferred] if preferred else sinks
            if len(candidates)==1:selected=candidates[0]
            elif not explicit and candidates:
                graph=pw_graph()
                by_name={pw_props(x).get('node.name'):pw_props(x) for x in pw_objects('Node',graph)}
                def priority(row):
                    props=by_name.get(row['name'],{})
                    try:return int(props.get('priority.session') or 0)
                    except (ValueError,TypeError):return 0
                ranked=sorted(candidates,key=priority,reverse=True)
                if len(ranked)==1 or priority(ranked[0])>priority(ranked[1]):selected=ranked[0]
            if selected is None:raise RuntimeError('Select a physical playback output')
    owner=sink_owner(selected,cards)
    if owner is None:raise RuntimeError('Selected sink has no physical device owner')
    if requested.get('card') and requested['card']!=owner['name'] and explicit:
        raise RuntimeError('Selected output belongs to a different device')
    return {'card':owner['name'],'profile':str(owner['activeProfile']),
            'sink':selected['name'],'port':str(requested.get('port') or ''),
            'label':selected['label'],'sinkCard':owner['name'],
            'sinkProfile':str(owner['activeProfile'])}

def activate_output(request,mode='laptop_laptop',password=None):
    if not isinstance(request,dict):raise ValueError('Invalid output request')
    card=str(request.get('card') or '');profile=request.get('profile');route=request.get('port')
    if not card or profile is None:raise ValueError('Select a device and profile')
    with LOCK, (audio_start_change() if STOPPED.exists() else media_change()):
        if STOPPED.exists():
            for unit in ('pipewire.socket','pipewire-pulse.socket','wireplumber.service'):
                user_service('start',unit)
            for _ in range(100):
                if run([exe('wpctl'),'status'],False,3,media_env()).returncode==0:break
                time.sleep(.1)
            else:raise RuntimeError('PipeWire did not become ready for output selection')
        before=audio_topology();original=_find_card(before,card)
        old_profile=original['activeProfile'];old_routes=list(original['activeRoutes'])
        previous_output=saved_output_route();previous_mode=audio_mode()
        try:
            (STATE/'output-preview').touch()
            set_output_profile(card,profile,topology=before)
            if route not in (None,''):
                set_output_route(card,route)
            else:
                current=_find_card(audio_topology(),card)
                valid=[r for r in current['routes'] if r['index'] in current['activeRoutes']
                       and (not r['profiles'] or current['activeProfile'] in r['profiles'])
                       and r['available'] not in ('no','unavailable')]
                if current['routes']:
                    if len(valid)!=1:
                        raise RuntimeError('The last known route is unavailable for this profile; select an exposed route')
                    route=valid[0]['index']
            target=str(request.get('sink') or '')
            deadline=time.monotonic()+5
            while True:
                owner=_find_card(audio_topology(),card)
                candidates=owner['sinks']
                if target and any(row['name']==target for row in candidates):
                    choice=_route_choice(owner,route,owner['activeProfile']) if owner['routes'] else {'devices':[]}
                    devices={str(v) for v in choice.get('devices',[])}
                    row=next(row for row in candidates if row['name']==target)
                    if not devices or row.get('profileDevice') is None or str(row['profileDevice']) in devices:break
                    raise RuntimeError('Selected sink does not belong to the chosen output route')
                if not target and candidates:
                    chosen=None
                    if route not in (None,'') and owner['routes']:
                        choice=_route_choice(owner,route,owner['activeProfile'])
                        devices={str(v) for v in choice.get('devices',[])}
                        if devices:
                            nodes=pw_objects('Node',pw_graph())
                            matched=[row for row in candidates if any(
                                pw_props(node).get('node.name')==row['name'] and
                                str(pw_props(node).get('card.profile.device','')) in devices
                                for node in nodes)]
                            if len(matched)==1:chosen=matched[0]
                            if matched:candidates=matched
                            elif all(row.get('profileDevice') is not None for row in candidates):
                                raise RuntimeError('Chosen route has no matching playback sink')
                    if chosen is None:
                        remembered=remembered_output(card,str(owner['activeProfile'])).get('sink')
                        chosen=next((row for row in candidates if row['name']==remembered),None)
                    if chosen is None:
                        try:default=pw_default()
                        except RuntimeError:default=''
                        chosen=next((row for row in candidates if row['name']==default),None)
                    if chosen is None and candidates and not request.get('sink'):
                        nodes={pw_props(node).get('node.name'):pw_props(node)
                               for node in pw_objects('Node',pw_graph())}
                        def rank(row):
                            try:return int(nodes.get(row['name'],{}).get('priority.session') or 0)
                            except (ValueError,TypeError):return 0
                        chosen=max(candidates,key=rank)
                    if chosen is not None:target=chosen['name'];break
                if time.monotonic()>=deadline:
                    raise RuntimeError('Selected route did not expose one unambiguous playback sink')
                time.sleep(.1)
            selected=choose_output_route({'card':card,'sink':target,'port':str(route or '')})
            result=set_mode(mode,password=password,output=selected,defer_resume=True)
            if not STOPPED.exists() and MODES[mode][2]:
                actual=saved_output_route()
                if actual.get('sink')!=selected['sink'] or not alive(rpid(LOCALMONPID)):
                    raise RuntimeError('Output switch was not confirmed by the local playback monitor')
            (STATE/'output-preview').unlink(missing_ok=True)
            return result
        except Exception as failure:
            (STATE/'output-preview').unlink(missing_ok=True)
            rollback=[]
            try:
                current=_find_card(audio_topology(),card)
                if old_profile is not None and current['activeProfile']!=old_profile:
                    set_output_profile(card,old_profile)
                for old_route in old_routes:
                    current=_find_card(audio_topology(),card)
                    if any(row['index']==old_route for row in current['routes']):
                        set_output_route(card,old_route)
                if previous_output.get('sink') and MODES[previous_mode][2] and not STOPPED.exists():
                    restored=choose_output_route(dict(previous_output,_remembered=True))
                    if selected_filter()==NO_FILTER and previous_mode=='laptop_laptop':direct_no_filter(restored)
                    start_local_monitor(restored,resolved=True)
                    MODE.write_text(previous_mode+'\n')
                    normalize_with_media()
            except Exception as error:rollback.append(str(error))
            if rollback:
                raise RuntimeError(str(failure)+'; previous output could not be fully restored: '+'; '.join(rollback)) from failure
            raise
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
def output_state():
    topology=audio_topology();topology['selected']=topology['saved'].copy()
    choices=_read_json(OUTPUT_CHOICES,{})
    topology['remembered']=choices if isinstance(choices,dict) else {}
    topology['rememberedProfiles']=_read_json(CARD_CHOICES,{})
    return topology
def start_local_monitor(route=None,resolved=False):
    selected=route if resolved else choose_output_route(route)
    sink=selected['sink'];pw_sink(sink)
    stop_local_monitor()
    command='set -o pipefail; "$1" -q -D camilladsp_output_shared -r 96000 -f S32_LE -c 2 -t raw | "$2" --playback --device="$3" --rate=96000 --format=s32le --channels=2'
    args=[exe('bash'),'-c',command,'monitor',exe('arecord'),exe('pacat'),sink]
    with (STATE/'local-monitor.log').open('ab',buffering=0) as log:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=media_env())
    LOCALMONPID.write_text(str(process.pid)+'\n')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if process.poll() is not None:break
        graph=pw_graph()
        streams=[x for x in pw_objects('Node',graph) if pw_props(x).get('media.class')=='Stream/Output/Audio'
                 and str(pw_props(x).get('application.process.id') or '').isdigit()]
        for stream in streams:
            pid=int(pw_props(stream)['application.process.id'])
            try:
                if os.getpgid(pid)!=process.pid:continue
            except OSError:continue
            links=[x for x in pw_objects('Link',graph) if str((x.get('info') or {}).get('output-node-id'))==str(stream['id'])]
            target=pw_sink(sink,graph)
            if any(str((x.get('info') or {}).get('input-node-id'))==str(target['id']) for x in links):
                _write_json(LOCALSINK,selected);remember_output(selected)
                return {'pid':process.pid,**selected}
        time.sleep(.1)
    stop_local_monitor()
    raise RuntimeError('Local playback stream not linked to '+sink+'; check '+str(STATE/'local-monitor.log'))
def mode_state():
    if STOPPED.exists():
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
        'localOutput':selected,'localOutputLabel':(route.get('label') or 'Not selected'),'sonobusPolicy':policy,
    }
def apply_mode(name,password=None,restore_camilla=True,output=None,normalize=True,output_resolved=False):
    if name not in MODES:raise ValueError('Invalid audio mode')
    label,source,local,sono=MODES[name];policy=MODE_POLICIES[name]
    if STOPPED.exists():
        user_service('start','pipewire.socket')
        user_service('start','pipewire-pulse.socket')
        user_service('start','wireplumber.service')
    direct=(selected_filter()==NO_FILTER and source=='system' and not sono and local)
    if selected_filter()==NO_FILTER:
        if alive(rpid(CAMPID),'camilladsp'):stop_camilla(include_stale=True)
        start_bypass()
    elif restore_camilla and not alive(rpid(CAMPID),'camilladsp'):
        try:saved=ACTIVE.read_text().strip()
        except OSError:saved=''
        available=profiles()
        selected=profile(saved) if saved and saved!=NO_FILTER else available[0]
        start_camilla(selected)
    apply_source_services(source,direct=direct)
    if source=='system' and not direct and selected_filter()!=NO_FILTER:
        restore_dsp_desktop_sink()
    if direct and run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode==0:
        user_service('stop',SYSTEM_AUDIO_SERVICE)
    # The NixOS desktop service periodically reasserts camilladsp as the
    # default. It must be stopped before No filter selects a physical sink.
    if direct and run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode==0:
        raise RuntimeError('Desktop sink watchdog is still active in No filter')
    if source=='system' and not direct and selected_filter()!=NO_FILTER:
        if run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode:
            raise RuntimeError('CamillaDSP desktop sink service is not active')
    if local:
        selected=output if output_resolved else choose_output_route(output)
        if direct:direct_no_filter(selected)
        current=saved_output_route()
        wanted={key:selected[key] for key in ('card','profile','sink','port')}
        start_local_monitor(selected,resolved=True)
    elif alive(rpid(LOCALMONPID)):stop_local_monitor()
    if sono:
        if not sonobus_matches(policy):restart_sonobus(password,policy,normalize=False)
    elif sonopids():stop_sonobus()
    MODE.write_text(name+'\n')
    if normalize:normalize_with_media()
    STOPPED.unlink(missing_ok=True)
    if source=='system' and not direct and QUEUE_FILE.is_file() and not alive(rpid(MPVPID),'mpv'):
        try:ensure_mpv()
        except (OSError,RuntimeError):pass
    return mode_state()
def set_mode(name,password=None,output=None,defer_resume=False):
    with LOCK:
        if name not in MODES:raise ValueError('Invalid audio mode')
        was_stopped=STOPPED.exists()
        transaction=nullcontext() if defer_resume else (audio_start_change() if was_stopped else media_change())
        with transaction:
            if was_stopped and MODES[name][2]:
                for unit in ('pipewire.socket','pipewire-pulse.socket','wireplumber.service'):
                    user_service('start',unit)
                for _ in range(100):
                    if run([exe('wpctl'),'status'],False,3,media_env()).returncode==0:break
                    time.sleep(.1)
                else:raise RuntimeError('PipeWire did not become ready for output selection')
            selected=choose_output_route(output) if MODES[name][2] else None
            if was_stopped and alive(rpid(CAMPID),'camilladsp'):
                stop_local_monitor();stop_camilla(include_stale=True)
            # Resume occurs only after apply_mode has normalized and returned.
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
COMBINED_LOCK=threading.RLock()
COMBINED_FILE=STATE/'away-display.lock'
def away_identity(pid):
    try:
        text=Path(f'/proc/{pid}/stat').read_text()
        rest=text.rsplit(') ',1)[1].split()
        if rest[0]=='Z':return None
        return (int(rest[1]),int(rest[19]))
    except (OSError,ValueError,IndexError):return None
def away_record():
    data=_read_json(AWAY_FILE,{})
    return data if isinstance(data,dict) else {}
def away_lock_pid():
    data=away_record();pid=data.get('lockerPid');start=data.get('lockerStart')
    try:
        pid=int(pid);start=int(start)
        identity=away_identity(pid)
        if identity and identity[1]==start and Path(f'/proc/{pid}/comm').read_text().strip()=='swaylock':return pid
    except (OSError,ValueError,TypeError):pass
    return None
def away_state():
    return {'active':away_lock_pid() is not None,'pausedWindows':0}
def thaw_legacy_away():
    old=away_record()
    for record in reversed(old.get('processes',[])):
        try:
            pid,start=record
            identity=away_identity(pid)
            if identity and identity[1]==int(start):os.kill(pid,signal.SIGCONT)
        except (OSError,ValueError,TypeError,PermissionError):pass
def start_away_lock():
    if away_lock_pid():return
    if away_record().get('lockerPid'):
        # A password unlock can exit swaylock independently of this UI.
        # The verified PID is gone; clear only the obsolete tracking record.
        AWAY_FILE.unlink(missing_ok=True)
    thaw_legacy_away()
    image=HOME/'.config/sway/mcqueen.jpeg'
    if not image.is_file():raise RuntimeError('Swaylock image not found: '+str(image))
    # Readiness FD reports compositor lock, not merely process creation.
    read_fd,write_fd=os.pipe()
    try:
        proc=subprocess.Popen([exe('swaylock'),'-F','-i',str(image),'-R',str(write_fd)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            pass_fds=(write_fd,),start_new_session=True,env=media_env())
        os.close(write_fd);write_fd=-1
        ready,_,_=select.select([read_fd],[],[],8)
        if not ready or os.read(read_fd,1)!=b'\n':
            detail=f'swaylock exit status {proc.poll()}' if proc.poll() is not None else 'readiness timeout'
            if proc.poll() is None:os.kill(proc.pid,signal.SIGUSR1)
            raise RuntimeError('Swaylock did not confirm session lock: '+detail)
        identity=away_identity(proc.pid)
        if not identity:raise RuntimeError('Swaylock exited before display off')
        _write_json(AWAY_FILE,{'active':True,'lockerPid':proc.pid,'lockerStart':identity[1]})
    finally:
        os.close(read_fd)
        if write_fd>=0:os.close(write_fd)
def stop_away_lock():
    pid=away_lock_pid()
    if pid:
        os.kill(pid,signal.SIGUSR1)
        deadline=time.monotonic()+5
        while away_lock_pid() and time.monotonic()<deadline:time.sleep(.05)
        if away_lock_pid():raise RuntimeError('Swaylock did not unlock; display is on for recovery')
    elif away_record().get('lockerPid'):
        # The user may already have unlocked with a password after waking.
        # Never signal an unverified/reused PID.
        pass
    thaw_legacy_away()
    AWAY_FILE.unlink(missing_ok=True)
def away_display_state():
    display=display_state();locked=away_state()['active']
    return {'active':locked or display['off'],'away':locked,'displayOff':display['off'],'pausedWindows':0}
def set_away_display(active):
    STATE.mkdir(parents=True,exist_ok=True)
    with COMBINED_LOCK, COMBINED_FILE.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        before=away_display_state()
        if active:
            if not before['away']:start_away_lock()
            try:
                if not before['displayOff']:display_power(False)
            except Exception:
                if not before['away']:stop_away_lock()
                raise
        else:
            # Wake first, then unlock. A failure leaves a visible recovery path.
            if before['displayOff']:display_power(True)
            if before['away']:stop_away_lock()
            elif away_record().get('lockerPid'):
                stop_away_lock()
        return away_display_state()
def toggle_away_display():
    state=away_display_state()
    if state['displayOff']:
        with COMBINED_LOCK, COMBINED_FILE.open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            display_power(True)
            return away_display_state()
    return set_away_display(not state['away'])
def full_state():
    return {
        'mode':mode_state(),'awayDisplay':away_display_state(),
        'profiles':{'profiles':[NO_FILTER]+[item.name for item in profiles()],'filters':listening_filters(),'active':active(),'running':alive(rpid(CAMPID),'camilladsp')},
        'player':player_state(),'systemMedia':system_state(),'groups':groups_state(),'playlists':lists(),
    }

def volatile_state():
    return {
        'audioStopSignal':True,
        'mode':mode_state(),'awayDisplay':away_display_state(),
        'profiles':{'active':active(),'filters':listening_filters(),'running':alive(rpid(CAMPID),'camilladsp')},
        'player':player_state(),'groups':groups_state(),
    }

PAGE='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>CamillaDSP Studio</title><style>\n:root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif;--v:#865dff;--b:#3f8eff;--g:#35d6a0}*{box-sizing:border-box}body{margin:0;min-height:100svh;padding:22px 16px 36px;color:#fff;background:radial-gradient(900px 520px at 50% -150px,#6542a8,#211a38 44%,#070910);background-attachment:fixed}main{max-width:540px;margin:auto}.hidden{display:none!important}.hero,.card{border:1px solid #ffffff22;background:linear-gradient(145deg,#ffffff1c,#ffffff0d);box-shadow:inset 0 1px #ffffff22,0 20px 50px #0005;backdrop-filter:blur(22px)}.hero{padding:25px 22px;border-radius:29px}.card{padding:18px;border-radius:24px;margin:14px 0}.card+.grid{margin-top:14px}.eyebrow,.label,.section-title{color:#bcb7cb;text-transform:uppercase;letter-spacing:.1em;font-size:12px;font-weight:800}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--g);box-shadow:0 0 15px var(--g);margin-right:8px}h1{margin:10px 0 5px;font-size:32px;letter-spacing:-.04em}.subtitle,.details,.meta{color:#b9b4c5;font-size:13px}.section-title{margin:27px 3px 10px}.title{font-size:20px;font-weight:800;margin-top:6px;overflow-wrap:anywhere}.grid{display:grid;gap:10px}.two{grid-template-columns:1fr 1fr}.three{grid-template-columns:1fr 1.2fr 1fr}.pc-transport{margin-bottom:18px}.pc-library-actions{margin-top:18px}.wide{grid-column:1/-1}button,input{font:inherit}button{border:0;color:#fff;cursor:pointer}.action,.item,.transport,.back{width:100%;transition:transform .12s,filter .15s}.action:active,.item:active,.transport:active,.back:active{transform:scale(.96);filter:brightness(1.15)}.action{min-height:54px;border-radius:18px;background:#ffffff1b;font-weight:780}.primary{background:linear-gradient(135deg,var(--b),var(--v))}.green{background:linear-gradient(135deg,#24ae7c,#287d95)}.danger{background:linear-gradient(135deg,#e64e74,#8e2b4b)}.active{outline:2px solid #a783ff;background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important}.search{width:100%;min-height:49px;border:1px solid #ffffff22;border-radius:17px;padding:12px 15px;color:#fff;background:#ffffff12;outline:0}.search:focus{border-color:#9b7aff;box-shadow:0 0 0 4px #825dff2e}.list{display:grid;gap:10px;margin-top:10px}.cover-art{width:48px;height:48px;flex:none;object-fit:cover;border-radius:11px;background:#ffffff18}.cover-art.large{width:100%;max-width:220px;height:auto;aspect-ratio:1;display:block;margin:0 auto 16px;border-radius:20px}.cover-fallback{display:grid;place-items:center;color:#c3b6f3;font-size:24px}.cover-fallback.large{display:grid;font-size:68px}.item-cover{display:flex;align-items:center;gap:12px;min-width:0}.item-cover .copy{min-width:0;flex:1;overflow-wrap:anywhere;font-size:16px;line-height:1.3}.item-cover .name{font-size:16px;line-height:1.3;font-weight:780}.item-cover .meta{font-size:13px;line-height:1.35;font-weight:400}.item{text-align:left;min-height:64px;padding:13px 15px;border-radius:19px;background:#ffffff16}.name{display:block;font-weight:780}.meta{display:block;margin-top:4px}.playlist-row{position:relative}\n.playlist-open{display:block;padding-right:120px;min-height:76px}\n.playlist-controls{position:absolute;right:12px;top:50%;transform:translateY(-50%);display:flex;gap:8px;z-index:1}\n.playlist-icon{display:grid;place-items:center;width:44px;height:44px;border:1px solid #ffffff38;border-radius:14px;background:#242036e8;box-shadow:0 4px 14px #0005}\n.playlist-icon:hover{background:#564282}\n.playlist-icon:active{transform:scale(.94)}\n.playlist-icon .media-icon{width:20px;height:20px}\n.playlist-icon.busy{font-size:20px}.header{display:grid;grid-template-columns:72px 1fr 72px;align-items:center}.header h1{text-align:center;font-size:23px}.back{min-height:43px;border-radius:15px;background:#ffffff18}.range{--fill:0%;width:100%;height:36px;background:transparent;appearance:none}.range::-webkit-slider-runnable-track{height:6px;border-radius:99px;background:linear-gradient(to right,#fff var(--fill),#ffffff2d var(--fill))}.range::-webkit-slider-thumb{appearance:none;width:21px;height:21px;margin-top:-7.5px;border-radius:50%;background:#fff;box-shadow:0 3px 10px #0008}.times{display:flex;justify-content:space-between;color:#aaa5b5;font-size:12px}.range-row{display:grid;grid-template-columns:24px 1fr 24px;align-items:center;gap:7px}.transport{display:grid;place-items:center;min-height:78px;border-radius:999px;background:#ffffff19}.transport.main{min-height:98px;background:linear-gradient(145deg,#9e68ff,#583ad2)}.media-icon{display:block;width:34px;height:34px;fill:none;stroke:#fff;stroke-width:2.15;stroke-linecap:round;stroke-linejoin:round;pointer-events:none}.transport.main .media-icon{width:42px;height:42px}.icon-fill{fill:#fff;stroke:#fff}.range{touch-action:none}.range.dragging::-webkit-slider-thumb{transform:scale(1.08)}.source-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}\n\n/* Final interaction and consistency pass */\nbutton{position:relative;overflow:hidden;-webkit-user-select:none;user-select:none;touch-action:manipulation}\nbutton:focus-visible,.search:focus-visible,.range:focus-visible{outline:2px solid #b79cff;outline-offset:3px}\nbutton:disabled{opacity:.58;cursor:default}\n.action,.item,.transport,.back{will-change:transform}\n.action.busy::after,.item.busy::after{content:"";position:absolute;inset:0;background:linear-gradient(105deg,transparent 25%,#ffffff2e 50%,transparent 75%);animation:ui-shine .8s linear infinite}\n@keyframes ui-shine{from{transform:translateX(-100%)}to{transform:translateX(100%)}}\n.transport{transition:transform .12s ease,filter .15s ease,box-shadow .2s ease}\n.transport.main.playing{box-shadow:0 18px 42px #6b45dd77,inset 0 1px #ffffff35}\n.media-icon{transition:transform .16s ease}.transport:active .media-icon{transform:scale(.9)}\n.range{cursor:pointer}.range.dragging::-webkit-slider-thumb{transform:scale(1.1)}\n.range::-webkit-slider-runnable-track{transition:background .08s linear}\n.mode-active{outline:2px solid #72e5bc;background:linear-gradient(135deg,#24ae7c,#287d95)!important}\n#repeat.active,#shuffle.active{outline:2px solid #a783ff;background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important}\n.output-modal{position:fixed;inset:0;z-index:50;display:grid;place-items:end center;padding:18px;background:#05060bbb;backdrop-filter:blur(12px)}.output-sheet{width:min(100%,540px);max-height:82svh;overflow:auto;padding:20px;border:1px solid #ffffff2b;border-radius:26px;background:linear-gradient(145deg,#272139,#11131c);box-shadow:0 28px 80px #000b}.output-options{display:grid;gap:10px;margin:16px 0}.output-choice{display:grid;grid-template-columns:24px 1fr;gap:10px;align-items:center;padding:14px;border-radius:18px;background:#ffffff12}.output-choice input{width:20px;height:20px;accent-color:#865dff}.output-choice strong,.output-choice span{display:block}.output-choice span{margin-top:3px;color:#aaa5b5;font-size:11px;overflow-wrap:anywhere}.modal-actions{display:grid;grid-template-columns:1fr 1.4fr;gap:10px}\n\n@media (prefers-reduced-motion:reduce){*{animation-duration:.001ms!important;transition-duration:.001ms!important;scroll-behavior:auto!important}}\n\n/* Page rhythm and accessible navigation. */\n#profiles>.hero{margin-bottom:20px}\n#profiles>.action{display:block;margin:14px 0 20px}\n#profiles>.grid{margin-top:12px}\n#profiles>.section-title{margin-top:26px;margin-bottom:12px}\n#groups-page .header,#listening-page .header,#songs .header,#playlist .header,#pc .header{margin-bottom:18px}\n#groups-page .card{display:grid;gap:12px}\n#groups-page .card .search,#groups-page .card .action{margin:0}\n#groups-page .card label{display:flex;align-items:center;gap:9px}\n.card>.details{margin-top:6px}\n#pc .card>.label{margin-bottom:8px}\n#listening-page .search{margin-bottom:16px}\n#song-search,#playlist-search{display:block;margin:0 0 16px}\n#song-list,#playlist-songs{margin-top:0}\n.system-media-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}\n.card .range{display:block;margin-top:12px}\n.card .times{margin-top:2px}\n.card .range-row{margin:14px 0 16px}\n.card .grid{margin-top:14px}\n.range-row{grid-template-columns:24px minmax(0,1fr) 24px;gap:10px}\n.volume-icon{display:grid;place-items:center;color:#d6d3de;pointer-events:none}\n.volume-icon svg{display:block;width:20px;height:20px}\n.local-now-header{display:flex;align-items:center;gap:14px;min-width:0;margin:10px 0 16px}\n.local-now-copy{flex:1;min-width:0}\n.local-now-copy .details{margin-top:6px;overflow-wrap:anywhere}\n.local-now-artwork{flex:0 0 72px;width:72px;height:72px;position:relative;display:grid;place-items:center;overflow:hidden;border-radius:15px;background:linear-gradient(145deg,#433b68,#222d42);color:#c8c1e9;font-size:32px;line-height:1}\n.local-now-artwork::before{content:\'♫\'}\n.local-now-artwork img{position:absolute;inset:0;display:block;width:100%;height:100%;object-fit:cover}\n.back-to-top{position:fixed;z-index:40;top:max(14px,env(safe-area-inset-top));left:50%;transform:translateX(-50%);width:auto;min-height:44px;padding:9px 17px;border:1px solid #ffffff38;border-radius:999px;background:#262039f2;box-shadow:0 8px 28px #0009;white-space:nowrap;font-size:14px;font-weight:750;backdrop-filter:blur(14px)}\n.back-to-top:active{transform:translateX(-50%) scale(.96)}\n\n/* Playlist actions: clear affordance without changing the page layout. */\n.playlist-row{border-radius:19px;transition:background .16s ease}\n.playlist-row:hover{background:#ffffff08}\n.playlist-icon{transition:background .16s ease,border-color .16s ease,transform .12s ease}\n.playlist-icon:hover{border-color:#aa90ff;background:#493572}\n#shuffle:disabled{opacity:.42;cursor:not-allowed}\n#shuffle:not(:disabled){background:linear-gradient(135deg,#6252a7,#373b77)}\n\n/* Unified indigo, blue and mint palette. */\n:root{--v:#8574f5;--b:#6c9cff;--g:#55d9ae}\nbody{background:radial-gradient(900px 520px at 50% -150px,#514784,#1d2039 48%,#0b101b)}\n.hero,.card{border-color:#a8b7ef25;background:linear-gradient(145deg,#b8c8ff18,#a8b8ee0a)}\n.action:not(.primary):not(.green):not(.danger):not(.active){background:#a8b7ef1b}\n#shuffle:not(:disabled){background:linear-gradient(135deg,#6257ae,#465b99)}\n.playlist-icon{background:#242941e8;border-color:#b7c7ff38}\n.playlist-icon:hover{background:#414a77;border-color:#9caeff}\n.output-sheet{background:linear-gradient(145deg,#252b47,#111827)}\n\n/* Shared home palette and compact destination layout. */\n:root{--v:#8574f5;--b:#6c9cff;--g:#55d9ae}\n#profiles{max-width:480px;margin-inline:auto}\n#profiles>.hero{padding:17px 18px;border-radius:23px;margin-bottom:12px}\n#profiles>.hero h1{font-size:27px;margin:6px 0 3px}\n#profiles>.card{padding:16px;margin:10px 0;border-radius:21px}\n#profiles>.section-title{margin:18px 3px 8px}\n#profiles>.action{min-height:48px;margin:10px 0 12px;border-radius:16px}\n#profiles .source-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:8px}\n#profiles .source-grid .source-heading{grid-column:1/-1;color:#bcb7cb;font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;margin:9px 2px 0}\n#profiles .source-grid .action{min-height:50px;padding:8px 5px;border-radius:15px;font-size:14px;line-height:1.2}\n#profiles .card .grid{margin-top:10px}\n#profiles .transport{min-height:64px}\n#profiles .transport.main{min-height:80px}\n#profiles .pc-transport{margin-bottom:12px}\n</style></head><body><main>\n<section id="profiles"><div class="hero"><div class="eyebrow"><span class="dot"></span><span id="engine-status">Audio engine offline</span></div><h1>CamillaDSP Studio</h1><div class="subtitle">System audio, AirPlay and PC Music through CamillaDSP and SonoBus.</div></div><div class="section-title">System media</div><div class="card"><div class="system-media-heading"><div class="label">Active desktop player</div></div><div id="system-title" class="title">No system media</div><div id="system-details" class="details"></div><input id="system-seek" class="range" type="range" aria-label="System media playback position" min="0" max="1" step=".1"><div class="times"><span id="system-elapsed">0:00</span><span id="system-duration">0:00</span></div><div class="range-row"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 4V5L7 9H3z"/></svg></span><input id="system-volume" class="range" type="range" aria-label="Master volume" min="0" max="100" value="100"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 9v6h4l5 4V5L6 9H2z"/><path d="M14 8.2a5 5 0 0 1 0 7.6l1.4 1.4a7 7 0 0 0 0-10.4L14 8.2z"/><path d="M17 5.3a9 9 0 0 1 0 13.4l1.4 1.4a11 11 0 0 0 0-16.2L17 5.3z"/></svg></span></div><div class="grid three"><button id="system-previous" class="transport" aria-label="Previous"><svg class="media-icon" viewBox="0 0 24 24"><path d="M6 5v14"></path><path d="M18 6.5 8.5 12 18 17.5z"></path></svg></button><button id="system-toggle" class="transport main" aria-label="Play"><svg class="media-icon" viewBox="0 0 24 24"><path id="system-play-shape" class="icon-fill" d="M8 5.5 19 12 8 18.5z"></path></svg></button><button id="system-next" class="transport" aria-label="Next"><svg class="media-icon" viewBox="0 0 24 24"><path d="M18 5v14"></path><path d="M6 6.5 15.5 12 6 17.5z"></path></svg></button></div></div><button id="open-pc" class="action green">Local Music Library</button><button id="away-display-toggle" class="action" type="button" aria-pressed="false" style="margin-top:10px">Away and display off</button><div class="section-title">Audio source</div><div class="card"><div id="source-title" class="title">Loading…</div><div id="source-details" class="details"></div><div id="mode-grid" class="source-grid"></div></div><div class="section-title">SonoBus Group</div><div class="card"><div class="label">Selected group</div><div id="sonobus-group" class="title">Loading…</div><div id="sonobus-status" class="details">Checking SonoBus…</div></div><button id="open-groups" class="action">Manage SonoBus Group</button><div class="section-title">Active profile</div><div class="card"><div id="active-profile" class="title">Loading…</div><div id="active-state" class="details"></div></div><button id="open-listening" class="action">Select Listening Profile</button><div class="section-title" id="restart-heading">Restart services</div><div class="grid two" aria-labelledby="restart-heading"><button id="restart-camilla" class="action">Restart CamillaDSP</button><button id="restart-sonobus" class="action">Restart SonoBus</button><button id="restart-airplay" class="action">Restart AirPlay</button><button id="restart-vnc" class="action">Restart VNC</button></div><button id="audio-toggle" class="action danger" type="button" style="margin-top:16px;min-height:64px">Stop all audio</button></section>\n<section id="groups-page" class="hidden"><div class="header"><button data-back="profiles" class="back">Back</button><h1>SonoBus Group</h1><div></div></div><div class="card"><select id="group-select" class="search"></select><input id="group-key" class="search" placeholder="Profile name"><input id="group-name" class="search" placeholder="Group name"><input id="group-user" class="search" placeholder="Username"><input id="group-server" class="search" value="aoo.sonobus.net:10998" placeholder="Connection server"><label class="details"><input id="group-required" type="checkbox"> Password required</label><input id="group-password" class="search" type="password" placeholder="Password (never saved)"><button id="save-group" class="action primary">Save Group Profile</button></div></section>\n<section id="listening-page" class="hidden"><div class="header"><button data-back="profiles" class="back">Back</button><h1>Listening Profiles</h1><div></div></div><div class="section-title">Listening profiles</div><input id="profile-search" class="search" placeholder="Search profiles"><div id="profile-list"></div></section>\n<section id="pc" class="hidden"><div class="header"><button id="pc-back" class="back">Back</button><h1>PC Music</h1><div></div></div><div class="card"><div class="label">Now playing</div><div class="local-now-header"><span id="now-cover" class="local-now-artwork" aria-hidden="true"></span><div class="local-now-copy"><div id="now-title" class="title">Nothing playing</div><div id="now-details" class="details"></div></div></div><input id="seek" class="range" type="range" aria-label="Local music playback position" min="0" max="1" step=".1"><div class="times"><span id="elapsed">0:00</span><span id="duration">0:00</span></div><div class="range-row"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 4V5L7 9H3z"/></svg></span><input id="volume" class="range" type="range" aria-label="Master volume" min="0" max="100" value="100"><span class="volume-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 9v6h4l5 4V5L6 9H2z"/><path d="M14 8.2a5 5 0 0 1 0 7.6l1.4 1.4a7 7 0 0 0 0-10.4L14 8.2z"/><path d="M17 5.3a9 9 0 0 1 0 13.4l1.4 1.4a11 11 0 0 0 0-16.2L17 5.3z"/></svg></span></div><div class="grid two"><button id="repeat" class="action">Repeat Off</button><button id="shuffle" class="action" title="Update the saved shuffled playlist without changing what is playing">Shuffle for next play</button></div></div><div class="grid three pc-transport"><button data-cmd="previous" class="transport" aria-label="Previous"><svg class="media-icon" viewBox="0 0 24 24"><path d="M6 5v14"></path><path d="M18 6.5 8.5 12 18 17.5z"></path></svg></button><button data-cmd="toggle" class="transport main" aria-label="Play"><svg class="media-icon" viewBox="0 0 24 24"><path id="local-play-shape" class="icon-fill" d="M8 5.5 19 12 8 18.5z"></path></svg></button><button data-cmd="next" class="transport" aria-label="Next"><svg class="media-icon" viewBox="0 0 24 24"><path d="M18 5v14"></path><path d="M6 6.5 15.5 12 6 17.5z"></path></svg></button></div><div class="grid pc-library-actions"><button id="all-songs" class="action primary">All Songs</button></div><div class="section-title">Playlists</div><div id="playlists" class="list"></div></section>\n<section id="songs" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1>All Songs</h1><div></div></div><input id="song-search" class="search" type="search" aria-label="Search all songs" placeholder="Search all songs"><div id="song-list" class="list"></div></section>\n<section id="playlist" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1 id="playlist-title">Playlist</h1><div></div></div><input id="playlist-search" class="search" type="search" aria-label="Search this playlist" placeholder="Search this playlist"><div id="playlist-songs" class="list"></div></section><button id="back-to-top" class="back-to-top hidden" type="button">Go back to top of page ↑</button><div id="output-modal" class="output-modal hidden"><div class="output-sheet"><div class="label">Laptop playback device</div><div id="output-mode-title" class="title">Choose output</div><div class="details">Choose from the devices, profiles, routes and sinks currently exposed by WirePlumber.</div><div class="output-options"><label class="details">Device<select id="output-card" class="search"></select></label><label class="details">Profile<select id="output-profile" class="search"></select></label><label class="details">Output route<select id="output-route" class="search"></select></label><label class="details">Playback sink<select id="output-sink" class="search"></select></label></div><div class="modal-actions"><button id="output-cancel" class="action">Back without changes</button><button id="output-apply" class="action primary">Apply output</button></div></div></div>\n</main><script>const $=selector=>document.querySelector(selector);\nconst screens=[\'profiles\',\'pc\',\'songs\',\'playlist\',\'groups-page\',\'listening-page\'].map(id=>$(\'#\'+id));\nlet all=[],inside=[],current=\'\';\n\nconst state={\n  system:{slider:$(\'#system-seek\'),elapsed:$(\'#system-elapsed\'),durationLabel:$(\'#system-duration\'),volume:$(\'#system-volume\'),duration:0,dragging:false,polling:false,repoll:false,clockPosition:0,clockAt:0,clockPlaying:false,lastAvailableAt:0,trackKey:\'\',skipPending:false,skipFrom:\'\',skipStarted:0,skipWarned:false,volumeHold:0,volumeTimer:null,volumePending:null},\n  local:{slider:$(\'#seek\'),elapsed:$(\'#elapsed\'),durationLabel:$(\'#duration\'),volume:$(\'#volume\'),duration:0,dragging:false,polling:false,volumeHold:0,volumeTimer:null,volumePending:null}\n};\n\nconst fmt=value=>{const v=Math.max(0,Number(value)||0);return Math.floor(v/60)+\':\'+String(Math.floor(v)%60).padStart(2,\'0\')};\nfunction show(id){screens.forEach(screen=>screen.classList.toggle(\'hidden\',screen.id!==id));scrollTo({top:0,behavior:\'smooth\'});updateBackToTop()}\nfunction updateBackToTop(){const visible=[\'songs\',\'playlist\'].some(id=>!$(\'#\'+id).classList.contains(\'hidden\'));$(\'#back-to-top\').classList.toggle(\'hidden\',!visible||window.scrollY<320)}\nwindow.addEventListener(\'scroll\',updateBackToTop,{passive:true});$(\'#back-to-top\').onclick=()=>window.scrollTo({top:0,behavior:\'smooth\'});$(\'#open-groups\').onclick=()=>show(\'groups-page\');$(\'#open-listening\').onclick=()=>show(\'listening-page\');\nfunction notificationBox(){const banners=[...document.querySelectorAll(\'#global-task-feedback\')];let banner=banners.shift()||null;banners.forEach(item=>item.remove());if(!banner){banner=document.createElement(\'div\');banner.id=\'global-task-feedback\';banner.setAttribute(\'role\',\'status\');banner.setAttribute(\'aria-live\',\'polite\');banner.style.cssText=\'position:fixed;left:12px;right:12px;top:12px;z-index:5000;box-sizing:border-box;padding:14px 64px 14px 18px;border-radius:14px;background:#152033;color:#eef5ff;border:1px solid #43638f;box-shadow:0 12px 32px rgba(0,0,0,.4);font-weight:700;text-align:center;\';const message=document.createElement(\'span\');message.className=\'task-feedback-message\';const close=document.createElement(\'button\');close.type=\'button\';close.className=\'task-feedback-close\';close.textContent=\'×\';close.setAttribute(\'aria-label\',\'Close notification\');close.style.cssText=\'position:absolute;right:8px;top:50%;transform:translateY(-50%);width:48px;height:48px;border:0;border-radius:12px;background:transparent;color:inherit;font-size:38px;font-weight:400;line-height:42px;cursor:pointer;\';close.addEventListener(\'click\',()=>{clearTimeout(showTaskFeedback.timer);banner.classList.add(\'hidden\')});banner.append(message,close);document.body.appendChild(banner)}return banner}function showTaskFeedback(message,kind=\'working\'){const banner=notificationBox(),messageNode=banner.querySelector(\'.task-feedback-message\');if(messageNode)messageNode.textContent=String(message||\'Working…\');banner.style.background=kind===\'done\'?\'#163d2b\':(kind===\'error\'?\'#4a2025\':\'#152033\');banner.style.borderColor=kind===\'done\'?\'#2f7654\':(kind===\'error\'?\'#a64b56\':\'#43638f\');banner.classList.remove(\'hidden\');clearTimeout(showTaskFeedback.timer);if(kind!==\'working\')showTaskFeedback.timer=setTimeout(()=>banner.classList.add(\'hidden\'),10000)}function note(message,error=false){if(error&&message)showTaskFeedback(message,\'error\')}\nasync function api(url,options={}){const response=await fetch(url,{cache:\'no-store\',...options});const data=await response.json().catch(()=>null);if(!data||typeof data!==\'object\'||Array.isArray(data))throw Error(\'Invalid server response\');if(!response.ok||data.ok===false)throw Error(data.error||\'Request failed\');return data}\nconst post=(url,data={})=>api(url,{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(data)});\nfunction fill(element,value,maximum){const max=Number(maximum),v=Number(value);const percent=Number.isFinite(max)&&max>0&&Number.isFinite(v)?Math.max(0,Math.min(100,v/max*100)):0;element.style.setProperty(\'--fill\',percent+\'%\')}\nfunction renderTimeline(media,position,duration){\n  const total=Number.isFinite(Number(duration))?Math.max(0,Number(duration)):0;\n  const current=Number.isFinite(Number(position))?Math.max(0,Math.min(Number(position),total>0?total:Number(position))):0;\n  media.duration=total;\n  media.slider.max=String(total>0?total:1);\n  if(!media.dragging)media.slider.value=String(current);\n  const shown=media.dragging?Number(media.slider.value):current;\n  fill(media.slider,shown,total);\n  media.elapsed.textContent=fmt(shown);\n  media.durationLabel.textContent=fmt(total);\n}\n\nfunction renderActiveProfile(data){\n  const info=data.filters?.[data.active];\n  $(\'#active-profile\').textContent=data.active?(info?.group||\'Other\'):\'No profile\';\n  $(\'#active-state\').textContent=data.active?((info?.label||data.active)+\' · \'+(data.active===\'__no_filter__\'?\'Bypassed\':data.running?\'Running\':\'Stopped\')):\'Stopped\'\n}\nfunction renderSonobusStatus(mode,groups){\n  const selected=groups?.profiles?.[groups.active];\n  $(\'#sonobus-group\').textContent=selected?.group||\'No group selected\';\n  $(\'#sonobus-status\').textContent=(mode.sonobus?\'Process running\':\'Process stopped\')+\n    (selected?\' • Profile: \'+groups.active+\' • User: \'+selected.username:\'\')\n}\nasync function busy(button,work,label=\'Working…\',doneLabel=\'Done\',feedback=false){if(button.disabled)return;const original=button.innerHTML;button.disabled=true;button.classList.add(\'busy\');if(!button.classList.contains(\'transport\'))button.textContent=label;if(feedback)showTaskFeedback(label,\'working\');try{const result=await work();if(feedback&&doneLabel)showTaskFeedback(doneLabel,\'done\');return result}catch(error){showTaskFeedback(error?.message||String(error),\'error\');throw error}finally{button.disabled=false;button.classList.remove(\'busy\');button.innerHTML=original}}\nfunction setPlayIcon(shape,button,playing){if(!shape||!button)return;shape.setAttribute(\'d\',playing?\'M8 5h3v14H8z M14 5h3v14h-3z\':\'M8 5.5 19 12 8 18.5z\');button.setAttribute(\'aria-label\',playing?\'Pause\':\'Play\');button.classList.toggle(\'playing\',playing)}\n\nfunction setSystemAvailability(data){for(const id of [\'system-previous\',\'system-toggle\',\'system-next\'])$(\'#\'+id).disabled=!data.available;$(\'#system-seek\').disabled=!data.available||!data.seekable}\nfunction systemTrackKey(data){\n  if(!data||!data.available)return \'\';\n  return [data.player||\'\',data.trackId||\'\',data.title||\'\',data.artist||\'\',data.duration||\'\'].join(\'\\x1f\')\n}\nfunction resetSystemPosition(){\n  const media=state.system;\n  media.clockPlaying=false;media.clockAt=0;media.clockPosition=0;\n  media.dragging=false;media.slider.classList.remove(\'dragging\');\n  renderTimeline(media,0,0)\n}\nfunction systemPositionError(message){\n  const media=state.system;\n  if(!media.skipWarned){media.skipWarned=true;showTaskFeedback(message,\'error\')}\n}\nfunction renderSystem(data){\n  const media=state.system,now=performance.now();\n  if(!data||typeof data!==\'object\')return;\n  if(!data.available&&media.lastAvailableAt&&now-media.lastAvailableAt<1500){\n    if(media.skipPending)resetSystemPosition();\n    return\n  }\n  if(data.available)media.lastAvailableAt=now;\n  else media.lastAvailableAt=0;\n  $(\'#system-title\').textContent=data.title||\'No system media\';\n  $(\'#system-details\').textContent=[data.artist,data.player,data.status].filter(Boolean).join(\' • \');\n  const key=systemTrackKey(data),duration=Number(data.duration),position=data.position===null||data.position===undefined||data.position===\'\'?NaN:Number(data.position);\n  const valid=!!data.available&&Number.isFinite(duration)&&duration>0&&Number.isFinite(position)&&position>=0&&position<=duration+2;\n  if(media.skipPending){\n    // Ignore snapshots from the old track after the skip command.\n    const changed=key&&key!==media.skipFrom;\n    const restarted=key&&key===media.skipFrom&&valid&&position<=3;\n    if(!changed&&!restarted){\n      resetSystemPosition();\n      if(now-media.skipStarted>2000)systemPositionError(\'Could not confirm the new track position. Marker held at 0:00.\');\n      return\n    }\n    media.skipPending=false;media.skipWarned=false\n  }\n  media.trackKey=key;\n  if(!valid){\n    resetSystemPosition();\n    if(data.available)systemPositionError(\'System media position is unavailable or invalid. Marker held at 0:00.\');\n  }else{\n    media.skipWarned=false;\n    media.clockPosition=Math.min(position,duration);\n    media.clockAt=now;\n    media.clockPlaying=!!data.playing;\n    renderTimeline(media,media.clockPosition,duration)\n  }\n  setSystemAvailability(data);\n  if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1)media.volumePending=null;\n  if(media.volumePending===null&&now>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){\n    displayMasterVolume(data.volume)\n  }\n  setPlayIcon($(\'#system-play-shape\'),$(\'#system-toggle\'),!!data.playing)\n}\nfunction tickSystem(){\n  const media=state.system;\n  if(document.hidden||media.skipPending||!media.clockPlaying||media.dragging||!media.clockAt||media.duration<=0)return;\n  renderTimeline(media,Math.min(media.duration,media.clockPosition+(performance.now()-media.clockAt)/1000),media.duration)\n}\nasync function pollSystem(force=false){\n  const media=state.system;\n  if(media.polling){if(force)media.repoll=true;return}\n  media.polling=true;\n  try{renderSystem(await api(\'/api/system-media\'))}\n  catch(error){resetSystemPosition();systemPositionError(\'System media update failed: \'+(error?.message||String(error)))}\n  finally{media.polling=false;if(media.repoll){media.repoll=false;pollSystem()}}\n}\nasync function pollLocal(){const media=state.local;if(media.polling)return;media.polling=true;try{if(!$(\'#pc\').classList.contains(\'hidden\')){const data=await api(\'/api/player\');$(\'#now-title\').textContent=data.title||\'Nothing playing\';updateLocalNowArtwork(data.cover||\'\');$(\'#now-details\').textContent=[data.artist,data.album].filter(Boolean).join(\' • \')||(data.path||\'\');renderTimeline(media,data.currentTime,data.duration);if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1){media.volumePending=null}if(media.volumePending===null&&performance.now()>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){displayMasterVolume(data.volume)}$(\'#shuffle\').disabled=!data.canShuffleQueue;$(\'#repeat\').textContent=\'Repeat \'+({off:\'Off\',all:\'All\',one:\'1\'}[data.repeat]||\'Off\');$(\'#repeat\').classList.toggle(\'active\',data.repeat!==\'off\');setPlayIcon($(\'#local-play-shape\'),document.querySelector(\'[data-cmd="toggle"]\'),data.playing)}}catch(error){note(error.message,true)}finally{media.polling=false}}\n\nfunction bindSeek(media,url){\n  const slider=media.slider;\n  const begin=()=>{media.dragging=true;slider.classList.add(\'dragging\')};\n  const end=()=>{media.dragging=false;slider.classList.remove(\'dragging\')};\n  slider.addEventListener(\'pointerdown\',begin);\n  slider.addEventListener(\'pointercancel\',end);\n  slider.addEventListener(\'touchstart\',begin,{passive:true});\n  slider.oninput=()=>{media.dragging=true;const value=Number(slider.value);fill(slider,value,media.duration);media.elapsed.textContent=fmt(value)};\n  slider.onchange=async()=>{const target=Math.max(0,Number(slider.value)||0);end();try{await post(url,{seconds:target});if(media===state.system)await pollSystem(true)}catch(error){note(error.message,true);if(media===state.system)await pollSystem(true)}};\n}\nfunction displayMasterVolume(value,origin=null){\n  const v=Number(value);\n  if(!Number.isFinite(v))return;\n  for(const media of [state.system,state.local]){\n    if(media!==origin && (document.activeElement===media.volume || media.volumePending!==null))continue;\n    media.volume.value=String(v);fill(media.volume,v,100)\n  }\n}\nlet volumeWriteQueue=Promise.resolve();\nfunction bindVolume(media,url){\n  const control=media.volume;\n  const send=()=>{\n    const value=Math.max(0,Math.min(100,Number(control.value)||0));\n    for(const item of [state.system,state.local]){\n      item.volumeHold=performance.now()+1500;item.volumePending=value;\n    }\n    for(const item of [state.system,state.local]){item.volume.value=String(value);fill(item.volume,value,100)}\n    volumeWriteQueue=volumeWriteQueue.catch(()=>{}).then(()=>post(url,{volume:value}));\n    volumeWriteQueue.then(result=>{\n      const actual=Number(result.volume);\n      if(Number.isFinite(actual)){\n        for(const item of [state.system,state.local])item.volumePending=null;\n        displayMasterVolume(actual)\n      }\n    }).catch(error=>{\n      for(const item of [state.system,state.local])item.volumePending=null;\n      note(error.message,true);pollSystem(true);pollLocal()\n    })\n  };\n  control.oninput=()=>{\n    const value=Number(control.value);\n    media.volumeHold=performance.now()+1500;\n    for(const item of [state.system,state.local]){item.volume.value=String(value);fill(item.volume,value,100)}\n    clearTimeout(media.volumeTimer);media.volumeTimer=setTimeout(send,90)\n  };\n  control.onchange=()=>{clearTimeout(media.volumeTimer);send()}\n}\n\nfunction coverNode(url,large=false){const node=document.createElement(url?\'img\':\'div\');node.className=\'cover-art\'+(large?\' large\':\'\')+(url?\'\':\' cover-fallback\');if(url){node.src=url;node.loading=large?\'eager\':\'lazy\';node.alt=\'Album cover\';node.onerror=()=>{const fallback=coverNode(\'\',large);fallback.id=node.id;fallback.dataset.failedCover=url;node.replaceWith(fallback)}}else{node.textContent=\'♫\';node.setAttribute(\'aria-label\',\'No album art\')}return node}\nlet localNowArtworkURL=\'\';\nfunction updateLocalNowArtwork(url){const next=url||\'\';if(next===localNowArtworkURL)return;localNowArtworkURL=next;const tile=$(\'#now-cover\');tile.replaceChildren();if(!next)return;const image=document.createElement(\'img\');image.alt=\'\';image.decoding=\'async\';image.addEventListener(\'error\',()=>image.remove(),{once:true});image.src=next;tile.appendChild(image)}\nfunction updateCover(id,url){const current=$(\'#\'+id);if(!current)return;if(url&&((current.tagName===\'IMG\'&&current.getAttribute(\'src\')===url)||current.dataset.failedCover===url))return;if(!url&&current.classList.contains(\'cover-fallback\'))return;const next=coverNode(url,true);next.id=id;current.replaceWith(next)}\nfunction render(sel,data,query){const list=$(sel);list.replaceChildren();const term=query.toLowerCase();const filtered=data.filter(song=>!term||[song.title,song.artist,song.album,song.relative].join(\' \').toLowerCase().includes(term));if(!filtered.length){const empty=document.createElement(\'div\');empty.className=\'card details\';empty.textContent=query?\'No matches\':\'Nothing here yet\';list.append(empty);return}const fragment=document.createDocumentFragment();for(const song of filtered){const button=document.createElement(\'button\');button.className=\'item\';button.innerHTML=\'<span class="name"></span><span class="meta"></span>\';button.children[0].textContent=song.title;button.children[1].textContent=[song.artist,song.album].filter(Boolean).join(\' • \')||song.relative;const wrap=document.createElement(\'div\');wrap.className=\'item-cover\';const copy=document.createElement(\'div\');copy.className=\'copy\';copy.append(...button.children);wrap.append(coverNode(song.cover),copy);button.append(wrap);button.onclick=async()=>{try{await busy(button,()=>post(\'/api/play/song\',{path:song.path}),\'Playing…\');note(\'Playing \'+song.title);await refreshVolatile()}catch(error){note(error.message,true)}};fragment.append(button)}list.append(fragment)}\nconst MODE_LABELS={ipad_ipad:\'External\',laptop_laptop:\'Laptop\',ipad_laptop:\'Laptop\',ipad_both:\'Both\',laptop_ipad:\'External\',laptop_both:\'Both\'};const LOCAL_MODES=new Set([\'ipad_laptop\',\'ipad_both\',\'laptop_laptop\',\'laptop_both\']);let pendingMode=null,outputTopology=null,lastOutputRefresh=0,outputProfileBusy=false;const outputCard=$(\'#output-card\'),outputProfile=$(\'#output-profile\'),outputRoute=$(\'#output-route\'),outputSink=$(\'#output-sink\');function selectedCard(){return outputTopology?.cards.find(item=>item.name===outputCard.value)}function fillRoutes(){\n  const card=selectedCard(),profile=Number(outputProfile.value);\n  const priorRoute=Number(outputProfile.value)===card?.activeProfile?outputRoute.value:\'\';outputRoute.replaceChildren();\n  for(const route of card?.routes||[]){\n    if(route.profiles?.length&&!route.profiles.includes(profile))continue;\n    const option=new Option(route.label+(route.available===\'no\'?\' (unavailable)\':\'\'),String(route.index));\n    option.disabled=route.available===\'no\';outputRoute.add(option)\n  }\n  const active=card?.activeRoutes?.map(String)||[];const preferred=[priorRoute,...active].find(value=>[...outputRoute.options].some(option=>option.value===value&&!option.disabled));if(preferred!==undefined)outputRoute.value=preferred;else outputRoute.selectedIndex=-1;\n  fillSinks();\n}\nfunction fillSinks(){\n  const card=selectedCard(),route=card?.routes.find(item=>String(item.index)===outputRoute.value);\n  const devices=(route?.devices||[]).map(String),prior=outputSink.value;\n  outputSink.replaceChildren();\n  if(Number(outputProfile.value)===card?.activeProfile){\n    for(const sink of card.sinks||[]){\n      if(devices.length&&sink.profileDevice!=null&&!devices.includes(String(sink.profileDevice)))continue;\n      outputSink.add(new Option(sink.label,sink.name));\n    }\n  }\n  if([...outputSink.options].some(option=>option.value===prior))outputSink.value=prior;\n  else if([...outputSink.options].some(option=>option.value===outputTopology?.saved?.sink))outputSink.value=outputTopology.saved.sink;\n}\nfunction fillProfiles(){\n  const card=selectedCard();outputProfile.replaceChildren();\n  for(const profile of card?.profiles||[]){\n    const option=new Option(profile.label+(profile.available===\'no\'?\' (unavailable)\':\'\'),String(profile.index));\n    option.disabled=profile.available===\'no\';outputProfile.add(option)\n  }\n  if(card?.activeProfile!==null&&card?.activeProfile!==undefined)outputProfile.value=String(card.activeProfile);\n  fillRoutes()\n}\nasync function refreshDevicePicker(cardName){\n  outputTopology=await api(\'/api/outputs\');lastOutputRefresh=performance.now();\n  outputCard.replaceChildren(...outputTopology.cards.map(item=>new Option(item.label,item.name)));\n  outputCard.value=cardName;fillProfiles()\n}\nasync function applyMode(mode,output=null){const b=document.querySelector(`[data-mode="${mode}"]`);try{await busy(b,()=>post(\'/api/mode\',{mode,password:$(\'#group-password\').value,output}),\'Applying audio route…\',\'Audio route activated\',true);await refreshAll()}catch(error){note(error.message,true)}}\n\nasync function chooseOutput(mode){pendingMode=mode;outputTopology=await api(\'/api/outputs\');lastOutputRefresh=performance.now();const saved=outputTopology.saved||{};outputCard.replaceChildren(...outputTopology.cards.map(item=>new Option(item.label,item.name)));if(!outputTopology.cards.length)throw Error(\'No playback cards available\');outputCard.value=outputTopology.cards.some(item=>item.name===saved.card)?saved.card:outputCard.value;fillProfiles();$(\'#output-mode-title\').textContent=MODE_LABELS[mode];$(\'#output-modal\').classList.remove(\'hidden\')}\nasync function commitOutput(){\n  if(outputProfileBusy||!pendingMode)return;\n  const card=selectedCard(),profile=outputProfile.value;\n  if(!card)throw Error(\'Choose a playback device\');\n  \n  \n  const output={card:card.name,profile:outputProfile.value,sink:outputSink.value,port:outputRoute.value};\n  outputProfileBusy=true;\n  try{await busy($(\'#output-apply\'),()=>post(\'/api/output-activate\',{output,mode:pendingMode,password:$(\'#group-password\').value}), \'Applying audio route…\',\'Audio route activated\',true);\n    $(\'#output-modal\').classList.add(\'hidden\');pendingMode=null;await refreshAll()\n  }catch(error){note(error.message,true)}finally{outputProfileBusy=false}\n}\noutputCard.onchange=async()=>{\n  if(outputProfileBusy)return;\n  fillProfiles();\n};\noutputProfile.onchange=()=>fillRoutes();\noutputRoute.onchange=()=>fillSinks();\n$(\'#output-apply\').onclick=()=>commitOutput();\nfor(const [source,items] of [[\'External\',[\'ipad_ipad\',\'ipad_laptop\',\'ipad_both\']],[\'Laptop\',[\'laptop_ipad\',\'laptop_laptop\',\'laptop_both\']]]){const heading=document.createElement(\'div\');heading.className=\'source-heading\';heading.textContent=\'Playing from \'+source;$(\'#mode-grid\').append(heading);for(const key of items){const b=document.createElement(\'button\');b.className=\'action\';b.dataset.mode=key;b.textContent=MODE_LABELS[key];b.onclick=async()=>{try{if(LOCAL_MODES.has(key))await chooseOutput(key);else await applyMode(key)}catch(error){note(error.message,true)}};$(\'#mode-grid\').append(b)}}$(\'#output-cancel\').onclick=()=>{if(outputProfileBusy)return;$(\'#output-modal\').classList.add(\'hidden\');pendingMode=null};function loadGroups(data){const state=data.groups,select=$(\'#group-select\');select.replaceChildren(...Object.entries(state.profiles).map(([key,g])=>new Option(key+\' • \'+g.group,key,key===state.active,key===state.active)));const show=()=>{const key=select.value||state.active,g=state.profiles[key];if(!g)return;$(\'#group-key\').value=key;$(\'#group-name\').value=g.group;$(\'#group-user\').value=g.username;$(\'#group-server\').value=g.server;$(\'#group-required\').checked=!!g.passwordRequired};select.onchange=show;show()}$(\'#save-group\').onclick=async()=>{try{await busy($(\'#save-group\'),()=>post(\'/api/groups/save\',{key:$(\'#group-key\').value,group:$(\'#group-name\').value,username:$(\'#group-user\').value,server:$(\'#group-server\').value,passwordRequired:$(\'#group-required\').checked}),\'Saving group…\',null,false);await refreshAll();note(\'Group profile saved\')}catch(error){note(error.message,true)}};$(\'#open-pc\').onclick=async()=>{try{show(\'pc\');await refreshStatic()}catch(error){note(error.message,true)}};\n$(\'#away-display-toggle\').onclick=async()=>{try{await busy($(\'#away-display-toggle\'),()=>post(\'/api/away-display\'),\'Switching away/display…\',null,false);await refreshVolatile()}catch(error){note(error.message,true)}};\n$(\'#pc-back\').onclick=()=>show(\'profiles\');\n$(\'#all-songs\').onclick=async()=>{try{all=(await api(\'/api/songs\')).songs;show(\'songs\');render(\'#song-list\',all,\'\');}catch(error){note(error.message,true)}};\n$(\'#song-search\').oninput=()=>render(\'#song-list\',all,$(\'#song-search\').value);\n$(\'#playlist-search\').oninput=()=>render(\'#playlist-songs\',inside,$(\'#playlist-search\').value);\ndocument.querySelectorAll(\'[data-back]\').forEach(button=>button.onclick=()=>show(button.dataset.back));\ndocument.querySelectorAll(\'[data-cmd]\').forEach(button=>button.onclick=async()=>{try{await busy(button,()=>post(\'/api/player/command\',{command:button.dataset.cmd}),\'…\',null,false);await pollLocal()}catch(error){note(error.message,true)}});\n$(\'#repeat\').onclick=async()=>{try{const data=await api(\'/api/player\');await post(\'/api/player/repeat\',{mode:{off:\'all\',all:\'one\',one:\'off\'}[data.repeat]||\'off\'});await pollLocal()}catch(error){note(error.message,true)}};\n$(\'#shuffle\').onclick=async()=>{try{await busy($(\'#shuffle\'),()=>post(\'/api/player/shuffle\'),\'Updating saved shuffle…\',\'Saved shuffle updated for next play\',true)}catch(error){note(error.message,true)}};\nfor(const [id,command] of [[\'system-previous\',\'previous\'],[\'system-toggle\',\'toggle\'],[\'system-next\',\'next\']])$(\'#\'+id).onclick=async()=>{if(command!==\'toggle\'){const media=state.system;media.skipPending=true;media.skipFrom=media.trackKey;media.skipStarted=performance.now();media.skipWarned=false;resetSystemPosition()}else{state.system.skipPending=false}try{await busy($(\'#\'+id),()=>post(\'/api/system-media\',{command}),\'…\',null,false);await pollSystem(true)}catch(error){if(command!==\'toggle\')systemPositionError(\'System media skip failed: \'+(error?.message||String(error)));else note(error.message,true)}};\n$(\'#audio-toggle\').onclick=async()=>{try{await busy($(\'#audio-toggle\'),()=>post(\'/api/audio-toggle\'),\'Switching audio services…\',\'Audio services updated\',true);await refreshStatic()}catch(error){note(error.message,true)}};\n$(\'#restart-camilla\').onclick=()=>busy($(\'#restart-camilla\'),()=>post(\'/api/restart-camilladsp\'),\'Restarting CamillaDSP…\',\'CamillaDSP restarted\',true).then(refreshStatic).catch(error=>note(error.message,true));\n$(\'#restart-sonobus\').onclick=()=>busy($(\'#restart-sonobus\'),()=>post(\'/api/restart-sonobus\'),\'Restarting SonoBus…\',\'SonoBus restarted\',true).then(refreshVolatile).catch(error=>note(error.message,true));\n$(\'#restart-airplay\').onclick=()=>busy($(\'#restart-airplay\'),()=>post(\'/api/restart-airplay\'),\'Restarting AirPlay…\',\'AirPlay restarted\',true).then(refreshVolatile).catch(error=>note(error.message,true));\n$(\'#restart-vnc\').onclick=()=>busy($(\'#restart-vnc\'),()=>post(\'/api/restart-vnc\'),\'Restarting VNC…\',\'VNC restarted\').catch(error=>note(error.message,true));\n$(\'#profile-search\').oninput=()=>{if(profileSnapshot)renderProfileSnapshot(profileSnapshot);else refreshStatic()};\nbindSeek(state.local,\'/api/player/seek\');bindSeek(state.system,\'/api/system-media/seek\');bindVolume(state.local,\'/api/player/volume\');bindVolume(state.system,\'/api/system-volume\');\nlet staticRefreshRunning=false,volatileRefreshRunning=false,profileSnapshot=null;\nfunction renderProfileSnapshot(data){\n  const query=$(\'#profile-search\').value.toLowerCase(),root=$(\'#profile-list\');\n  renderActiveProfile(data);root.replaceChildren();\n  const groups=new Map();\n  for(const name of data.profiles||[]){\n    const info=data.filters?.[name]||{group:\'Other\',label:name};\n    if(![name,info.group,info.label].some(value=>value.toLowerCase().includes(query)))continue;\n    if(!groups.has(info.group))groups.set(info.group,[]);\n    groups.get(info.group).push({name,label:info.label});\n  }\n  for(const [group,items] of groups){\n    const heading=document.createElement(\'div\');heading.className=\'section-title\';heading.textContent=group;root.append(heading);\n    const list=document.createElement(\'div\');list.className=\'list\';\n    for(const {name,label} of items){\n      const button=document.createElement(\'button\');\n      button.className=\'item\'+(name===data.active?\' active\':\'\');\n      const title=document.createElement(\'span\');title.className=\'name\';title.textContent=label;\n      button.append(title);\n      button.onclick=async()=>{try{await busy(button,()=>post(\'/api/select\',{profile:name}),\'Switching profile…\',\'Profile activated\',true);await refreshStatic()}catch(error){note(error.message,true)}};\n      list.append(button);\n    }\n    root.append(list);\n  }\n}\nfunction playlistAction(playlist,shuffle){\n  const button=document.createElement(\'button\');button.type=\'button\';button.className=\'playlist-icon\';\n  button.setAttribute(\'aria-label\',(shuffle?\'Play saved shuffle of \':\'Play \')+playlist.name);\n  button.title=(shuffle?\'Play saved shuffle\':\'Play playlist\')+\' · \'+playlist.name;\n  button.innerHTML=shuffle\n    ? \'<svg class="media-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h3c5 0 7 10 12 10h3m-4-4 4 4-4 4M3 17h3c2 0 3-1 4-3m3-4c1-2 2-3 5-3h3m-4-4 4 4-4 4"/></svg>\'\n    : \'<svg class="media-icon" viewBox="0 0 24 24" aria-hidden="true"><path class="icon-fill" d="M8 5.5 19 12 8 18.5z"/></svg>\';\n  button.onclick=()=>busy(button,()=>post(\'/api/play/playlist\',{name:playlist.name,shuffle}),\'…\',null,false).then(refreshVolatile).catch(error=>note(error.message,true));\n  return button\n}\nfunction renderPlaylistSnapshot(playlists){\n  const list=$(\'#playlists\');list.replaceChildren();\n  for(const playlist of playlists){\n    const row=document.createElement(\'div\');row.className=\'playlist-row\';\n    const open=document.createElement(\'button\');open.className=\'item playlist-open\';open.dataset.playlistName=playlist.name;open.innerHTML=\'<span class="name"></span><span class="meta"></span>\';open.children[0].textContent=playlist.name;open.children[1].textContent=playlist.count+(playlist.count===1?\' song\':\' songs\');const wrap=document.createElement(\'div\');wrap.className=\'item-cover\';const copy=document.createElement(\'div\');copy.className=\'copy\';copy.append(...open.children);wrap.append(coverNode(playlist.cover),copy);open.append(wrap);\n    open.onclick=async()=>{current=playlist.name;inside=(await api(\'/api/playlist?name=\'+encodeURIComponent(playlist.name))).songs;$(\'#playlist-title\').textContent=playlist.name;show(\'playlist\');render(\'#playlist-songs\',inside,\'\')};\n    const controls=document.createElement(\'div\');controls.className=\'playlist-controls\';\n    const play=playlistAction(playlist,false),shuffle=playlistAction(playlist,true);\n    controls.append(play,shuffle);row.append(open,controls);list.append(row)\n  }\n}\nfunction updatePlaylistCover(player){if(!player?.playlist||!player.playing)return;for(const button of document.querySelectorAll(\'#playlists [data-playlist-name]\')){if(button.dataset.playlistName!==player.playlist)continue;const old=button.querySelector(\'.item-cover .cover-art\');if(!old)continue;const url=player.cover||\'\';if(url&&old.tagName===\'IMG\'&&old.getAttribute(\'src\')===url)return;if(!url&&old.classList.contains(\'cover-fallback\'))return;old.replaceWith(coverNode(url))}}\nfunction renderVolatile(data){\n  $(\'#audio-toggle\').textContent=data.mode.mode===\'stopped\'?\'Start all audio\':\'Stop all audio\';\n  if(data.awayDisplay){const button=$(\'#away-display-toggle\');button.textContent=data.awayDisplay.displayOff?\'Show lock screen\':data.awayDisplay.away?\'Unlock desktop\':\'Away and display off\';button.setAttribute(\'aria-pressed\',String(!!data.awayDisplay.active));button.classList.toggle(\'mode-active\',!!data.awayDisplay.active)}\n  if(data.profiles)renderActiveProfile(data.profiles);\n  renderSonobusStatus(data.mode,data.groups);\n  $(\'#engine-status\').textContent=data.mode.camilla?\'Audio engine online\':\'Audio engine offline\';$(\'.dot\').style.opacity=data.mode.camilla?\'1\':\'.25\';\n  $(\'#source-title\').textContent=({ipad_external:\'External\',laptop_external:\'Laptop\',ipad_ipad:\'External\',ipad_laptop:\'Laptop\',ipad_both:\'Both\',laptop_ipad:\'External\',laptop_laptop:\'Laptop\',laptop_both:\'Both\'})[data.mode.mode]||data.mode.label;$(\'#source-details\').textContent=\'CamillaDSP \'+(data.mode.camilla?\'running\':\'stopped\')+\' • SonoBus \'+(data.mode.sonobus?\'on\':\'off\')+\' • AirPlay \'+(data.mode.airplay?\'on\':\'off\')+(data.mode.localWanted?\' • Output \'+(data.mode.localOutputLabel||\'Not selected\'):\'\');\n  document.querySelectorAll(\'[data-mode]\').forEach(button=>button.classList.toggle(\'mode-active\',button.dataset.mode===data.mode.mode));\n  updatePlaylistCover(data.player);\n  const local=data.player,lm=state.local;$(\'#now-title\').textContent=local.title||\'Nothing playing\';updateLocalNowArtwork(local.cover||\'\');$(\'#now-details\').textContent=[local.artist,local.album].filter(Boolean).join(\' • \')||(local.path||\'\');renderTimeline(lm,local.currentTime,local.duration);if(lm.volumePending===null&&document.activeElement!==lm.volume){displayMasterVolume(local.volume)}$(\'#shuffle\').disabled=!local.canShuffleQueue;$(\'#repeat\').textContent=\'Repeat \'+({off:\'Off\',all:\'All\',one:\'1\'}[local.repeat]||\'Off\');$(\'#repeat\').classList.toggle(\'active\',local.repeat!==\'off\');setPlayIcon($(\'#local-play-shape\'),document.querySelector(\'[data-cmd="toggle"]\'),local.playing)\n}\nasync function refreshStatic(){if(staticRefreshRunning)return;staticRefreshRunning=true;try{const data=await api(\'/api/state\');profileSnapshot=data.profiles;renderProfileSnapshot(data.profiles);renderPlaylistSnapshot(data.playlists);loadGroups(data);renderVolatile(data);renderSystem(data.systemMedia);return data}catch(error){note(error.message,true)}finally{staticRefreshRunning=false}}\nasync function refreshOutputPicker(){if($(\'#output-modal\').classList.contains(\'hidden\')||!pendingMode||outputProfileBusy||outputProfile.disabled||performance.now()-lastOutputRefresh<5000)return;const cardValue=outputCard.value,profileValue=outputProfile.value,routeValue=outputRoute.value,sinkValue=outputSink.value;try{const fresh=await api(\'/api/outputs\');lastOutputRefresh=performance.now();outputTopology=fresh;outputCard.replaceChildren(...fresh.cards.map(item=>new Option(item.label,item.name)));if(!fresh.cards.length){outputProfile.replaceChildren();outputRoute.replaceChildren();return}outputCard.value=fresh.cards.some(item=>item.name===cardValue)?cardValue:outputCard.value;fillProfiles();if([...outputProfile.options].some(option=>option.value===profileValue&&!option.disabled))outputProfile.value=profileValue;fillRoutes();if([...outputRoute.options].some(option=>option.value===routeValue&&!option.disabled))outputRoute.value=routeValue;fillSinks();if([...outputSink.options].some(option=>option.value===sinkValue))outputSink.value=sinkValue}catch(error){console.warn(\'output picker refresh failed\',error)}}\nasync function refreshVolatile(){if(volatileRefreshRunning||document.hidden)return;volatileRefreshRunning=true;try{renderVolatile(await api(\'/api/volatile\'));await refreshOutputPicker()}catch(error){console.warn(\'volatile refresh failed\',error)}finally{volatileRefreshRunning=false}}\nrefreshAll=async()=>refreshStatic();\nrefreshStatic();window.addEventListener(\'pageshow\',event=>{if(event.persisted){refreshStatic();refreshVolatile()}});document.addEventListener(\'visibilitychange\',()=>{if(!document.hidden){refreshStatic();refreshVolatile()}});setInterval(refreshVolatile,1000);setInterval(()=>{if(!document.hidden)pollSystem()},1000);setInterval(tickSystem,250);\n</script></body></html>'
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
            elif p=='/api/profiles':r={'profiles':[NO_FILTER]+[x.name for x in profiles()],'filters':listening_filters(),'active':active(),'running':alive(rpid(CAMPID),'camilladsp')}
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
            if p=='/api/output-activate':r=activate_output(d.get('output'),mode=d.get('mode'),password=d.get('password'))
            elif p=='/api/away-display':r=toggle_away_display()
            elif p=='/api/mode':r=set_mode(d.get('mode'),d.get('password'),d.get('output'))
            elif p=='/api/groups/save':r=save_group(d)
            elif p=='/api/select':r=switch_profile(d.get('profile'))
            elif p=='/api/audio-toggle':r=toggle_audio_services()
            elif p=='/api/restart-camilladsp':r=restart_camilla()
            elif p=='/api/restart-sonobus':r=restart_sonobus_action(d.get('password'))
            elif p=='/api/restart-airplay':r=restart_airplay()
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

def start_runtime(force=False):
    global MASTER_RESTORING
    MASTER_RESTORING=True
    ensure()
    (STATE/'output-preview').unlink(missing_ok=True)
    if STOPPED.exists() and not force:
        MODE.write_text('laptop_laptop\n')
        run(['systemctl','--user','stop',SYSTEM_AUDIO_SERVICE],False,10)
        run(['systemctl','stop','shairport-sync.service','nqptp.service'],False,10)
        return
    for unit in ('pipewire.socket','pipewire-pulse.socket','wireplumber.service'):
        user_service('start',unit)
    for _ in range(100):
        if run([exe('wpctl'),'status'],False,3,media_env()).returncode==0:break
        time.sleep(.1)
    else:raise RuntimeError('PipeWire did not become ready')
    startup_output=None
    try:
        initial=audio_topology()
        default=pw_default()
        startup_output=next((sink for card in initial['cards'] for sink in card['sinks'] if sink['name']==default),None)
    except (RuntimeError,ValueError,OSError):pass
    alsa100()
    # Keep persisted master until the route has been restored; a newly
    # created default sink may temporarily report 100%.
    available=profiles()
    saved=ACTIVE.read_text().strip() if ACTIVE.is_file() else '';selected=None
    if not available and saved!=NO_FILTER:raise RuntimeError('No CamillaDSP profiles found in '+str(PROFILES))
    if saved and saved!=NO_FILTER:
        try:selected=profile(saved)
        except (ValueError,FileNotFoundError):pass
    if selected is None and saved!=NO_FILTER:
        selected=available[0]
    # Set the startup mode before any bypass, engine or source decisions.
    mode='laptop_laptop'
    MODE.write_text(mode+'\n')
    # Reuse a verified surviving engine; never start a competing instance.
    if saved==NO_FILTER:
        stop_camilla(include_stale=True)
        start_bypass()
    elif not alive(rpid(CAMPID),'camilladsp'):
        stop_camilla(include_stale=True)
        start_camilla(selected)
    if selected_filter()!=NO_FILTER:
        wait_for_camilla_config(profile(selected_filter()))
    saved_route=saved_output_route()
    if startup_output:
        # A physical default selected before webremote starts wins over stale
        # webremote state. Virtual CamillaDSP defaults are excluded above.
        saved_route={'card':startup_output['cardName'], 'profile':'',
                     'sink':startup_output['name'], 'port':''}
    if saved_route['card'] and saved_route['profile']:
        try:
            set_output_profile(saved_route['card'],saved_route['profile'])
            if saved_route['port']:
                set_output_route(saved_route['card'],saved_route['port'])
        except (RuntimeError,ValueError,OSError) as error:
            print('Saved output pending: '+str(error),flush=True)
    try:
        apply_mode(mode,restore_camilla=False,normalize=False,
                   output=choose_output_route(dict(saved_route,_remembered=True)) if saved_route.get('card') else None)
    except RuntimeError as error:
        if not any(message in str(error) for message in ('exposes no available physical sink','Select a physical playback output','No physical playback device in PipeWire graph','Playback device unavailable:','Playback sink unavailable:')):raise
        label,source,local,sono=MODES[mode]
        apply_source_services(source);stop_local_monitor()
        if sono:restart_sonobus(None,MODE_POLICIES[mode],normalize=False)
        else:stop_sonobus()
        MODE.write_text(mode+'\n')
    except ValueError as error:
        # A saved password-protected group cannot be joined unattended. Keep
        # CamillaDSP and the web UI alive so the password can be entered there.
        if 'requires a password' not in str(error):raise
        label,source,local,sono=MODES[mode]
        apply_source_services(source)
        if local:start_local_monitor()
        else:stop_local_monitor()
        stop_sonobus();MODE.write_text(mode+'\n')
    if MODES[audio_mode()][1]=='system' and QUEUE_FILE.is_file():
        try:ensure_mpv()
        except (OSError,RuntimeError):pass
    if selected_filter()!=NO_FILTER:
        wait_for_camilla_config(profile(selected_filter()))
    try:
        apply_master_volume(master_volume(),normalize_sinks=False)
    except RuntimeError as error:
        if not alive(rpid(LOCALMONPID)) and any(x in str(error) for x in ('No playback sink','Could not identify the current default sink','Playback sink unavailable','Audio node unavailable')):
            print('Saved master pending: '+str(error),flush=True)
        else:raise
    MASTER_RESTORING=False
def stop_audio_services(mark_stopped=True,stop_pipewire=True):
    global MASTER_RESTORING
    # Capture the last live value before stopping PipeWire, then prevent
    # status polling from saving a recreated sink's 100% default.
    with LOCK:
        pause_for_audio_stop()
        if not MASTER_RESTORING and not STOPPED.exists():master_volume()
        MASTER_RESTORING=True
        if mark_stopped:STOPPED.touch()
        STOP_ACK.unlink(missing_ok=True)
        try:
            stop_local_monitor()
            stop_mpv()
            stop_sonobus()
            stop_bypass()
            stop_camilla(include_stale=True)
            user_service('stop',SYSTEM_AUDIO_SERVICE)
            airplay(False)
            if stop_pipewire:
                for unit in ('wireplumber.service','pipewire-pulse.service','pipewire.service',
                             'pipewire-pulse.socket','pipewire.socket'):
                    user_service('stop',unit)
        finally:
            if mark_stopped:STOP_ACK.touch()
def release_audio_for_output_switch():
    # Transfer ownership without stopping PipeWire or marking the whole system stopped.
    with LOCK:
        STOP_ACK.unlink(missing_ok=True)
        try:
            stop_local_monitor();stop_mpv();stop_sonobus();stop_bypass();stop_camilla(include_stale=True)
            user_service('stop',SYSTEM_AUDIO_SERVICE)
            airplay(False)
        except Exception as error:
            (STATE/'audio-start-error').write_text(str(error)+'\n')
        finally:STOP_ACK.touch()
def toggle_audio_services():
    with LOCK:
        if STOPPED.exists():
            with audio_start_change():
                start_runtime(force=True)
                STOPPED.unlink(missing_ok=True)
        else:
            stop_audio_services()
        return {'stopped':STOPPED.exists()}
def cleanup():
    # Exit is a full teardown too. Do not turn an ordinary Ctrl+C into a
    # persistent explicit-stop request: the next server launch restores audio.
    stop_audio_services(mark_stopped=False)
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
    startup_lock=(SWITCH_STATE/'media-control.lock').open('a+')
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
        if alive(old):raise RuntimeError('Previous webremote instance did not complete audio shutdown; refusing unsafe replacement')
    SERVERPID.unlink(missing_ok=True)
    if alive(rpid(BYPASSPID)):stop_bypass()
    # First replacement of an older server may leave its tracked engine alive.
    tracked=rpid(CAMPID)
    if tracked and reusable_camilla(tracked):
        stop_local_monitor();stop_camilla(include_stale=True)
    # A surviving engine may be reused only if it is the tracked engine and
    # reports the selected YAML. Every active startup still reapplies local mode.
    running=other_camilla_processes()
    if running:
        if len(running)!=1:
            raise RuntimeError('Multiple CamillaDSP engines found; refusing unsafe startup')
        verified=reusable_camilla(running[0])
        if not verified or selected_filter()==NO_FILTER:
            raise RuntimeError('Unverified CamillaDSP engine is running; refusing unsafe startup')
        if rpid(CAMPID)!=running[0]:
            CAMPID.write_text(str(running[0])+'\n')
        if selected_filter()!=verified.name:
            raise RuntimeError('Surviving CamillaDSP config differs from selected profile; refusing unsafe startup')
    LOCAL_ENGINE_OWNED=False
    watcher_stop=threading.Event()
    threading.Thread(target=watch_sonobus_workspace,args=(watcher_stop,),daemon=True).start()
    if STOPPED.exists():
        start_runtime()
    else:
        with media_change():
            start_runtime()
    threading.Thread(target=watch_master_volume,args=(watcher_stop,),daemon=True).start()
    server=S(('0.0.0.0',PORT),H);SERVERPID.write_text(str(me)+'\n');STOP_CAP.write_text(str(me)+'\n')
    fcntl.flock(startup_lock,fcntl.LOCK_UN)
    startup_lock.close()
    def shut(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shut);signal.signal(signal.SIGHUP,shut)
    signal.signal(signal.SIGUSR1,lambda *_:threading.Thread(target=release_audio_for_output_switch,daemon=True).start())
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
        if len(sys.argv)==2 and sys.argv[1]=='--audio-toggle':
            ensure()
            server=rpid(SERVERPID)
            if alive(server) and server!=os.getpid():
                from urllib.request import Request,urlopen
                request=Request('http://127.0.0.1:'+str(PORT)+'/api/audio-toggle',data=b'{}',
                                headers={'Content-Type':'application/json'},method='POST')
                with urlopen(request,timeout=90) as response:data=json.load(response)
                if not data.get('ok'):raise RuntimeError(data.get('error') or 'Audio toggle failed')
                print(json.dumps({'stopped':data['stopped']}))
            else:print(json.dumps(toggle_audio_services()))
            return
        if len(sys.argv)==3 and sys.argv[1]=='--restart-service':
            name=sys.argv[2]
            paths={'1':'/api/restart-camilladsp','2':'/api/restart-sonobus',
                   '3':'/api/restart-airplay','4':'/api/restart-vnc'}
            if name not in paths:raise ValueError('Choose restart 1, 2, 3 or 4')
            if not alive(rpid(SERVERPID)):
                if name=='1':result=restart_camilla()
                elif name=='2':result=restart_sonobus_action()
                elif name=='3':result=restart_airplay()
                else:result=restart_vnc()
                print(json.dumps(result));return
            from urllib.request import Request,urlopen
            request=Request('http://127.0.0.1:'+str(PORT)+paths[name],data=b'{}',
                            headers={'Content-Type':'application/json'},method='POST')
            with urlopen(request,timeout=40) as response:data=json.load(response)
            if not data.get('ok'):raise RuntimeError(data.get('error') or 'Restart failed')
            print(json.dumps(data));return
        if len(sys.argv)==2 and sys.argv[1]=='--toggle-away-display':
            print(json.dumps(toggle_away_display()));return
        if len(sys.argv)==2 and sys.argv[1]=='--away-display-status':
            print(json.dumps(away_display_state()));return
        if len(sys.argv)==3 and sys.argv[1]=='--select-filter':
            server=rpid(SERVERPID)
            if not alive(server) or server==os.getpid():
                raise RuntimeError('Webremote must be running for filter selection')
            from urllib.request import Request,urlopen
            from urllib.error import HTTPError
            request=Request('http://127.0.0.1:'+str(PORT)+'/api/select',
                            data=json.dumps({'profile':sys.argv[2]}).encode(),
                            headers={'Content-Type':'application/json'},method='POST')
            try:
                with urlopen(request,timeout=120) as response:data=json.load(response)
            except HTTPError as error:
                try:detail=json.load(error).get('error')
                except (ValueError,TypeError):detail=None
                raise RuntimeError(detail or 'Filter selection failed') from error
            if not data.get('ok'):raise RuntimeError(data.get('error') or 'Filter selection failed')
            print(json.dumps(data,ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--normalize-menu-open':
            if STOPPED.exists():
                print(json.dumps({'skipped':'audio stopped'}));return
            print(json.dumps(normalize_with_media()));return
        if len(sys.argv)==2 and sys.argv[1]=='--normalize-audio':
            print(json.dumps(normalize_with_media()));return
        if len(sys.argv)==3 and sys.argv[1]=='--profile-path':
            print(profile(sys.argv[2]));return
        if len(sys.argv)==2 and sys.argv[1]=='--dsp-profiles':
            print(json.dumps([NO_FILTER]+[item.name for item in profiles()],ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--dsp-filter-info':
            print(json.dumps(listening_filters(),ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--selected-filter':
            print(selected_filter());return
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
        if len(sys.argv)==3 and sys.argv[1]=='--sink-label':
            name=sys.argv[2];top=audio_topology();live=next((row for card in top['cards'] for row in card['sinks'] if row['name']==name),None)
            saved=_read_json(LOCALSINK,{})
            print((live or {}).get('label') or (saved.get('label') if isinstance(saved,dict) and saved.get('sink')==name else '') or name);return
        if len(sys.argv)==3 and sys.argv[1]=='--switch-output':
            request=json.loads(sys.argv[2])
            if not isinstance(request,dict) or not request.get('card') or request.get('profile') is None:
                raise ValueError('Choose a playback device and profile')
            server=rpid(SERVERPID)
            if not alive(server) or server==os.getpid():
                raise RuntimeError('Webremote must be running for an output switch')
            from urllib.request import Request,urlopen
            from urllib.error import HTTPError,URLError
            payload=json.dumps({'output':request,'mode':'laptop_laptop'}).encode()
            command=Request('http://127.0.0.1:'+str(PORT)+'/api/output-activate',data=payload,
                            headers={'Content-Type':'application/json'},method='POST')
            try:
                with urlopen(command,timeout=120) as response:data=json.load(response)
            except HTTPError as error:
                try:detail=json.load(error).get('error')
                except (ValueError,TypeError):detail=None
                raise RuntimeError(detail or 'Output switch failed') from error
            except URLError as error:raise RuntimeError('Webremote is unavailable: '+str(error)) from error
            if not data.get('ok'):raise RuntimeError(data.get('error') or 'Output switch failed')
            print(json.dumps(data,ensure_ascii=False))
            return
        if len(sys.argv)==4 and sys.argv[1]=='--output-route':
            raise ValueError('Choose profile and route together with --switch-output')
        if len(sys.argv)==2 and sys.argv[1]=='--output-topology':
            print(json.dumps(audio_topology(),ensure_ascii=False));return
        if len(sys.argv)==4 and sys.argv[1]=='--output-profile':
            raise ValueError('Choose profile and route together with --switch-output')
        if len(sys.argv)==3 and sys.argv[1]=='--resolve-output':
            print(json.dumps(choose_output_route(json.loads(sys.argv[2])),ensure_ascii=False));return
        raise ValueError('Invalid topology command')
    except Exception as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
if __name__=='__main__':
    import sys
    if len(sys.argv)>1:topology_cli()
    else:main()
