#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json, os, random, re, shutil, signal, socket, subprocess, threading, time, uuid

HOME=Path('/home/joel'); PROFILES=HOME/'Documents/prefs/audio/camilladsp'; MUSIC=HOME/'Downloads/Music'
STATE=HOME/'.local/state/sway/audio/camilladsp-webremote'; PORT=8766
CAMILLA=Path('/run/current-system/sw/bin/camilladsp'); SONOBUS=Path('/run/current-system/sw/bin/sonobus')
SONOSET=HOME/'.config/sonobus/SonoBus.settings'; EXTS={'.m4a','.aac','.mp3','.flac','.wav','.ogg','.opus'}
CAMPID=STATE/'camilladsp.pid'; ACTIVE=STATE/'active-profile'; SERVERPID=STATE/'web-server.pid'
MPVPID=STATE/'mpv.pid'; MPVSOCK=STATE/'mpv.sock'; MPVLOG=STATE/'mpv.log'; MPVVOLUME=STATE/'mpv-volume'; MODE=STATE/'mode'
LOCK=threading.RLock(); PLAYERLOCK=threading.RLock(); MPRISLOCK=threading.RLock(); LAST_MPRIS=None
SYSTEM_AUDIO_SERVICE='camilladsp-system-audio.service'
CACHELOCK=threading.RLock()
CACHE={'profiles':(0.0,[]),'playlists':(0.0,[]),'songs':(0.0,[])}
CAMILLA_PROCESS=None
SONOBUS_PROCESS=None

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

def exe(*names):
    for n in names:
        for p in (Path('/run/current-system/sw/bin')/n,HOME/'.nix-profile/bin'/n,Path(shutil.which(n) or '/nonexistent')):
            if p.is_file() and os.access(p,os.X_OK): return str(p)
    raise FileNotFoundError('Executable not found: '+', '.join(names))
def run(a,check=True,timeout=30,env=None): return subprocess.run(a,text=True,capture_output=True,check=check,timeout=timeout,env=env)
def ensure(): STATE.mkdir(parents=True,exist_ok=True); MUSIC.mkdir(parents=True,exist_ok=True)
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
    if source=='airplay':user_service('stop',SYSTEM_AUDIO_SERVICE);stop_mpv();airplay(True)
    elif source=='system':airplay(False);user_service('restart',SYSTEM_AUDIO_SERVICE)
    else:raise ValueError('Invalid audio source')
def restart_vnc():user_service('restart','camilladsp-wayvnc.service');return {'restarted':True}


def profiles():
    def load():
        return sorted(
            [p for pattern in ('*.yml','*.yaml') for p in PROFILES.glob(pattern) if p.is_file()],
            key=lambda item:item.name.lower(),
        )
    return cached('profiles',5.0,load)
def profile(name):
    if not isinstance(name,str) or Path(name).name!=name:raise ValueError('Invalid profile')
    p=(PROFILES/name).resolve()
    if p.parent!=PROFILES.resolve() or p.suffix.lower() not in {'.yml','.yaml'} or not p.is_file():raise FileNotFoundError(name)
    return p
def active():
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
    CAMPID.unlink(missing_ok=True)
def validate_camilla_profile(p):
    result=run([str(CAMILLA),'--check',str(p)],False,30)
    if result.returncode:
        detail=(result.stderr or result.stdout).strip()
        raise RuntimeError(
            'Invalid CamillaDSP profile '+p.name+
            ((': '+detail) if detail else '')
        )

def start_camilla(p):
    global CAMILLA_PROCESS
    target=profile(p.name if isinstance(p,Path) else str(p))
    validate_camilla_profile(target)
    logpath=STATE/'camilladsp.log'
    marker='\n===== start '+time.strftime('%Y-%m-%d %H:%M:%S')+' profile='+target.name+' =====\n'
    with logpath.open('ab',buffering=0) as log:
        log.write(marker.encode())
        process=subprocess.Popen(
            [str(CAMILLA),str(target)],stdin=subprocess.DEVNULL,
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
            try:
                content=logpath.read_text(errors='replace')
                tail=content[content.rfind('===== start '):][-4000:].strip()
            except OSError:tail=''
            raise RuntimeError(
                f'CamillaDSP exited with status {code} while starting {target.name}'+
                (f': {tail}' if tail else '')
            )
        time.sleep(.05)
    ACTIVE.write_text(target.name+'\n')
    return process.pid
def switch_profile(name):
    with LOCK:
        target=profile(name)
        previous=active()
        _,source,local_wanted,_=MODES[audio_mode()]
        apply_source_services(source)
        # SonoBus is intentionally left running. Profile changes only replace
        # CamillaDSP, and temporarily release the local monitor when required.
        if local_wanted:
            stop_local_monitor()
        stop_camilla()
        alsa100()

        try:
            pid=start_camilla(target)
            if local_wanted:
                start_local_monitor()
        except Exception:
            if local_wanted:
                stop_local_monitor()
            stop_camilla()
            if previous and previous!=target.name:
                try:
                    start_camilla(profile(previous))
                    if local_wanted:
                        start_local_monitor()
                except Exception:
                    pass
            raise

        invalidate_cache('profiles')
        return {'profile':target.name,'pid':pid,'mode':mode_state()}
def restart_camilla():
    name=active()
    if not name:raise RuntimeError('No active profile')
    return switch_profile(name)
def alsa100():
    r=run(['amixer','-c','Loopback','sset','PCM','100%'],False)
    if r.returncode:raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'Could not set Loopback PCM')

def sonopids():
    out=set()
    for n in ('sonobus','SonoBus'):
        r=run(['pgrep','-x',n],False)
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
    try:return max(0.0,min(100.0,float(MPVVOLUME.read_text().strip())))
    except (OSError,ValueError):return 100.0
def save_mpv_volume(value):
    value=max(0.0,min(100.0,float(value)));atomic(MPVVOLUME,f'{value:.2f}\n');return value
def stop_mpv():killpidfile(MPVPID,'mpv');MPVSOCK.unlink(missing_ok=True)
def ensure_mpv():
    if alive(rpid(MPVPID),'mpv') and MPVSOCK.exists():return
    stop_mpv();log=MPVLOG.open('ab',buffering=0)
    cmd=[exe('mpv'),'--idle=yes','--no-video','--no-terminal','--keep-open=no','--ao=alsa','--audio-device=alsa/camilladsp_input','--audio-samplerate=96000','--audio-channels=stereo','--audio-format=s32',f'--input-ipc-server={MPVSOCK}',f'--volume={saved_mpv_volume():.2f}','--volume-max=100']
    try:q=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    finally:log.close()
    MPVPID.write_text(f'{q.pid}\n');deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if MPVSOCK.exists() and q.poll() is None:return
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

def prop(name,default=None):
    try:
        v=mpv(['get_property',name]);return default if v is None else v
    except Exception:return default
def repeat_mode():
    if prop('loop-file','no') not in ('no',False,None,0):return 'one'
    if prop('loop-playlist','no') not in ('no',False,None,0):return 'all'
    return 'off'
def set_repeat(m):
    if m not in ('off','all','one'):raise ValueError('Invalid repeat mode')
    mpv(['set_property','loop-file','inf' if m=='one' else 'no']);mpv(['set_property','loop-playlist','inf' if m=='all' else 'no']);return m

def player_state():
    # Read-only status: do not start MPV while opening or polling the library.
    if not (alive(rpid(MPVPID),'mpv') and MPVSOCK.exists()):
        return {'available':False,'playing':False,'title':'','path':'','currentTime':None,'duration':None,'volume':saved_mpv_volume(),'repeat':'off'}
    try:
        values=mpv_properties(['path','playback-time','duration','volume','pause','loop-file','loop-playlist','media-title'])
    except Exception:
        return {'available':False,'playing':False,'title':'','path':'','currentTime':None,'duration':None,'volume':saved_mpv_volume(),'repeat':'off'}
    path=values.get('path') or ''
    loop_file=values.get('loop-file');loop_playlist=values.get('loop-playlist')
    repeat='one' if loop_file not in ('no',False,None,0) else ('all' if loop_playlist not in ('no',False,None,0) else 'off')
    def number(value):
        try:return float(value) if value is not None else None
        except (TypeError,ValueError):return None
    return {
        'available':bool(path),'playing':bool(path) and not bool(values.get('pause',True)),
        'title':values.get('media-title') or (Path(path).stem if path else ''),'path':path,
        'currentTime':number(values.get('playback-time')),'duration':number(values.get('duration')),
        'volume':number(values.get('volume')) if number(values.get('volume')) is not None else saved_mpv_volume(),
        'repeat':repeat,
    }

def mpris_players():
    r=run([exe('playerctl'),'--list-all'],False,5,media_env());return [x.strip() for x in r.stdout.splitlines() if x.strip() and x.strip()!='playerctld']
def mpris_status(p):
    r=run([exe('playerctl'),'--player',p,'status'],False,5,media_env());return r.stdout.strip() if r.returncode==0 else ''
def active_mpris():
    global LAST_MPRIS
    with MPRISLOCK:
        players=mpris_players()
        if not players:
            LAST_MPRIS=None
            return None
        statuses={player:mpris_status(player) for player in players}
        playing=[player for player in players if statuses[player]=='Playing']
        if playing:
            LAST_MPRIS=LAST_MPRIS if LAST_MPRIS in playing else playing[0]
        elif LAST_MPRIS not in players:
            LAST_MPRIS=min(players,key=lambda player:{'Paused':0,'Stopped':1}.get(statuses[player],2))
        return LAST_MPRIS
def pctl(p,*args,default=''):
    r=run([exe('playerctl'),'--player',p,*args],False,5,media_env());return r.stdout.strip() if r.returncode==0 else default
def system_volume():
    r=run([exe('pactl'),'get-sink-volume','@DEFAULT_SINK@'],False,5,media_env());m=re.search(r'(\d+)%',r.stdout);return int(m.group(1)) if m else 100
def set_system_volume(v):
    v=max(0,min(100,float(v)));r=run([exe('pactl'),'set-sink-volume','@DEFAULT_SINK@',f'{v:.2f}%'],False,5,media_env())
    if r.returncode:raise RuntimeError(r.stderr.strip() or 'Could not set system volume')
    return {'volume':v}

def system_state():
    player=active_mpris()
    base={'available':False,'player':'','status':'Stopped','playing':False,'title':'No system media','artist':'','position':0.0,'duration':0.0,'seekable':False,'volume':system_volume()}
    if not player:return base
    metadata=pctl(player,'metadata','--format','{{xesam:title}}\n{{xesam:artist}}\n{{mpris:length}}')
    rows=metadata.splitlines();title=rows[0] if rows else player;artist=rows[1] if len(rows)>1 else ''
    try:duration=float(rows[2])/1_000_000 if len(rows)>2 and rows[2] else 0.0
    except ValueError:duration=0.0
    try:position=float(pctl(player,'position') or 0)
    except ValueError:position=0.0
    status=mpris_status(player) or 'Stopped'
    base.update(available=True,player=player,status=status,playing=status=='Playing',title=title or player,artist=artist,position=max(0,position),duration=max(0,duration),seekable=duration>0)
    return base
def system_media(command):
    cmd={'previous':'previous','toggle':'play-pause','next':'next'}.get(command)
    if not cmd:raise ValueError('Invalid media command')
    p=active_mpris()
    if p:
        r=run([exe('playerctl'),'--player',p,cmd],False,10,media_env())
        if r.returncode==0:return {'player':p,'command':command}
    if alive(rpid(MPVPID),'mpv') and MPVSOCK.exists() and prop('path',''):
        mpv({'previous':['playlist-prev','force'],'toggle':['cycle','pause'],'next':['playlist-next','force']}[command]);return {'player':'PC Music','command':command}
    raise RuntimeError('No controllable media player')
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
def sobj(p):
    try:r=str(p.relative_to(MUSIC))
    except ValueError:r=str(p)
    return {'id':str(p.resolve()),'title':p.stem,'path':str(p.resolve()),'relative':r}

def lists():
    def load():
        if not MUSIC.exists():return []
        directories=sorted(
            [item for item in MUSIC.iterdir() if item.is_dir() and not item.name.startswith('.')],
            key=lambda item:item.name.casefold(),
        )
        return [{'name':item.name,'count':len(files(item))} for item in directories]
    return cached('playlists',5.0,load)
def atomic(path,text):
    t=path.with_name('.'+path.name+'.'+uuid.uuid4().hex);t.write_text(text);os.replace(t,path)

def rebuild(n):
    directory=pdir(n);directory.mkdir(parents=True,exist_ok=True)
    entries=[os.path.relpath(str(item.resolve()),MUSIC) for item in files(directory)]
    atomic(MUSIC/f'{n}.m3u','#EXTM3U\n'+'\n'.join(entries)+('\n' if entries else ''))
    shuffled=entries[:];random.SystemRandom().shuffle(shuffled)
    atomic(MUSIC/f'{n}-shuffled.m3u','#EXTM3U\n'+'\n'.join(shuffled)+('\n' if shuffled else ''))
    invalidate_cache('playlists','songs')
    return {'name':n,'count':len(entries)}
def create_list(n):
    d=pdir(n);exists=d.exists();d.mkdir(parents=True,exist_ok=True);r=rebuild(n);r.update(created=not exists);return r
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
        'sendchannels':'2.0','defsendqual':'4.0','mastinmute':'0.0',
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
def restart_sonobus(password=None,policy=None):
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
                try:tail=logpath.read_text(errors='replace')[-2500:]
                except OSError:tail=''
                raise RuntimeError('SonoBus failed to start: '+tail)
            if process.pid in sonopids():break
            time.sleep(.05)
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
def _audio_label(value,fallback='Audio output'):
    text=str(value or fallback).strip()
    return text.replace('_',' ').replace('-',' ')
def _profile_rows(card):
    raw=card.get('profiles') or []
    rows=[]
    if isinstance(raw,dict):
        raw=[dict(value or {},name=name) for name,value in raw.items()]
    for item in raw:
        if not isinstance(item,dict):continue
        name=str(item.get('name') or '').strip()
        if not name or name=='off':continue
        available=str(item.get('available','yes')).lower()
        if available in ('no','false'):continue
        sinks=item.get('sinks',item.get('n_sinks',1))
        try:
            if int(sinks)<1:continue
        except (TypeError,ValueError):pass
        rows.append({'name':name,'label':str(item.get('description') or name),'priority':int(item.get('priority') or 0)})
    return sorted(rows,key=lambda item:(-item['priority'],item['label'].casefold()))
def _port_rows(sink):
    raw=sink.get('ports') or []
    if isinstance(raw,dict):raw=[dict(value or {},name=name) for name,value in raw.items()]
    rows=[]
    for item in raw:
        if not isinstance(item,dict):continue
        name=str(item.get('name') or '').strip()
        if not name:continue
        available=str(item.get('availability',item.get('available','unknown'))).lower()
        if available in ('no','false'):continue
        rows.append({'name':name,'label':str(item.get('description') or name),'available':available,'priority':int(item.get('priority') or 0)})
    return sorted(rows,key=lambda item:(item['available'] not in ('yes','true'),-item['priority'],item['label'].casefold()))
def saved_output_route():
    data=_read_json(LOCALSINK,{})
    if isinstance(data,dict):return {key:str(data.get(key) or '') for key in ('card','profile','sink','port')}
    return {'card':'','profile':'','sink':'','port':''}
def audio_topology():
    cards_raw=_pactl_json('cards');sinks_raw=_pactl_json('sinks');saved=saved_output_route()
    sinks=[]
    for sink in sinks_raw:
        if not isinstance(sink,dict):continue
        name=str(sink.get('name') or '');low=name.lower()
        if not name.startswith('alsa_output.') or any(token in low for token in ('snd_aloop','loopback','camilladsp','sonobus','silent','filter-chain','null')):continue
        props=sink.get('properties') or {};card_index=str(sink.get('card',''))
        card_name=str(props.get('device.name') or props.get('alsa.card_name') or '')
        label=str(sink.get('description') or props.get('device.description') or name)
        ports=_port_rows(sink)
        if not ports:ports=[{'name':'','label':label,'available':'unknown','priority':0}]
        sinks.append({'name':name,'label':label,'cardIndex':card_index,'cardName':card_name,'ports':ports})
    cards=[]
    for card in cards_raw:
        if not isinstance(card,dict):continue
        name=str(card.get('name') or '');index=str(card.get('index',''));props=card.get('properties') or {}
        profiles=_profile_rows(card)
        if not name or not profiles:continue
        label=str(props.get('device.description') or props.get('alsa.card_name') or name)
        card_sinks=[item for item in sinks if item['cardIndex']==index or item['cardName']==name]
        if not card_sinks:
            card_id=str(props.get('alsa.card') or '')
            card_sinks=[item for item in sinks if card_id and (f'card{card_id}' in item['name'] or f'_{card_id}_' in item['name'])]
        active=card.get('active_profile') or {};active_name=str(active.get('name') if isinstance(active,dict) else active or '')
        cards.append({'name':name,'index':index,'label':label,'profiles':profiles,'activeProfile':active_name,'sinks':card_sinks})
    cards.sort(key=lambda item:item['label'].casefold())
    return {'cards':cards,'saved':saved}
def set_output_profile(card,profile):
    card=str(card or '').strip();profile=str(profile or '').strip()
    topology=audio_topology();item=next((x for x in topology['cards'] if x['name']==card),None)
    if item is None:raise ValueError('Selected audio card is unavailable')
    if profile not in {x['name'] for x in item['profiles']}:raise ValueError('Selected card profile is unavailable')
    result=run([exe('pactl'),'set-card-profile',card,profile],False,10,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not activate audio card profile')
    deadline=time.monotonic()+2.0
    while time.monotonic()<deadline:
        updated=audio_topology();current=next((x for x in updated['cards'] if x['name']==card),None)
        if current and current['activeProfile']==profile:return updated
        time.sleep(.1)
    return audio_topology()
def choose_output_route(requested=None):
    requested=requested if isinstance(requested,dict) else saved_output_route()
    card=str(requested.get('card') or '');profile=str(requested.get('profile') or '')
    if card and profile:set_output_profile(card,profile)
    topology=audio_topology();cards=topology['cards']
    selected_card=next((x for x in cards if x['name']==card),None)
    if selected_card is None:
        selected_card=next((x for x in cards if x['sinks']),cards[0] if cards else None)
    if selected_card is None:raise RuntimeError('No physical laptop audio card was found')
    if not profile or profile not in {x['name'] for x in selected_card['profiles']}:
        profile=selected_card['activeProfile'] or selected_card['profiles'][0]['name'];set_output_profile(selected_card['name'],profile);topology=audio_topology();selected_card=next(x for x in topology['cards'] if x['name']==selected_card['name'])
    sink_name=str(requested.get('sink') or '');sink=next((x for x in selected_card['sinks'] if x['name']==sink_name),None)
    if sink is None:
        if not selected_card['sinks']:raise RuntimeError('The selected card profile exposes no physical output sink')
        sink=selected_card['sinks'][0]
    port_name=str(requested.get('port') or '');port=next((x for x in sink['ports'] if x['name']==port_name),None)
    if port is None:port=sink['ports'][0]
    if port['name']:
        result=run([exe('pactl'),'set-sink-port',sink['name'],port['name']],False,10,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not activate the selected output port')
    route={'card':selected_card['name'],'profile':profile,'sink':sink['name'],'port':port['name'],'label':selected_card['label']+' · '+port['label']}
    _write_json(LOCALSINK,route);return route
def output_state():
    topology=audio_topology();topology['selected']=saved_output_route();return topology
def start_local_monitor(route=None):
    stop_local_monitor();selected=choose_output_route(route);sink=selected['sink']
    command=f"exec {shutil.which('arecord') or '/run/current-system/sw/bin/arecord'} -q -D camilladsp_output_shared -r 96000 -f S32_LE -c 2 -t raw | {exe('pacat')} --playback --device={sink} --rate=96000 --format=s32le --channels=2"
    log=(STATE/'local-monitor.log').open('ab',buffering=0)
    try:process=subprocess.Popen(['/run/current-system/sw/bin/bash','-lc',command],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=media_env())
    finally:log.close()
    LOCALMONPID.write_text(str(process.pid)+'\n');return {'pid':process.pid,**selected}
def mode_state():
    name=audio_mode();label,source,local,sono=MODES[name];policy=MODE_POLICIES[name]
    local_pid=rpid(LOCALMONPID)
    return {
        'mode':name,'label':label,'source':source,'localWanted':local,'sonobusWanted':sono,
        'camilla':alive(rpid(CAMPID),'camilladsp'),'sonobus':bool(sonopids()),'localMonitor':alive(local_pid),
        'systemAudio':run(['systemctl','--user','is-active',SYSTEM_AUDIO_SERVICE],False,5).stdout.strip()=='active',
        'airplay':run(['systemctl','is-active','shairport-sync.service'],False,5).stdout.strip()=='active',
        'localOutput':saved_output_route(),'localOutputLabel':(_read_json(LOCALSINK,{}).get('label') or 'Automatic'),'sonobusPolicy':policy,
    }
def apply_mode(name,password=None,restore_camilla=True,output=None):
    if name not in MODES:raise ValueError('Invalid audio mode')
    label,source,local,sono=MODES[name];policy=MODE_POLICIES[name]
    if restore_camilla and not alive(rpid(CAMPID),'camilladsp'):
        saved=active();available=profiles()
        selected=profile(saved) if saved else next((p for p in available if p.name=='00-filterless.yml'),available[0])
        start_camilla(selected)
    apply_source_services(source)
    if local:start_local_monitor(output)
    else:stop_local_monitor()
    if sono:restart_sonobus(password,policy)
    else:stop_sonobus()
    MODE.write_text(name+'\n')
    return mode_state()
def set_mode(name,password=None,output=None):
    with LOCK:return apply_mode(name,password,output=output)
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

def play_list(name,shuffle=False):
    rebuilt=rebuild(name)
    if rebuilt['count']<1:raise ValueError('Playlist is empty')
    playlist=MUSIC/(f'{name}-shuffled.m3u' if shuffle else f'{name}.m3u')
    mpv(['loadlist',str(playlist),'replace'])
    mpv(['set_property','pause',False])
    deadline=time.monotonic()+5
    last={}
    while time.monotonic()<deadline:
        last=player_state()
        if last.get('available'):
            return {'playlist':name,'shuffled':shuffle,'state':last}
        time.sleep(.05)
    # mpv accepted both commands; avoid a false failure if metadata is delayed.
    return {'playlist':name,'shuffled':shuffle,'state':last,'pending':True}

def full_state():
    return {
        'mode':mode_state(),
        'profiles':{'profiles':[item.name for item in profiles()],'active':active(),'running':alive(rpid(CAMPID),'camilladsp')},
        'player':player_state(),'systemMedia':system_state(),'groups':groups_state(),'playlists':lists(),
    }

def volatile_state():
    return {'mode':mode_state(),'player':player_state(),'systemMedia':system_state()}

PAGE='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>CamillaDSP Studio</title><style>\n:root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif;--v:#865dff;--b:#3f8eff;--g:#35d6a0}*{box-sizing:border-box}body{margin:0;min-height:100svh;padding:22px 16px 36px;color:#fff;background:radial-gradient(900px 520px at 50% -150px,#6542a8,#211a38 44%,#070910);background-attachment:fixed}main{max-width:540px;margin:auto}.hidden{display:none!important}.hero,.card{border:1px solid #ffffff22;background:linear-gradient(145deg,#ffffff1c,#ffffff0d);box-shadow:inset 0 1px #ffffff22,0 20px 50px #0005;backdrop-filter:blur(22px)}.hero{padding:25px 22px;border-radius:29px}.card{padding:18px;border-radius:24px;margin:14px 0}.card+.grid{margin-top:14px}.eyebrow,.label,.section-title{color:#bcb7cb;text-transform:uppercase;letter-spacing:.1em;font-size:12px;font-weight:800}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--g);box-shadow:0 0 15px var(--g);margin-right:8px}h1{margin:10px 0 5px;font-size:32px;letter-spacing:-.04em}.subtitle,.details,.meta{color:#b9b4c5;font-size:13px}.section-title{margin:27px 3px 10px}.title{font-size:20px;font-weight:800;margin-top:6px;overflow-wrap:anywhere}.grid{display:grid;gap:10px}.two{grid-template-columns:1fr 1fr}.three{grid-template-columns:1fr 1.2fr 1fr}.pc-transport{margin-bottom:18px}.pc-library-actions{margin-top:18px}.wide{grid-column:1/-1}button,input{font:inherit}button{border:0;color:#fff;cursor:pointer}.action,.item,.transport,.back{width:100%;transition:transform .12s,filter .15s}.action:active,.item:active,.transport:active,.back:active{transform:scale(.96);filter:brightness(1.15)}.action{min-height:54px;border-radius:18px;background:#ffffff1b;font-weight:780}.primary{background:linear-gradient(135deg,var(--b),var(--v))}.green{background:linear-gradient(135deg,#24ae7c,#287d95)}.danger{background:linear-gradient(135deg,#e64e74,#8e2b4b)}.active{outline:2px solid #a783ff;background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important}.search{width:100%;min-height:49px;border:1px solid #ffffff22;border-radius:17px;padding:12px 15px;color:#fff;background:#ffffff12;outline:0}.search:focus{border-color:#9b7aff;box-shadow:0 0 0 4px #825dff2e}.list{display:grid;gap:10px;margin-top:10px}.item{text-align:left;min-height:64px;padding:13px 15px;border-radius:19px;background:#ffffff16}.name{display:block;font-weight:780}.meta{display:block;margin-top:4px}.row{display:grid;grid-template-columns:minmax(0,1fr) 72px 72px;gap:8px}.header{display:grid;grid-template-columns:72px 1fr 72px;align-items:center}.header h1{text-align:center;font-size:23px}.back{min-height:43px;border-radius:15px;background:#ffffff18}.range{--fill:0%;width:100%;height:36px;background:transparent;appearance:none}.range::-webkit-slider-runnable-track{height:6px;border-radius:99px;background:linear-gradient(to right,#fff var(--fill),#ffffff2d var(--fill))}.range::-webkit-slider-thumb{appearance:none;width:21px;height:21px;margin-top:-7.5px;border-radius:50%;background:#fff;box-shadow:0 3px 10px #0008}.times{display:flex;justify-content:space-between;color:#aaa5b5;font-size:12px}.range-row{display:grid;grid-template-columns:24px 1fr 24px;align-items:center;gap:7px}.transport{display:grid;place-items:center;min-height:78px;border-radius:999px;background:#ffffff19}.transport.main{min-height:98px;background:linear-gradient(145deg,#9e68ff,#583ad2)}.media-icon{display:block;width:34px;height:34px;fill:none;stroke:#fff;stroke-width:2.15;stroke-linecap:round;stroke-linejoin:round;pointer-events:none}.transport.main .media-icon{width:42px;height:42px}.icon-fill{fill:#fff;stroke:#fff}.range{touch-action:none}.range.dragging::-webkit-slider-thumb{transform:scale(1.08)}.source-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.status{position:sticky;bottom:12px;margin-top:14px;text-align:center}.status:not(:empty){padding:11px;border-radius:16px;background:#121322ee}.error{background:#711c34ee!important}\n\n/* Final interaction and consistency pass */\nbutton{position:relative;overflow:hidden;-webkit-user-select:none;user-select:none;touch-action:manipulation}\nbutton:focus-visible,.search:focus-visible,.range:focus-visible{outline:2px solid #b79cff;outline-offset:3px}\nbutton:disabled{opacity:.58;cursor:default}\n.action,.item,.transport,.back{will-change:transform}\n.action.busy::after,.item.busy::after{content:"";position:absolute;inset:0;background:linear-gradient(105deg,transparent 25%,#ffffff2e 50%,transparent 75%);animation:ui-shine .8s linear infinite}\n@keyframes ui-shine{from{transform:translateX(-100%)}to{transform:translateX(100%)}}\n.transport{transition:transform .12s ease,filter .15s ease,box-shadow .2s ease}\n.transport.main.playing{box-shadow:0 18px 42px #6b45dd77,inset 0 1px #ffffff35}\n.media-icon{transition:transform .16s ease}.transport:active .media-icon{transform:scale(.9)}\n.range{cursor:pointer}.range.dragging::-webkit-slider-thumb{transform:scale(1.1)}\n.range::-webkit-slider-runnable-track{transition:background .08s linear}\n.mode-active{outline:2px solid #72e5bc;background:linear-gradient(135deg,#24ae7c,#287d95)!important}\n#repeat.active,#shuffle.active{outline:2px solid #a783ff;background:linear-gradient(135deg,#4e82ff66,#8552ff77)!important}\n.output-modal{position:fixed;inset:0;z-index:50;display:grid;place-items:end center;padding:18px;background:#05060bbb;backdrop-filter:blur(12px)}.output-sheet{width:min(100%,540px);max-height:82svh;overflow:auto;padding:20px;border:1px solid #ffffff2b;border-radius:26px;background:linear-gradient(145deg,#272139,#11131c);box-shadow:0 28px 80px #000b}.output-options{display:grid;gap:10px;margin:16px 0}.output-choice{display:grid;grid-template-columns:24px 1fr;gap:10px;align-items:center;padding:14px;border-radius:18px;background:#ffffff12}.output-choice input{width:20px;height:20px;accent-color:#865dff}.output-choice strong,.output-choice span{display:block}.output-choice span{margin-top:3px;color:#aaa5b5;font-size:11px;overflow-wrap:anywhere}.modal-actions{display:grid;grid-template-columns:1fr 1.4fr;gap:10px}\n\n.status{pointer-events:none}\n@media (prefers-reduced-motion:reduce){*{animation-duration:.001ms!important;transition-duration:.001ms!important;scroll-behavior:auto!important}}\n</style></head><body><main>\n<section id="profiles"><div class="hero"><div class="eyebrow"><span class="dot"></span>Audio engine online</div><h1>CamillaDSP Studio</h1><div class="subtitle">System audio, AirPlay and PC Music through CamillaDSP and SonoBus.</div></div><div class="section-title">Audio source</div><div class="card"><div id="source-title" class="title">Loading…</div><div id="source-details" class="details"></div><div id="mode-grid" class="source-grid"></div></div><button id="open-pc" class="action green">Open PC Music Library</button><div class="section-title">SonoBus Group</div><div class="card"><select id="group-select" class="search"></select><input id="group-key" class="search" placeholder="Profile name"><input id="group-name" class="search" placeholder="Group name"><input id="group-user" class="search" placeholder="Username"><input id="group-server" class="search" value="aoo.sonobus.net:10998" placeholder="Connection server"><label class="details"><input id="group-required" type="checkbox"> Password required</label><input id="group-password" class="search" type="password" placeholder="Password (never saved)"><button id="save-group" class="action primary">Save Group Profile</button></div><div class="section-title">Active profile</div><div class="card"><div id="active-profile" class="title">Loading…</div><div id="active-state" class="details"></div></div><div class="grid two"><button id="restart-camilla" class="action">Restart CamillaDSP</button><button id="restart-sonobus" class="action">Restart SonoBus</button><button id="restart-airplay" class="action">Restart AirPlay</button><button id="restart-vnc" class="action">Restart VNC</button></div><div class="section-title">System media</div><div class="card"><div class="label">Active desktop player</div><div id="system-title" class="title">No system media</div><div id="system-details" class="details"></div><input id="system-seek" class="range" type="range" min="0" max="1" step=".1"><div class="times"><span id="system-elapsed">0:00</span><span id="system-duration">0:00</span></div><div class="range-row"><span>🔈</span><input id="system-volume" class="range" type="range" min="0" max="100" value="100"><span>🔊</span></div><div class="grid three"><button id="system-previous" class="transport" aria-label="Previous"><svg class="media-icon" viewBox="0 0 24 24"><path d="M6 5v14"></path><path d="M18 6.5 8.5 12 18 17.5z"></path></svg></button><button id="system-toggle" class="transport main" aria-label="Play"><svg class="media-icon" viewBox="0 0 24 24"><path id="system-play-shape" class="icon-fill" d="M8 5.5 19 12 8 18.5z"></path></svg></button><button id="system-next" class="transport" aria-label="Next"><svg class="media-icon" viewBox="0 0 24 24"><path d="M18 5v14"></path><path d="M6 6.5 15.5 12 6 17.5z"></path></svg></button></div></div><div class="section-title">Listening profiles</div><input id="profile-search" class="search" placeholder="Search profiles"><div id="profile-list"></div></section>\n<section id="pc" class="hidden"><div class="header"><button id="pc-back" class="back">Back</button><h1>PC Music</h1><div></div></div><div class="card"><div class="label">Now playing</div><div id="now-title" class="title">Nothing playing</div><div id="now-details" class="details"></div><input id="seek" class="range" type="range" min="0" max="1" step=".1"><div class="times"><span id="elapsed">0:00</span><span id="duration">0:00</span></div><div class="range-row"><span>🔈</span><input id="volume" class="range" type="range" min="0" max="100" value="100"><span>🔊</span></div><div class="grid two"><button id="repeat" class="action">Repeat Off</button><button id="shuffle" class="action">Shuffle Queue</button></div></div><div class="grid three pc-transport"><button data-cmd="previous" class="transport" aria-label="Previous"><svg class="media-icon" viewBox="0 0 24 24"><path d="M6 5v14"></path><path d="M18 6.5 8.5 12 18 17.5z"></path></svg></button><button data-cmd="toggle" class="transport main" aria-label="Play"><svg class="media-icon" viewBox="0 0 24 24"><path id="local-play-shape" class="icon-fill" d="M8 5.5 19 12 8 18.5z"></path></svg></button><button data-cmd="next" class="transport" aria-label="Next"><svg class="media-icon" viewBox="0 0 24 24"><path d="M18 5v14"></path><path d="M6 6.5 15.5 12 6 17.5z"></path></svg></button></div><div class="grid two pc-library-actions"><button id="all-songs" class="action primary">All Songs</button><button id="new-playlist" class="action green">New Playlist</button></div><div class="section-title">Playlists</div><div id="playlists" class="list"></div></section>\n<section id="songs" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1>All Songs</h1><div></div></div><input id="song-search" class="search" placeholder="Search all songs"><div id="song-list" class="list"></div></section>\n<section id="playlist" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1 id="playlist-title">Playlist</h1><div></div></div><input id="playlist-search" class="search" placeholder="Search this playlist"><div id="playlist-songs" class="list"></div></section><div id="output-modal" class="output-modal hidden"><div class="output-sheet"><div class="label">Laptop playback device</div><div id="output-mode-title" class="title">Choose output</div><div class="details">Choose the hardware card, its playback profile, then the sink or jack.</div><div class="output-options"><label class="details">Card<select id="output-card" class="search"></select></label><label class="details">Profile<select id="output-profile" class="search"></select></label><label class="details">Sink / port<select id="output-sink" class="search"></select></label></div><div class="modal-actions"><button id="output-cancel" class="action">Cancel</button><button id="output-apply" class="action primary">Use Output</button></div></div></div><div id="status" class="status"></div>\n</main><script>const $=selector=>document.querySelector(selector);\nconst screens=[\'profiles\',\'pc\',\'songs\',\'playlist\'].map(id=>$(\'#\'+id));\nlet all=[],inside=[],current=\'\',statusTimer=null;\n\nconst state={\n  system:{slider:$(\'#system-seek\'),elapsed:$(\'#system-elapsed\'),durationLabel:$(\'#system-duration\'),volume:$(\'#system-volume\'),duration:0,dragging:false,polling:false,volumeHold:0,volumeTimer:null,volumePending:null},\n  local:{slider:$(\'#seek\'),elapsed:$(\'#elapsed\'),durationLabel:$(\'#duration\'),volume:$(\'#volume\'),duration:0,dragging:false,polling:false,volumeHold:0,volumeTimer:null,volumePending:null}\n};\n\nconst fmt=value=>{const v=Math.max(0,Number(value)||0);return Math.floor(v/60)+\':\'+String(Math.floor(v)%60).padStart(2,\'0\')};\nfunction show(id){screens.forEach(screen=>screen.classList.toggle(\'hidden\',screen.id!==id));scrollTo({top:0,behavior:\'smooth\'})}\nfunction note(message,error=false){const box=$(\'#status\');box.textContent=message;box.classList.toggle(\'error\',error);clearTimeout(statusTimer);if(message)statusTimer=setTimeout(()=>{if(box.textContent===message)box.textContent=\'\'},3200)}\nasync function api(url,options={}){const response=await fetch(url,{cache:\'no-store\',...options});const data=await response.json().catch(()=>({ok:false,error:\'Invalid server response\'}));if(!response.ok||data.ok===false)throw Error(data.error||\'Request failed\');return data}\nconst post=(url,data={})=>api(url,{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(data)});\nfunction fill(element,value,maximum){const max=Number(maximum),v=Number(value);const percent=Number.isFinite(max)&&max>0&&Number.isFinite(v)?Math.max(0,Math.min(100,v/max*100)):0;element.style.setProperty(\'--fill\',percent+\'%\')}\nfunction renderTimeline(media,position,duration){\n  const total=Number.isFinite(Number(duration))?Math.max(0,Number(duration)):0;\n  const current=Number.isFinite(Number(position))?Math.max(0,Math.min(Number(position),total>0?total:Number(position))):0;\n  media.duration=total;\n  media.slider.max=String(total>0?total:1);\n  if(!media.dragging)media.slider.value=String(current);\n  const shown=media.dragging?Number(media.slider.value):current;\n  fill(media.slider,shown,total);\n  media.elapsed.textContent=fmt(shown);\n  media.durationLabel.textContent=fmt(total);\n}\n\nfunction friendly(name){return name.replace(/\\.ya?ml$/i,\'\').replace(/^\\d+-/,\'\').replace(/^(earpods|cloud3|cmf-buds-pro-2)-/i,\'\').replace(/-/g,\' \').replace(/\\b\\w/g,char=>char.toUpperCase())}\nfunction device(name){const lower=name.toLowerCase();return lower.includes(\'cloud3\')?\'HyperX Cloud III\':lower.includes(\'cmf-buds-pro-2\')?\'CMF Buds Pro 2\':\'Apple EarPods\'}\nasync function busy(button,work,label=\'Working…\'){if(button.disabled)return;const original=button.innerHTML;button.disabled=true;button.classList.add(\'busy\');button.textContent=label;try{return await work()}finally{button.disabled=false;button.classList.remove(\'busy\');button.innerHTML=original}}\nfunction setPlayIcon(shape,button,playing){shape.setAttribute(\'d\',playing?\'M8 5h3v14H8z M14 5h3v14h-3z\':\'M8 5.5 19 12 8 18.5z\');button.setAttribute(\'aria-label\',playing?\'Pause\':\'Play\');button.classList.toggle(\'playing\',playing)}\n\nasync function loadProfiles(){const data=await api(\'/api/profiles\'),query=$(\'#profile-search\').value.toLowerCase(),root=$(\'#profile-list\');$(\'#active-profile\').textContent=data.active?friendly(data.active):\'No profile\';$(\'#active-state\').textContent=data.running?\'Running\':\'Stopped\';root.replaceChildren();for(const group of [\'Apple EarPods\',\'CMF Buds Pro 2\',\'HyperX Cloud III\']){const matches=data.profiles.filter(profile=>device(profile)===group&&friendly(profile).toLowerCase().includes(query));if(!matches.length)continue;const heading=document.createElement(\'div\');heading.className=\'section-title\';heading.textContent=group;root.append(heading);const list=document.createElement(\'div\');list.className=\'list\';for(const profile of matches){const button=document.createElement(\'button\');button.className=\'item\'+(profile===data.active&&data.running?\' active\':\'\');button.innerHTML=\'<span class="name"></span><span class="meta"></span>\';button.children[0].textContent=friendly(profile);button.children[1].textContent=group;button.onclick=async()=>{try{await busy(button,()=>post(\'/api/select\',{profile}),\'Switching…\');await loadProfiles();note(\'Profile activated\')}catch(error){note(error.message,true)}};list.append(button)}root.append(list)}}\n\nasync function pollSystem(){const media=state.system;if(media.polling)return;media.polling=true;try{const data=await api(\'/api/system-media\');$(\'#system-title\').textContent=data.title||\'No system media\';$(\'#system-details\').textContent=[data.artist,data.player,data.status].filter(Boolean).join(\' • \');renderTimeline(media,data.position,data.duration);if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1){media.volumePending=null}if(media.volumePending===null&&performance.now()>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){media.volume.value=String(data.volume);fill(media.volume,data.volume,100)}setPlayIcon($(\'#system-play-shape\'),$(\'#system-toggle\'),data.playing)}catch(error){note(error.message,true)}finally{media.polling=false}}\nasync function pollLocal(){const media=state.local;if(media.polling)return;media.polling=true;try{if(!$(\'#pc\').classList.contains(\'hidden\')){const data=await api(\'/api/player\');$(\'#now-title\').textContent=data.title||\'Nothing playing\';$(\'#now-details\').textContent=data.path||\'\';renderTimeline(media,data.currentTime,data.duration);if(media.volumePending!==null&&Number.isFinite(data.volume)&&Math.abs(data.volume-media.volumePending)<=1){media.volumePending=null}if(media.volumePending===null&&performance.now()>=media.volumeHold&&document.activeElement!==media.volume&&Number.isFinite(data.volume)){media.volume.value=String(data.volume);fill(media.volume,data.volume,100)}$(\'#repeat\').textContent=\'Repeat \'+({off:\'Off\',all:\'All\',one:\'1\'}[data.repeat]||\'Off\');$(\'#repeat\').classList.toggle(\'active\',data.repeat!==\'off\');setPlayIcon($(\'#local-play-shape\'),document.querySelector(\'[data-cmd="toggle"]\'),data.playing)}}catch(error){note(error.message,true)}finally{media.polling=false}}\n\nfunction bindSeek(media,url){\n  const slider=media.slider;\n  const begin=()=>{media.dragging=true;slider.classList.add(\'dragging\')};\n  const end=()=>{media.dragging=false;slider.classList.remove(\'dragging\')};\n  slider.addEventListener(\'pointerdown\',begin);\n  slider.addEventListener(\'pointercancel\',end);\n  slider.addEventListener(\'touchstart\',begin,{passive:true});\n  slider.oninput=()=>{media.dragging=true;const value=Number(slider.value);fill(slider,value,media.duration);media.elapsed.textContent=fmt(value)};\n  slider.onchange=async()=>{const target=Math.max(0,Number(slider.value)||0);end();try{await post(url,{seconds:target})}catch(error){note(error.message,true)}};\n}\nfunction bindVolume(media,url){const control=media.volume;const send=()=>{const value=Math.max(0,Math.min(100,Number(control.value)||0));media.volumeHold=performance.now()+1000;media.volumePending=value;post(url,{volume:value}).catch(error=>note(error.message,true))};control.oninput=()=>{const value=Number(control.value);media.volumeHold=performance.now()+1000;media.volumePending=value;fill(control,value,100);clearTimeout(media.volumeTimer);media.volumeTimer=setTimeout(send,90)};control.onchange=()=>{clearTimeout(media.volumeTimer);send()}}\n\nfunction render(sel,data,query){const list=$(sel);list.replaceChildren();const filtered=data.filter(song=>!query||[song.title,song.relative].join(\' \').toLowerCase().includes(query.toLowerCase()));if(!filtered.length){const empty=document.createElement(\'div\');empty.className=\'card details\';empty.textContent=query?\'No matches\':\'Nothing here yet\';list.append(empty);return}for(const song of filtered){const button=document.createElement(\'button\');button.className=\'item\';button.innerHTML=\'<span class="name"></span><span class="meta"></span>\';button.children[0].textContent=song.title;button.children[1].textContent=song.relative;button.onclick=async()=>{try{await busy(button,()=>post(\'/api/play/song\',{path:song.path}),\'Playing…\');note(\'Playing \'+song.title)}catch(error){note(error.message,true)}};list.append(button)}}\nasync function loadLists(){const data=await api(\'/api/playlists\'),list=$(\'#playlists\');list.replaceChildren();for(const playlist of data.playlists){const row=document.createElement(\'div\');row.className=\'row\';const open=document.createElement(\'button\');open.className=\'item\';open.innerHTML=\'<span class="name"></span><span class="meta"></span>\';open.children[0].textContent=playlist.name;open.children[1].textContent=playlist.count+(playlist.count===1?\' song\':\' songs\');open.onclick=async()=>{current=playlist.name;inside=(await api(\'/api/playlist?name=\'+encodeURIComponent(playlist.name))).songs;$(\'#playlist-title\').textContent=playlist.name;show(\'playlist\');render(\'#playlist-songs\',inside,\'\')};const play=document.createElement(\'button\');play.className=\'action\';play.textContent=\'Play\';play.onclick=()=>busy(play,()=>post(\'/api/play/playlist\',{name:playlist.name,shuffle:false}),\'Loading…\').catch(error=>note(error.message,true));const shuffle=document.createElement(\'button\');shuffle.className=\'action primary\';shuffle.textContent=\'Shuffle\';shuffle.onclick=()=>busy(shuffle,()=>post(\'/api/play/playlist\',{name:playlist.name,shuffle:true}),\'Mixing…\').catch(error=>note(error.message,true));row.append(open,play,shuffle);list.append(row)}}\n\nconst MODE_LABELS={ipad_ipad:\'iPad only\',laptop_laptop:\'Laptop only\',ipad_laptop:\'iPad → laptop\',laptop_ipad:\'Laptop → iPad\',ipad_both:\'iPad → iPad + laptop\',laptop_both:\'Laptop → iPad + laptop\',ipad_external:\'iPad → external\',laptop_external:\'Laptop → external\'};const LOCAL_MODES=new Set([\'ipad_laptop\',\'ipad_both\',\'laptop_laptop\',\'laptop_both\']);let pendingMode=null,outputTopology=null;const outputCard=$(\'#output-card\'),outputProfile=$(\'#output-profile\'),outputSink=$(\'#output-sink\');function selectedCard(){return outputTopology?.cards.find(item=>item.name===outputCard.value)}function fillSinks(){const card=selectedCard();outputSink.replaceChildren();for(const sink of card?.sinks||[])for(const port of sink.ports){const value=JSON.stringify({card:card.name,profile:outputProfile.value,sink:sink.name,port:port.name});outputSink.add(new Option(sink.label+\' · \'+port.label,value))}}function fillProfiles(preferred=\'\'){const card=selectedCard();outputProfile.replaceChildren(...(card?.profiles||[]).map(item=>new Option(item.label,item.name)));outputProfile.value=preferred&&card?.profiles.some(item=>item.name===preferred)?preferred:(card?.activeProfile||outputProfile.value);fillSinks()}async function activateProfile(){const card=selectedCard();if(!card||!outputProfile.value)return;outputProfile.disabled=true;outputSink.disabled=true;try{outputTopology=await post(\'/api/output-profile\',{card:card.name,profile:outputProfile.value});outputCard.value=card.name;fillProfiles(outputProfile.value)}finally{outputProfile.disabled=false;outputSink.disabled=false}}async function applyMode(mode,output=null){const b=document.querySelector(`[data-mode="${mode}"]`);try{await busy(b,()=>post(\'/api/mode\',{mode,password:$(\'#group-password\').value,output}),\'Switching…\');await refreshAll();note(\'Audio route activated\')}catch(error){note(error.message,true)}}async function chooseOutput(mode){pendingMode=mode;outputTopology=await api(\'/api/outputs\');const saved=outputTopology.saved||{};outputCard.replaceChildren(...outputTopology.cards.map(item=>new Option(item.label,item.name)));if(!outputTopology.cards.length)throw Error(\'No physical laptop audio cards are available\');outputCard.value=outputTopology.cards.some(item=>item.name===saved.card)?saved.card:outputCard.value;fillProfiles(saved.profile);const wanted=JSON.stringify({card:outputCard.value,profile:outputProfile.value,sink:saved.sink||\'\',port:saved.port||\'\'});if([...outputSink.options].some(option=>option.value===wanted))outputSink.value=wanted;$(\'#output-mode-title\').textContent=MODE_LABELS[mode];$(\'#output-modal\').classList.remove(\'hidden\')}outputCard.onchange=()=>fillProfiles();outputProfile.onchange=()=>activateProfile().catch(error=>note(error.message,true));for(const [key,label] of Object.entries(MODE_LABELS)){const b=document.createElement(\'button\');b.className=\'action\';b.dataset.mode=key;b.textContent=label;b.onclick=async()=>{try{if(LOCAL_MODES.has(key))await chooseOutput(key);else await applyMode(key)}catch(error){note(error.message,true)}};$(\'#mode-grid\').append(b)}$(\'#output-cancel\').onclick=()=>{$(\'#output-modal\').classList.add(\'hidden\');pendingMode=null};$(\'#output-apply\').onclick=async()=>{if(!outputSink.value||!pendingMode)return;const mode=pendingMode,route=JSON.parse(outputSink.value);$(\'#output-modal\').classList.add(\'hidden\');pendingMode=null;await applyMode(mode,route)};function loadGroups(data){const state=data.groups,select=$(\'#group-select\');select.replaceChildren(...Object.entries(state.profiles).map(([key,g])=>new Option(key+\' • \'+g.group,key,key===state.active,key===state.active)));const show=()=>{const key=select.value||state.active,g=state.profiles[key];if(!g)return;$(\'#group-key\').value=key;$(\'#group-name\').value=g.group;$(\'#group-user\').value=g.username;$(\'#group-server\').value=g.server;$(\'#group-required\').checked=!!g.passwordRequired};select.onchange=show;show()}async function refreshAll(){try{const d=await api(\'/api/state\');$(\'#source-title\').textContent=d.mode.label;$(\'#source-details\').textContent=\'CamillaDSP \'+(d.mode.camilla?\'running\':\'stopped\')+\' • SonoBus \'+(d.mode.sonobus?\'on\':\'off\')+\' • AirPlay \'+(d.mode.airplay?\'on\':\'off\')+(d.mode.localWanted?\' • Output \'+(d.mode.localOutputLabel||\'Automatic\'):\'\');document.querySelectorAll(\'[data-mode]\').forEach(b=>b.classList.toggle(\'mode-active\',b.dataset.mode===d.mode.mode));loadGroups(d);await loadProfiles();await pollSystem();await pollLocal();return d}catch(error){note(error.message,true)}}$(\'#save-group\').onclick=async()=>{try{await post(\'/api/groups/save\',{key:$(\'#group-key\').value,group:$(\'#group-name\').value,username:$(\'#group-user\').value,server:$(\'#group-server\').value,passwordRequired:$(\'#group-required\').checked});await refreshAll();note(\'Group profile saved\')}catch(error){note(error.message,true)}};$(\'#open-pc\').onclick=async()=>{try{show(\'pc\');await loadLists();await pollLocal()}catch(error){note(error.message,true)}};\n$(\'#pc-back\').onclick=()=>show(\'profiles\');\n$(\'#all-songs\').onclick=async()=>{try{all=(await api(\'/api/songs\')).songs;show(\'songs\');render(\'#song-list\',all,\'\')}catch(error){note(error.message,true)}};\n$(\'#song-search\').oninput=()=>render(\'#song-list\',all,$(\'#song-search\').value);\n$(\'#playlist-search\').oninput=()=>render(\'#playlist-songs\',inside,$(\'#playlist-search\').value);\ndocument.querySelectorAll(\'[data-back]\').forEach(button=>button.onclick=()=>show(button.dataset.back));\n$(\'#new-playlist\').onclick=async()=>{const name=prompt(\'New playlist name\');if(name)try{await post(\'/api/playlist/create\',{name});await loadLists();note(\'Playlist created\')}catch(error){note(error.message,true)}};\ndocument.querySelectorAll(\'[data-cmd]\').forEach(button=>button.onclick=async()=>{try{await busy(button,()=>post(\'/api/player/command\',{command:button.dataset.cmd}),\'…\');await pollLocal()}catch(error){note(error.message,true)}});\n$(\'#repeat\').onclick=async()=>{try{const data=await api(\'/api/player\');await post(\'/api/player/repeat\',{mode:{off:\'all\',all:\'one\',one:\'off\'}[data.repeat]||\'off\'});await pollLocal()}catch(error){note(error.message,true)}};\n$(\'#shuffle\').onclick=async()=>{try{await busy($(\'#shuffle\'),()=>post(\'/api/player/command\',{command:\'shuffle\'}),\'Shuffling…\');note(\'Queue shuffled\')}catch(error){note(error.message,true)}};\nfor(const [id,command] of [[\'system-previous\',\'previous\'],[\'system-toggle\',\'toggle\'],[\'system-next\',\'next\']])$(\'#\'+id).onclick=async()=>{try{await busy($(\'#\'+id),()=>post(\'/api/system-media\',{command}),\'…\');await pollSystem()}catch(error){note(error.message,true)}};\n$(\'#restart-camilla\').onclick=()=>busy($(\'#restart-camilla\'),()=>post(\'/api/restart-camilladsp\'),\'Restarting…\').then(loadProfiles).catch(error=>note(error.message,true));\n$(\'#restart-sonobus\').onclick=()=>busy($(\'#restart-sonobus\'),()=>post(\'/api/restart-sonobus\'),\'Restarting…\').catch(error=>note(error.message,true));\n$(\'#restart-airplay\').onclick=()=>busy($(\'#restart-airplay\'),()=>post(\'/api/restart-airplay\'),\'Restarting…\').catch(error=>note(error.message,true));\n$(\'#restart-vnc\').onclick=()=>busy($(\'#restart-vnc\'),()=>post(\'/api/restart-vnc\'),\'Restarting…\').catch(error=>note(error.message,true));\n$(\'#profile-search\').oninput=loadProfiles;\nbindSeek(state.local,\'/api/player/seek\');bindSeek(state.system,\'/api/system-media/seek\');bindVolume(state.local,\'/api/player/volume\');bindVolume(state.system,\'/api/system-volume\');\nlet staticRefreshRunning=false,volatileRefreshRunning=false;\nfunction renderProfileSnapshot(data){\n  const query=$(\'#profile-search\').value.toLowerCase(),root=$(\'#profile-list\');\n  $(\'#active-profile\').textContent=data.active?friendly(data.active):\'No profile\';\n  $(\'#active-state\').textContent=data.running?\'Running\':\'Stopped\';root.replaceChildren();\n  const groups=[\n    {name:\'Filterless / Speakers\',test:p=>p.toLowerCase()===\'00-filterless.yml\'},\n    {name:\'Apple EarPods\',test:p=>device(p)===\'Apple EarPods\'&&p.toLowerCase()!==\'00-filterless.yml\'},\n    {name:\'CMF Buds Pro 2\',test:p=>device(p)===\'CMF Buds Pro 2\'},\n    {name:\'HyperX Cloud III\',test:p=>device(p)===\'HyperX Cloud III\'}\n  ];\n  for(const group of groups){\n    const matches=data.profiles.filter(p=>group.test(p)&&friendly(p).toLowerCase().includes(query));if(!matches.length)continue;\n    const heading=document.createElement(\'div\');heading.className=\'section-title\';heading.textContent=group.name;root.append(heading);\n    const list=document.createElement(\'div\');list.className=\'list\';\n    for(const profile of matches){\n      const button=document.createElement(\'button\');button.className=\'item\'+(profile===data.active&&data.running?\' active\':\'\');\n      button.innerHTML=\'<span class="name"></span><span class="meta"></span>\';button.children[0].textContent=friendly(profile);button.children[1].textContent=group.name;\n      button.onclick=async()=>{try{await busy(button,()=>post(\'/api/select\',{profile}),\'Switching…\');await refreshStatic();note(\'Profile activated\')}catch(error){note(error.message,true)}};list.append(button)\n    }root.append(list)\n  }\n}\nfunction renderPlaylistSnapshot(playlists){\n  const list=$(\'#playlists\');list.replaceChildren();\n  for(const playlist of playlists){\n    const row=document.createElement(\'div\');row.className=\'row\';\n    const open=document.createElement(\'button\');open.className=\'item\';open.innerHTML=\'<span class="name"></span><span class="meta"></span>\';open.children[0].textContent=playlist.name;open.children[1].textContent=playlist.count+(playlist.count===1?\' song\':\' songs\');\n    open.onclick=async()=>{current=playlist.name;inside=(await api(\'/api/playlist?name=\'+encodeURIComponent(playlist.name))).songs;$(\'#playlist-title\').textContent=playlist.name;show(\'playlist\');render(\'#playlist-songs\',inside,\'\')};\n    const play=document.createElement(\'button\');play.className=\'action\';play.textContent=\'Play\';play.onclick=()=>busy(play,()=>post(\'/api/play/playlist\',{name:playlist.name,shuffle:false}),\'Loading…\').then(refreshVolatile).catch(error=>note(error.message,true));\n    const shuffle=document.createElement(\'button\');shuffle.className=\'action primary\';shuffle.textContent=\'Shuffle\';shuffle.onclick=()=>busy(shuffle,()=>post(\'/api/play/playlist\',{name:playlist.name,shuffle:true}),\'Mixing…\').then(refreshVolatile).catch(error=>note(error.message,true));\n    row.append(open,play,shuffle);list.append(row)\n  }\n}\nfunction renderVolatile(data){\n  $(\'#source-title\').textContent=data.mode.label;$(\'#source-details\').textContent=\'CamillaDSP \'+(data.mode.camilla?\'running\':\'stopped\')+\' • SonoBus \'+(data.mode.sonobus?\'on\':\'off\')+\' • AirPlay \'+(data.mode.airplay?\'on\':\'off\')+(data.mode.localWanted?\' • Output \'+(data.mode.localOutputLabel||\'Automatic\'):\'\');\n  document.querySelectorAll(\'[data-mode]\').forEach(button=>button.classList.toggle(\'mode-active\',button.dataset.mode===data.mode.mode));\n  const system=data.systemMedia,sm=state.system;$(\'#system-title\').textContent=system.title||\'No system media\';$(\'#system-details\').textContent=[system.artist,system.player,system.status].filter(Boolean).join(\' • \');renderTimeline(sm,system.position,system.duration);if(sm.volumePending===null&&document.activeElement!==sm.volume){sm.volume.value=String(system.volume);fill(sm.volume,system.volume,100)}setPlayIcon($(\'#system-play-shape\'),$(\'#system-toggle\'),system.playing);\n  const local=data.player,lm=state.local;$(\'#now-title\').textContent=local.title||\'Nothing playing\';$(\'#now-details\').textContent=local.path||\'\';renderTimeline(lm,local.currentTime,local.duration);if(lm.volumePending===null&&document.activeElement!==lm.volume){lm.volume.value=String(local.volume);fill(lm.volume,local.volume,100)}$(\'#repeat\').textContent=\'Repeat \'+({off:\'Off\',all:\'All\',one:\'1\'}[local.repeat]||\'Off\');$(\'#repeat\').classList.toggle(\'active\',local.repeat!==\'off\');setPlayIcon($(\'#local-play-shape\'),document.querySelector(\'[data-cmd="toggle"]\'),local.playing)\n}\nasync function refreshStatic(){if(staticRefreshRunning)return;staticRefreshRunning=true;try{const data=await api(\'/api/state\');renderProfileSnapshot(data.profiles);renderPlaylistSnapshot(data.playlists);loadGroups(data);renderVolatile(data);return data}catch(error){note(error.message,true)}finally{staticRefreshRunning=false}}\nasync function refreshVolatile(){if(volatileRefreshRunning||document.hidden)return;volatileRefreshRunning=true;try{renderVolatile(await api(\'/api/volatile\'))}catch(error){console.warn(\'volatile refresh failed\',error)}finally{volatileRefreshRunning=false}}\nloadProfiles=async()=>refreshStatic();loadLists=async()=>refreshStatic();refreshAll=async()=>refreshStatic();\nrefreshStatic();window.addEventListener(\'pageshow\',refreshStatic);document.addEventListener(\'visibilitychange\',()=>{if(!document.hidden){refreshStatic();refreshVolatile()}});setInterval(refreshVolatile,1000);\n</script></body></html>'
class H(BaseHTTPRequestHandler):
    def data(self,n,t,b):self.send_response(n);self.send_header('Content-Type',t);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(b)
    def out(self,n,d):self.data(n,'application/json; charset=utf-8',json.dumps(d,separators=(',',':')).encode())
    def body(self):
        n=int(self.headers.get('Content-Length','0'));d=json.loads(self.rfile.read(n)) if n else {}
        if not isinstance(d,dict):raise ValueError('Body must be object')
        return d
    def do_GET(self):
        u=urlparse(self.path);p=u.path;q=parse_qs(u.query)
        try:
            if p=='/':self.data(200,'text/html; charset=utf-8',PAGE.encode());return
            if p=='/api/state':r=full_state()
            elif p=='/api/volatile':r=volatile_state()
            elif p=='/api/profiles':r={'profiles':[x.name for x in profiles()],'active':active(),'running':alive(rpid(CAMPID),'camilladsp')}
            elif p=='/api/mode':r=mode_state()
            elif p=='/api/outputs':r=output_state()
            elif p=='/api/system-media':r=system_state()
            elif p=='/api/player':r=player_state()
            elif p=='/api/playlists':r={'playlists':lists()}
            elif p=='/api/songs':r={'songs':[sobj(x) for x in files()]}
            elif p=='/api/playlist':n=q.get('name',[''])[0];r={'name':n,'songs':[sobj(x) for x in files(pdir(n))]}
            else:self.out(404,{'ok':False,'error':'Not found'});return
            self.out(200,{'ok':True,**r})
        except Exception as e:self.out(500,{'ok':False,'error':str(e)})
    def do_POST(self):
        p=urlparse(self.path).path
        try:
            d=self.body()
            if p=='/api/mode':r=set_mode(d.get('mode'),d.get('password'),d.get('output'))
            elif p=='/api/output-profile':r=set_output_profile(d.get('card'),d.get('profile'))
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
                c=d.get('command');cmd={'toggle':['cycle','pause'],'next':['playlist-next','force'],'previous':['playlist-prev','force'],'shuffle':['playlist-shuffle']}.get(c)
                if not cmd:raise ValueError('Invalid command')
                mpv(cmd);r={'command':c}
            elif p=='/api/player/seek':v=max(0,float(d.get('seconds')));mpv(['seek',v,'absolute+exact']);r={'seconds':v}
            elif p=='/api/player/volume':v=save_mpv_volume(d.get('volume'));mpv(['set_property','volume',v]);r={'volume':v}
            elif p=='/api/player/repeat':r={'mode':set_repeat(d.get('mode'))}
            elif p=='/api/play/song':x=song(d.get('path'));mpv(['loadfile',str(x),'replace']);r=sobj(x)
            elif p=='/api/play/playlist':r=play_list(d.get('name'),bool(d.get('shuffle')))
            elif p=='/api/playlist/create':r=create_list(d.get('name'))
            else:self.out(404,{'ok':False,'error':'Not found'});return
            self.out(200,{'ok':True,**r})
        except (ValueError,TypeError,KeyError,FileNotFoundError,json.JSONDecodeError) as e:self.out(400,{'ok':False,'error':str(e)})
        except Exception as e:self.out(500,{'ok':False,'error':str(e)})
    def log_message(self,*a):pass
class S(ThreadingHTTPServer):allow_reuse_address=True;daemon_threads=True

def reset_runtime():
    ensure();stop_local_monitor();stop_mpv();stop_sonobus();stop_camilla(include_stale=True)
def start_runtime():
    ensure();alsa100()
    available=profiles()
    if not available:raise RuntimeError('No CamillaDSP profiles found in '+str(PROFILES))
    saved=active();selected=None
    if saved:
        try:selected=profile(saved)
        except (ValueError,FileNotFoundError):pass
    if selected is None:
        selected=next((item for item in available if item.name=='00-filterless.yml'),available[0])
    # Reap only a stale PID inherited from an earlier crashed server.
    stop_camilla(include_stale=True)
    start_camilla(selected)
    mode=audio_mode()
    try:
        apply_mode(mode,restore_camilla=False)
    except RuntimeError as error:
        if 'exposes no physical output sink' not in str(error):raise
        label,source,local,sono=MODES[mode]
        apply_source_services(source);stop_local_monitor()
        if sono:restart_sonobus(None,MODE_POLICIES[mode])
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
def cleanup():
    # Stop only processes owned by this server instance. An older server's
    # cleanup can therefore never kill a replacement instance.
    stop_local_monitor();stop_mpv();stop_sonobus();stop_camilla()
def main():
    ensure();me=os.getpid();old=rpid(SERVERPID)
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
    reset_runtime();start_runtime()
    server=S(('0.0.0.0',PORT),H);SERVERPID.write_text(str(me)+'\n')
    def shut(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shut);signal.signal(signal.SIGHUP,shut)
    print(f'CamillaDSP web remote listening on 0.0.0.0:{PORT}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close();cleanup()
        if rpid(SERVERPID)==me:SERVERPID.unlink(missing_ok=True)
if __name__=='__main__':main()
