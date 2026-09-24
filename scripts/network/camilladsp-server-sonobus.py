#!/usr/bin/env python3
# CamillaDSP + SonoBus + PC Music web remote.
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json, os, random, re, shutil, signal, socket, subprocess, threading, time, uuid

HOME=Path('/home/joel'); PROFILES=HOME/'Documents/prefs/audio/camilladsp'; MUSIC=HOME/'Downloads/Music'
STATE=HOME/'.local/state/sway/audio/camilladsp-webremote'; PORT=8766
CAMILLA=Path('/run/current-system/sw/bin/camilladsp'); SONOBUS=Path('/run/current-system/sw/bin/sonobus')
SONOSET=HOME/'.config/sonobus/SonoBus.settings'; EXTS={'.m4a','.aac','.mp3','.flac','.wav','.ogg','.opus'}
CAMPID=STATE/'camilladsp.pid'; ACTIVE=STATE/'active-profile'; SERVERPID=STATE/'web-server.pid'
MPVPID=STATE/'mpv.pid'; MPVSOCK=STATE/'mpv.sock'; MPVLOG=STATE/'mpv.log'; MODE=STATE/'mode'
LOCK=threading.RLock(); PLAYERLOCK=threading.RLock(); PLAYLISTLOCK=threading.RLock()

def exe(*names):
    for n in names:
        for p in (Path('/run/current-system/sw/bin')/n, HOME/'.nix-profile/bin'/n, Path(shutil.which(n) or '/nonexistent')):
            if p.is_file() and os.access(p,os.X_OK): return str(p)
    raise FileNotFoundError('Executable not found: '+', '.join(names))
def run(a,check=True,timeout=30,env=None): return subprocess.run(a,text=True,capture_output=True,check=check,timeout=timeout,env=env)
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
        except (OSError,PermissionError):
            try:os.kill(pid,signal.SIGTERM)
            except OSError:pass
        for _ in range(30):
            if not alive(pid):break
            time.sleep(.1)
        if alive(pid):
            try:os.killpg(pid,signal.SIGKILL)
            except OSError:pass
    path.unlink(missing_ok=True)
def ensure(): STATE.mkdir(parents=True,exist_ok=True); MUSIC.mkdir(parents=True,exist_ok=True)
def stop_system_audio():
    # PipeWire must remain running because it carries desktop audio.
    return None
def start_system_audio():
    return set_source_mode("system")
def airplay(start):
    action='restart' if start else 'stop'; r=run(['systemctl',action,'nqptp.service','shairport-sync.service'],False)
    if r.returncode: raise RuntimeError(r.stderr.strip() or 'Could not change AirPlay service state')

SYSTEM_AUDIO_SERVICE = "camilladsp-system-audio.service"

def user_service(action, name):
    result = run(["systemctl", "--user", action, name], False, 30)
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Could not {action} {name}"
        )
    return result

def audio_mode():
    try:
        mode = MODE.read_text(encoding="utf-8").strip()
    except OSError:
        mode = "airplay"
    return mode if mode in {"airplay", "system"} else "airplay"

def apply_source_mode(mode):
    if mode == "system":
        airplay(False)
        user_service("restart", SYSTEM_AUDIO_SERVICE)
        MODE.write_text("system\n", encoding="utf-8")
    elif mode == "airplay":
        user_service("stop", SYSTEM_AUDIO_SERVICE)
        stop_mpv()
        airplay(True)
        MODE.write_text("airplay\n", encoding="utf-8")
    else:
        raise ValueError("Invalid audio source mode")
    return {"mode": mode}

def set_source_mode(mode):
    with LOCK:
        if not alive(rpid(CAMPID), "camilladsp"):
            restart_camilla()
        result = apply_source_mode(mode)
        result["sonobus"] = bool(sonopids())
        return result

def restart_vnc_service():
    result = run(
        ["systemctl", "--user", "restart", "camilladsp-wayvnc.service"],
        False,
        30,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Could not restart WayVNC service"
        )
    return {"restarted": True}

def media_session_env():
    env = os.environ.copy()
    runtime = f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return env


def mpris_players():
    result = run([exe("playerctl"), "--list-all"], False, 10, media_session_env())
    if result.returncode:
        return []
    seen = set()
    players = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if name and name != "playerctld" and name not in seen:
            seen.add(name)
            players.append(name)
    return players


def mpris_status(player):
    result = run(
        [exe("playerctl"), "--player", player, "status"],
        False,
        5,
        media_session_env(),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def active_mpris_player():
    players = mpris_players()
    if not players:
        return None
    return min(
        players,
        key=lambda player: {
            "Playing": 0,
            "Paused": 1,
            "Stopped": 2,
        }.get(mpris_status(player), 3),
    )


def playerctl_value(player, *arguments, default=""):
    result = run(
        [exe("playerctl"), "--player", player, *arguments],
        False,
        5,
        media_session_env(),
    )
    return result.stdout.strip() if result.returncode == 0 else default


def system_volume_state():
    result = run(
        [exe("pactl"), "get-sink-volume", "@DEFAULT_SINK@"],
        False,
        5,
        media_session_env(),
    )
    match = re.search(r"(\d+)%", result.stdout)
    return int(match.group(1)) if match else 100


def set_system_volume(percent):
    value = max(0, min(100, float(percent)))
    result = run(
        [exe("pactl"), "set-sink-volume", "@DEFAULT_SINK@", f"{value:.2f}%"],
        False,
        5,
        media_session_env(),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not set system volume")
    return {"volume": value}


def system_media_state():
    player = active_mpris_player()
    state = {
        "available": False,
        "player": "",
        "status": "Stopped",
        "playing": False,
        "title": "No system media",
        "artist": "",
        "position": 0.0,
        "duration": 0.0,
        "seekable": False,
        "volume": system_volume_state(),
    }
    if not player:
        return state
    status = mpris_status(player) or "Stopped"
    try:
        position = float(playerctl_value(player, "position", default="0") or 0)
    except ValueError:
        position = 0.0
    try:
        duration = float(playerctl_value(player, "metadata", "mpris:length", default="0") or 0) / 1_000_000
    except ValueError:
        duration = 0.0
    state.update({
        "available": True,
        "player": player,
        "status": status,
        "playing": status == "Playing",
        "title": playerctl_value(player, "metadata", "xesam:title", default=player),
        "artist": playerctl_value(player, "metadata", "xesam:artist", default=""),
        "position": max(0.0, position),
        "duration": max(0.0, duration),
        "seekable": duration > 0,
    })
    return state


def system_media(command):
    commands = {
        "previous": "previous",
        "toggle": "play-pause",
        "next": "next",
    }
    playerctl_command = commands.get(command)
    if playerctl_command is None:
        raise ValueError("Invalid system media command")
    player = active_mpris_player()
    if player:
        result = run(
            [exe("playerctl"), "--player", player, playerctl_command],
            False,
            10,
            media_session_env(),
        )
        if result.returncode == 0:
            return {"command": command, "backend": "mpris", "player": player}
    if alive(rpid(MPVPID), "mpv") and MPVSOCK.exists() and prop("path", ""):
        mpv_commands = {
            "previous": ["playlist-prev", "force"],
            "toggle": ["cycle", "pause"],
            "next": ["playlist-next", "force"],
        }
        mpv(mpv_commands[command])
        return {"command": command, "backend": "mpv-ipc", "player": "PC Music"}
    raise RuntimeError("No controllable system media player is available")


def system_media_seek(seconds):
    player = active_mpris_player()
    if not player:
        raise RuntimeError("No controllable system media player is available")
    value = max(0.0, float(seconds))
    result = run(
        [exe("playerctl"), "--player", player, "position", f"{value:.3f}"],
        False,
        10,
        media_session_env(),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "This player does not support seeking")
    return {"player": player, "position": value}


def alsa100():
    r=run(['amixer','-c','Loopback','sset','PCM','100%'],False)
    if r.returncode:raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'Could not set Loopback PCM')
def profiles():
    return sorted([p for pat in ('*.yml','*.yaml') for p in PROFILES.glob(pat) if p.is_file()],key=lambda p:p.name.lower())
def profile(name):
    if not isinstance(name,str) or Path(name).name!=name:raise ValueError('Invalid profile name')
    p=(PROFILES/name).resolve()
    if p.parent!=PROFILES.resolve() or p.suffix.lower() not in {'.yml','.yaml'} or not p.is_file():raise FileNotFoundError(name)
    return p
def active():
    try:return ACTIVE.read_text().strip()
    except OSError:return ''
def stop_camilla():
    killpidfile(CAMPID,'camilladsp'); subprocess.run(['pkill','-x','camilladsp'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)

def start_camilla(p):
    log_path = STATE / "camilladsp.log"

    log = log_path.open(
        "ab",
        buffering=0,
    )

    try:
        process = subprocess.Popen(
            [
                str(CAMILLA),
                str(p),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    finally:
        log.close()

    CAMPID.write_text(
        f"{process.pid}\n"
    )

    # Verify that CamillaDSP remains alive through its
    # initial ALSA-device setup. This is readiness polling,
    # not an arbitrary fixed delay.
    for _ in range(20):
        return_code = process.poll()

        if return_code is not None:
            try:
                log_tail = log_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )[-2000:].strip()

            except OSError:
                log_tail = ""

            CAMPID.unlink(
                missing_ok=True
            )

            raise RuntimeError(
                "CamillaDSP exited during startup"
                + (
                    f" with status {return_code}"
                    if return_code is not None
                    else ""
                )
                + (
                    ": " + log_tail
                    if log_tail
                    else ""
                )
            )

        time.sleep(0.1)

    ACTIVE.write_text(
        p.name + "\n"
    )

    return process.pid
def switch_profile(name):
    with LOCK:
        p=profile(name); stop_system_audio(); stop_camilla(); alsa100(); return {'profile':p.name,'pid':start_camilla(p)}
def restart_camilla():
    if not active():raise RuntimeError('No active profile')
    return switch_profile(active())
def sonopids():
    out=set()
    for n in ('sonobus','SonoBus'):
        r=run(['pgrep','-x',n],False)
        for x in r.stdout.split():
            try:out.add(int(x))
            except ValueError:pass
    return out
def stop_sonobus():
    for p in sonopids():
        try:os.kill(p,signal.SIGTERM)
        except OSError:pass
    for _ in range(30):
        if not sonopids():return
        time.sleep(.1)
    for p in sonopids():
        try:os.kill(p,signal.SIGKILL)
        except OSError:pass
def configure_sonobus():
    if not SONOSET.is_file():raise FileNotFoundError(SONOSET)
    t=SONOSET.read_text(); attrs={'deviceType':'ALSA','audioOutputDeviceName':'SonoBus Silent Output','audioInputDeviceName':'CamillaDSP SonoBus','audioDeviceRate':'96000.0','audioDeviceBufferSize':'512'}
    for k,v in attrs.items():
        t,n=re.subn(rf'(<DEVICESETUP\b[^>]*\b{k}=\")[^\"]*(\")',rf'\g<1>{v}\g<2>',t,count=1)
        if n!=1:raise RuntimeError(f'Could not set SonoBus {k}')
    t,n=re.subn(r'(<PARAM\s+id=\"sendchannels\"\s+value=\")[^\"]*(\"\s*/>)',r'\g<1>2.0\g<2>',t,count=1)
    if n!=1:raise RuntimeError('Could not set SonoBus sendchannels')
    tmp=SONOSET.with_suffix('.tmp'); tmp.write_text(t); tmp.replace(SONOSET)
def restart_sonobus():
    with LOCK:
        stop_sonobus()
        alsa100()
        configure_sonobus()
        log = (STATE / "sonobus.log").open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                [str(SONOBUS), "--group=rt4817-camilladsp", "--username=rt4817", "--connectionserver=aoo.sonobus.net:10998"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log.close()
        # SonoBus has no readiness socket here. Require the process to
        # survive a bounded startup window before reporting success.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("SonoBus failed to start; check " + str(STATE / "sonobus.log"))
            time.sleep(0.05)
        return {"pid": process.pid}

def stop_mpv():
    killpidfile(MPVPID,'mpv'); MPVSOCK.unlink(missing_ok=True)
def ensure_mpv():
    if alive(rpid(MPVPID), "mpv") and MPVSOCK.exists():
        return

    stop_mpv()
    log = MPVLOG.open("ab", buffering=0)
    command = [
        exe("mpv"),
        "--idle=yes",
        "--no-video",
        "--no-terminal",
        "--keep-open=no",
        "--ao=alsa",
        "--audio-device=alsa/camilladsp_input",
        "--audio-samplerate=96000",
        "--audio-channels=stereo",
        "--audio-format=s32",
        f"--input-ipc-server={MPVSOCK}",
        "--volume=100",
        "--volume-max=100",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()

    MPVPID.write_text(f"{process.pid}\n")
    for _ in range(50):
        if MPVSOCK.exists() and process.poll() is None:
            return
        time.sleep(0.1)

    raise RuntimeError("mpv failed to start; check " + str(MPVLOG))
def mpv(command):
    with PLAYERLOCK:
        ensure_mpv(); data=b''
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as c:
            c.settimeout(3); c.connect(str(MPVSOCK)); c.sendall((json.dumps({'command':command})+'\n').encode())
            while b'\n' not in data:
                x=c.recv(65536)
                if not x:break
                data+=x
        if not data:raise RuntimeError('mpv returned no response')
        r=json.loads(data.splitlines()[0])
        if r.get('error')!='success':raise RuntimeError(r.get('error','mpv command failed'))
        return r.get('data')
def prop(name,default=None):
    try:
        v=mpv(['get_property',name]); return default if v is None else v
    except Exception:return default
def repeat_mode():
    if prop('loop-file','no') not in ('no',False,None,0):return 'one'
    if prop('loop-playlist','no') not in ('no',False,None,0):return 'all'
    return 'off'
def set_repeat(m):
    if m not in ('off','all','one'):raise ValueError('Invalid repeat mode')
    mpv(['set_property','loop-file','inf' if m=='one' else 'no']); mpv(['set_property','loop-playlist','inf' if m=='all' else 'no']); return m
def player_state():
    p=prop('path','')
    return {'available':bool(p),'playing':bool(p) and not bool(prop('pause',True)),'title':prop('media-title','') or (Path(p).stem if p else ''),'path':p or '','currentTime':float(prop('time-pos',0) or 0),'duration':float(prop('duration',0) or 0),'volume':float(prop('volume',100) or 0),'repeat':repeat_mode()}
def enter_pc():
    with LOCK:
        result = set_source_mode("system")
        ensure_mpv()
        result["player"] = "mpv"
        return result
def return_airplay():
    with LOCK:
        stop_mpv()
        return set_source_mode("airplay")

def pname(n):
    if not isinstance(n,str):raise ValueError('Invalid playlist name')
    n=n.strip()
    if not n or n in ('.','..') or '/' in n or '\\' in n or '\0' in n:raise ValueError('Invalid playlist name')
    return n
def pdir(n):
    p=(MUSIC/pname(n)).resolve()
    if p.parent!=MUSIC.resolve():raise ValueError('Invalid playlist path')
    return p
def files(root=None):
    root=root or MUSIC; found={}
    if not root.exists():return []
    for p in root.rglob('*'):
        try:
            if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith('.'):found[str(p.resolve())]=p
        except OSError:pass
    return sorted(found.values(),key=lambda p:(p.stem.casefold(),str(p).casefold()))
def sobj(p):
    try:r=str(p.relative_to(MUSIC))
    except ValueError:r=str(p)
    return {'id':str(p.resolve()),'title':p.stem,'path':str(p.resolve()),'relative':r}
def lists():return [{'name':d.name,'count':len(files(d))} for d in sorted([p for p in MUSIC.iterdir() if p.is_dir() and not p.name.startswith('.')],key=lambda p:p.name.casefold())]
def atomic(path,text):
    t=path.with_name('.'+path.name+'.'+uuid.uuid4().hex); t.write_text(text,encoding='utf-8'); os.replace(t,path)
def rebuild(n):
    d=pdir(n); d.mkdir(parents=True,exist_ok=True); entries=[os.path.relpath(str(x.resolve()),MUSIC) for x in files(d)]
    atomic(MUSIC/f'{n}.m3u','#EXTM3U\n'+'\n'.join(entries)+('\n' if entries else ''))
    sh=entries[:]; random.SystemRandom().shuffle(sh); atomic(MUSIC/f'{n}-shuffled.m3u','#EXTM3U\n'+'\n'.join(sh)+('\n' if sh else ''))
    return {'name':n,'count':len(entries)}
def create_list(n):
    with PLAYLISTLOCK:
        d=pdir(n); existed=d.exists(); d.mkdir(parents=True,exist_ok=True); r=rebuild(n); r.update(created=not existed,changed=not existed); return r
def unique(p):
    if not p.exists() and not p.is_symlink():return p
    for i in range(2,10000):
        q=p.with_name(f'{p.stem} ({i}){p.suffix}')
        if not q.exists() and not q.is_symlink():return q
    raise RuntimeError('Could not create unique filename')
def remove_list(n):
    with PLAYLISTLOCK:
        d=pdir(n)
        if not d.exists():return {'name':n,'removed':True,'changed':False,'songsPreserved':True}
        keep=MUSIC/'Unsorted'; keep.mkdir(exist_ok=True)
        for x in list(d.iterdir()):
            if x.is_symlink():x.unlink()
            elif x.is_file() and x.suffix.lower() in EXTS:x.replace(unique(keep/x.name))
            elif x.is_file():x.unlink()
        shutil.rmtree(d); (MUSIC/f'{n}.m3u').unlink(missing_ok=True); (MUSIC/f'{n}-shuffled.m3u').unlink(missing_ok=True)
        return {'name':n,'removed':True,'changed':True,'songsPreserved':True}
def song(v):
    if not isinstance(v,str):raise ValueError('Invalid song')
    p=Path(v).resolve()
    if MUSIC.resolve() not in p.parents or not p.is_file() or p.suffix.lower() not in EXTS:raise ValueError('Song is outside library or unsupported')
    return p
def add_to_list(n,paths):
    if not isinstance(paths,list) or not paths:raise ValueError('Select at least one song')
    with PLAYLISTLOCK:
        d=pdir(n); d.mkdir(parents=True,exist_ok=True); added=0
        for v in paths:
            s=song(v); t=d/s.name
            if t.exists() or t.is_symlink():
                if t.resolve()==s:continue
                t=unique(t)
            t.symlink_to(s); added+=1
        r=rebuild(n);r['added']=added;return r
def play_list(n,shuffle=False):
    rebuild(n); p=MUSIC/(f'{n}-shuffled.m3u' if shuffle else f'{n}.m3u');mpv(['loadlist',str(p),'replace']);mpv(['set_property','pause',False]);return {'playlist':n,'shuffled':shuffle}




PAGE = '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#090a12"><title>CamillaDSP Studio</title>\n<style>\n:root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",Inter,sans-serif;--violet:#825dff;--blue:#3e8dff;--mint:#35d6a0;--rose:#ef537d;--text:#fff;--muted:#aaa7b7}\n*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{width:100%;max-width:100%;overflow-x:hidden}body{margin:0;min-height:100svh;padding:max(21px,env(safe-area-inset-top)) 17px max(30px,env(safe-area-inset-bottom));color:#fff;background:radial-gradient(850px 520px at 50% -160px,#6240a4 0%,#211a38 43%,#070910 100%);background-attachment:fixed}main{width:min(100%,520px);margin:auto}.hidden{display:none!important}section{animation:arrive .25s cubic-bezier(.2,.8,.2,1)}@keyframes arrive{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}\n.hero{padding:25px 22px 22px;margin-bottom:15px;border:1px solid rgba(255,255,255,.14);border-radius:29px;background:linear-gradient(145deg,rgba(139,92,246,.28),rgba(44,48,82,.12));box-shadow:inset 0 1px rgba(255,255,255,.16),0 25px 60px rgba(0,0,0,.33);backdrop-filter:blur(22px)}.eyebrow{display:flex;align-items:center;gap:8px;color:#cbc7da;font-size:12px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}.dot{width:9px;height:9px;border-radius:50%;background:var(--mint);box-shadow:0 0 15px var(--mint)}h1{margin:11px 0 5px;font-size:32px;line-height:1.08;letter-spacing:-.04em}.subtitle{color:#c2bece;font-size:14px;line-height:1.45}\n.card{padding:19px;margin-bottom:15px;border:1px solid rgba(255,255,255,.12);border-radius:24px;background:linear-gradient(145deg,rgba(255,255,255,.105),rgba(255,255,255,.055));box-shadow:inset 0 1px rgba(255,255,255,.13),0 17px 42px rgba(0,0,0,.26);backdrop-filter:blur(20px)}.label,.section-title{color:#aaa7b7;font-size:12px;font-weight:820;letter-spacing:.1em;text-transform:uppercase}.section-title{margin:28px 3px 11px}.title{margin-top:7px;font-size:21px;font-weight:780;line-height:1.28;overflow-wrap:anywhere}.details{margin-top:5px;color:#b7b3c1;font-size:13px;line-height:1.4;overflow-wrap:anywhere}\n.grid{display:grid;gap:10px}.two{grid-template-columns:1fr 1fr}.three{grid-template-columns:1fr 1.2fr 1fr}.wide{grid-column:1/-1}button,input{font:inherit}button{width:100%;border:0;color:#fff;cursor:pointer;touch-action:manipulation}.action,.item,.transport,.back{position:relative;overflow:hidden;transition:transform .12s ease,filter .17s ease,box-shadow .18s ease,background .18s ease}.action:active,.item:active,.transport:active,.back:active{transform:scale(.96);filter:brightness(1.14)}.action:disabled{opacity:.55}.action{min-height:55px;padding:11px 13px;border-radius:18px;background:rgba(255,255,255,.12);box-shadow:inset 0 1px rgba(255,255,255,.16),0 10px 26px rgba(0,0,0,.2);font-size:14px;font-weight:790}.primary{background:linear-gradient(135deg,var(--blue),var(--violet))}.green{background:linear-gradient(135deg,#25ae7d,#287d95)}.danger{background:linear-gradient(135deg,#e84f75,#8e2b4b)}.busy:after{content:"";position:absolute;inset:0;background:linear-gradient(105deg,transparent 25%,rgba(255,255,255,.2) 50%,transparent 75%);animation:shine .8s linear infinite}@keyframes shine{from{transform:translateX(-100%)}to{transform:translateX(100%)}}\n.search{width:100%;min-height:50px;padding:11px 15px;border:1px solid rgba(255,255,255,.14);border-radius:17px;outline:0;color:#fff;background:rgba(255,255,255,.085);transition:.2s}.search:focus{border-color:#9b7aff;background:rgba(255,255,255,.12);box-shadow:0 0 0 4px rgba(130,93,255,.18)}.list{display:grid;gap:10px;margin-top:10px}.item{min-height:67px;padding:13px 16px;border:1px solid rgba(255,255,255,.08);border-radius:19px;text-align:left;background:rgba(255,255,255,.085);box-shadow:inset 0 1px rgba(255,255,255,.12)}.item.active{border-color:rgba(164,124,255,.68);background:linear-gradient(135deg,rgba(78,130,255,.42),rgba(133,82,255,.45));box-shadow:0 13px 32px rgba(81,57,190,.25)}.name{display:block;font-size:16px;font-weight:780;line-height:1.3}.meta{display:block;margin-top:4px;color:#aaa7b5;font-size:12px;line-height:1.35}.device-group{margin-top:18px}.device-head{display:flex;align-items:center;gap:11px;margin:0 3px 10px;color:#d8d4df;font-size:14px;font-weight:800}.device-icon{display:grid;place-items:center;width:31px;height:31px;border-radius:10px;background:rgba(255,255,255,.11);font-size:17px}.row{display:grid;grid-template-columns:minmax(0,1fr) 72px 72px;gap:9px}.header{display:grid;grid-template-columns:74px 1fr 74px;align-items:center;margin:4px 0 16px}.header h1{margin:0;text-align:center;font-size:23px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.back{min-height:44px;border-radius:15px;background:rgba(255,255,255,.11);font-weight:780}\n.player{padding:21px}.now-grid{display:grid;grid-template-columns:56px minmax(0,1fr);gap:14px;align-items:center}.album{display:grid;place-items:center;width:56px;height:56px;border-radius:18px;background:linear-gradient(145deg,#a66fff,#3c65de);box-shadow:0 13px 30px rgba(76,62,202,.34);font-size:24px}.range-row{display:grid;grid-template-columns:22px minmax(0,1fr) 22px;gap:8px;align-items:center}.volume-icon{display:grid;place-items:center;color:#e1deea}.range{--fill:0%;width:100%;height:36px;background:transparent;appearance:none;-webkit-appearance:none;touch-action:none}.range::-webkit-slider-runnable-track{height:6px;border-radius:999px;background:linear-gradient(to right,#fff 0,#fff var(--fill),rgba(255,255,255,.2) var(--fill),rgba(255,255,255,.2) 100%)}.range::-webkit-slider-thumb{width:21px;height:21px;margin-top:-7.5px;border:0;border-radius:50%;background:#fff;box-shadow:0 3px 10px rgba(0,0,0,.42);appearance:none;-webkit-appearance:none}.times{display:flex;justify-content:space-between;color:#aaa7b5;font-size:12px;font-variant-numeric:tabular-nums}.mode-controls{margin-top:10px}.mode-controls .active{background:var(--violet)}.source-card{padding:16px}.source-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.source-mode.active{background:linear-gradient(135deg,var(--mint),#267da2);box-shadow:0 13px 30px rgba(37,174,125,.25)}\n.transport{min-height:80px;border-radius:999px;background:rgba(255,255,255,.11);box-shadow:inset 0 1px rgba(255,255,255,.18),0 15px 32px rgba(0,0,0,.28)}.transport.main{min-height:104px;background:linear-gradient(145deg,#9e68ff,#583ad2);box-shadow:0 20px 42px rgba(95,57,212,.36)}.media-icon{display:block;width:38px;height:38px;margin:auto;fill:none;stroke:#fff;stroke-width:2.1;stroke-linecap:round;stroke-linejoin:round;pointer-events:none}.main .media-icon{width:46px;height:46px}.icon-fill{fill:#fff;stroke:#fff}.toolbar{margin-top:13px}.check{display:grid;grid-template-columns:29px minmax(0,1fr);gap:11px;align-items:center;padding:12px;border:1px solid rgba(255,255,255,.08);border-radius:17px;background:rgba(255,255,255,.075)}.check input{width:22px;height:22px;accent-color:var(--violet)}.status{position:sticky;bottom:12px;z-index:10;min-height:0;margin-top:15px;text-align:center}.status:not(:empty){padding:11px 14px;border:1px solid rgba(255,255,255,.12);border-radius:16px;background:rgba(18,19,34,.94);box-shadow:0 18px 38px rgba(0,0,0,.38)}.error{background:rgba(113,28,52,.95)!important}@media(max-width:390px){.row{grid-template-columns:minmax(0,1fr) 64px 64px}.action{padding:9px 7px}.transport{min-height:72px}.transport.main{min-height:94px}}\n.system-player{padding:19px}.system-player .grid{margin-top:10px}</style></head><body><main>\n<section id="profiles"><div class="hero"><div class="eyebrow"><span class="dot"></span>Audio engine online</div><h1>CamillaDSP Studio</h1><div class="subtitle">Choose your listening profile or switch to the local music player.</div></div><div class="section-title">Audio source</div><div class="card source-card"><div id="source-title" class="title">Loading source…</div><div id="source-details" class="details">SonoBus always transmits the processed CamillaDSP output.</div><div class="source-grid"><button id="mode-system" class="action source-mode">Linux System Audio</button><button id="mode-airplay" class="action source-mode">iPad AirPlay</button></div></div><button id="open-pc" class="action green">Open PC Music Library</button><div class="section-title">Active profile</div><div class="card"><div id="active-profile" class="title">Loading…</div><div id="active-state" class="details"></div></div><div class="grid two"><button id="restart-camilla" class="action">Restart CamillaDSP</button><button id="restart-sonobus" class="action">Restart SonoBus</button><button id="restart-airplay" class="action">Restart AirPlay</button><button id="restart-vnc" class="action">Restart VNC</button></div><div class="section-title">System media</div><div class="card system-player"><div class="label">Active desktop player</div><div id="system-title" class="title">No system media</div><div id="system-details" class="details"></div><input id="system-seek" class="range" type="range" min="0" max="0" step=".1" disabled><div class="times"><span id="system-elapsed">0:00</span><span id="system-duration">0:00</span></div><div class="range-row"><span class="volume-icon">🔈</span><input id="system-volume" class="range" type="range" min="0" max="100" value="100"><span class="volume-icon">🔊</span></div><div class="grid three"><button id="system-previous" class="action">Previous</button><button id="system-toggle" class="action primary">Play / Pause</button><button id="system-next" class="action">Next</button></div></div><div class="section-title">Listening profiles</div><input id="profile-search" class="search" placeholder="Search profiles"><div id="profile-list"></div></section>\n<section id="pc" class="hidden"><div class="header"><button id="pc-back" class="back">Back</button><h1>PC Music</h1><div></div></div><div class="card player"><div class="now-grid"><div class="album">♪</div><div><div class="label">Now playing</div><div id="now-title" class="title">Nothing playing</div><div id="now-details" class="details"></div></div></div><input id="seek" class="range" type="range" min="0" max="0" step=".1"><div class="times"><span id="elapsed">0:00</span><span id="duration">0:00</span></div><div class="range-row"><span class="volume-icon">🔈</span><input id="volume" class="range" type="range" min="0" max="100" value="100"><span class="volume-icon">🔊</span></div><div class="grid two mode-controls"><button id="repeat" class="action">Repeat Off</button><button id="queue-shuffle" class="action">Shuffle Queue</button></div></div><div class="grid three"><button data-cmd="previous" class="transport" aria-label="Previous"><svg class="media-icon" viewBox="0 0 24 24"><path d="M6 5v14"></path><path d="M18 6.5 8.5 12 18 17.5z"></path></svg></button><button data-cmd="toggle" id="play-toggle" class="transport main" aria-label="Play or pause"><svg class="media-icon" viewBox="0 0 24 24"><path id="play-shape" class="icon-fill" d="M8 5.5 19 12 8 18.5z"></path></svg></button><button data-cmd="next" class="transport" aria-label="Next"><svg class="media-icon" viewBox="0 0 24 24"><path d="M18 5v14"></path><path d="M6 6.5 15.5 12 6 17.5z"></path></svg></button></div><div class="grid two toolbar"><button id="all-songs" class="action primary">All Songs</button><button id="new-playlist" class="action green">New Playlist</button><button id="return-airplay" class="action danger wide">Switch to iPad AirPlay</button></div><div class="section-title">Playlists</div><div id="playlists" class="list"></div></section>\n<section id="songs" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1>All Songs</h1><div></div></div><input id="song-search" class="search" placeholder="Search all songs"><div id="song-list" class="list"></div></section>\n<section id="playlist" class="hidden"><div class="header"><button data-back="pc" class="back">Back</button><h1 id="playlist-title">Playlist</h1><div></div></div><input id="playlist-search" class="search" placeholder="Search this playlist"><div id="playlist-songs" class="list"></div></section>\n<section id="add" class="hidden"><div class="header"><button id="add-back" class="back">Back</button><h1>Add Songs</h1><div></div></div><button id="add-selected" class="action primary">Add Selected Songs</button><input id="add-search" class="search" placeholder="Search available songs"><div id="add-list" class="list"></div></section><div id="status" class="status"></div></main>\n<script>\nconst $=s=>document.querySelector(s),screens=[\'profiles\',\'pc\',\'songs\',\'playlist\',\'add\'].map(x=>$(\'#\'+x));let current=\'\',all=[],inside=[],statusTimer=null;\nfunction show(id){screens.forEach(x=>x.classList.toggle(\'hidden\',x.id!==id));scrollTo({top:0,behavior:\'smooth\'})}function stat(x,e=false){const b=$(\'#status\');b.textContent=x;b.classList.toggle(\'error\',e);clearTimeout(statusTimer);if(x)statusTimer=setTimeout(()=>{if(b.textContent===x)b.textContent=\'\'},3000)}async function api(u,o={}){const r=await fetch(u,{cache:\'no-store\',...o}),d=await r.json().catch(()=>({ok:false,error:\'Invalid response\'}));if(!r.ok||d.ok===false)throw Error(d.error||\'Request failed\');return d}const post=(u,d={})=>api(u,{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(d)}),fmt=v=>Math.floor((v||0)/60)+\':\'+String(Math.floor(v||0)%60).padStart(2,\'0\'),matches=(s,q)=>!q||[s.title,s.relative].join(\' \').toLowerCase().includes(q.toLowerCase());\nfunction friendly(filename){return filename.replace(/\\\\.ya?ml$/i,\'\').replace(/^\\\\d+-/,\'\').replace(/^earpods-/i,\'\').replace(/^cloud3-/i,\'\').replace(/^cmf-buds-pro-2-/i,\'\').replace(/-/g,\' \').replace(/\\\\b\\\\w/g,x=>x.toUpperCase())}function device(filename){const n=filename.toLowerCase();if(n.includes(\'cloud3\'))return{key:\'cloud3\',name:\'HyperX Cloud III\',icon:\'🎧\'};if(n.includes(\'cmf-buds-pro-2\'))return{key:\'cmf\',name:\'CMF Buds Pro 2\',icon:\'●\'};return{key:\'earpods\',name:\'Apple EarPods\',icon:\'♫\'}}\nasync function press(b,fn,label=\'Working…\'){if(b.disabled)return;const old=b.innerHTML;b.disabled=true;b.classList.add(\'busy\');b.textContent=label;try{return await fn()}catch(e){stat(e.message,true);throw e}finally{b.disabled=false;b.classList.remove(\'busy\');b.innerHTML=old}}\nasync function loadMode(){const d=await api(\'/api/mode\'),system=d.mode===\'system\';$(\'#source-title\').textContent=system?\'Linux System Audio\':\'iPad AirPlay\';$(\'#source-details\').textContent=system?\'Desktop apps, Glide, YouTube and PC Music feed CamillaDSP, then SonoBus.\':\'The iPad feeds Shairport Sync, then CamillaDSP and SonoBus.\';$(\'#mode-system\').classList.toggle(\'active\',system);$(\'#mode-airplay\').classList.toggle(\'active\',!system)}\nasync function chooseMode(mode,button){await press(button,async()=>{await post(\'/api/mode/\'+mode);await loadMode();stat(mode===\'system\'?\'Linux system audio active\':\'iPad AirPlay active\')},\'Switching…\')}\nasync function pollSystemMedia(){try{const d=await api(\'/api/system-media\'),seek=$(\'#system-seek\'),vol=$(\'#system-volume\');$(\'#system-title\').textContent=d.title||\'No system media\';$(\'#system-details\').textContent=[d.artist,d.player,d.status].filter(Boolean).join(\' • \');seek.disabled=!d.seekable;seek.max=d.duration||0;if(document.activeElement!==seek)seek.value=d.position||0;seek.style.setProperty(\'--fill\',(d.duration?d.position/d.duration*100:0)+\'%\');$(\'#system-elapsed\').textContent=fmt(d.position);$(\'#system-duration\').textContent=fmt(d.duration);if(document.activeElement!==vol)vol.value=d.volume;vol.style.setProperty(\'--fill\',(d.volume||0)+\'%\')}catch(e){stat(e.message,true)}}\nasync function loadProfiles(){const d=await api(\'/api/profiles\'),root=$(\'#profile-list\'),q=$(\'#profile-search\').value.toLowerCase();$(\'#active-profile\').textContent=d.active?friendly(d.active):\'No profile selected\';$(\'#active-state\').textContent=d.running?(d.active?device(d.active).name+\' • Running\':\'Running\'):\'Stopped\';root.replaceChildren();const groups=[{key:\'earpods\',name:\'Apple EarPods\',icon:\'♫\'},{key:\'cmf\',name:\'CMF Buds Pro 2\',icon:\'●\'},{key:\'cloud3\',name:\'HyperX Cloud III\',icon:\'🎧\'}];for(const g of groups){const names=d.profiles.filter(p=>device(p).key===g.key&&[p,friendly(p),g.name].join(\' \').toLowerCase().includes(q));if(!names.length)continue;const wrap=document.createElement(\'div\');wrap.className=\'device-group\';wrap.innerHTML=\'<div class="device-head"><span class="device-icon"></span><span></span></div><div class="list"></div>\';wrap.querySelector(\'.device-icon\').textContent=g.icon;wrap.querySelector(\'.device-head span:last-child\').textContent=g.name;const list=wrap.querySelector(\'.list\');for(const p of names){const b=document.createElement(\'button\');b.className=\'item\'+(d.running&&p===d.active?\' active\':\'\');b.innerHTML=\'<span class="name"></span><span class="meta"></span>\';b.querySelector(\'.name\').textContent=friendly(p);b.querySelector(\'.meta\').textContent=g.name;b.onclick=()=>press(b,async()=>{await post(\'/api/select\',{profile:p});await loadProfiles();stat(\'Profile activated\')},\'Switching…\');list.append(b)}root.append(wrap)}}\nasync function loadLists(){const d=await api(\'/api/playlists\'),l=$(\'#playlists\');l.replaceChildren();d.playlists.forEach(p=>{const r=document.createElement(\'div\');r.className=\'row\';const o=document.createElement(\'button\');o.className=\'item\';o.innerHTML=\'<span class="name"></span><span class="meta"></span>\';o.querySelector(\'.name\').textContent=p.name;o.querySelector(\'.meta\').textContent=p.count+(p.count===1?\' song\':\' songs\');o.onclick=()=>openList(p.name);const a=document.createElement(\'button\');a.className=\'action\';a.textContent=\'Play\';a.onclick=()=>press(a,()=>post(\'/api/play/playlist\',{name:p.name,shuffle:false}),\'Loading…\').then(()=>stat(\'Playing \'+p.name));const s=document.createElement(\'button\');s.className=\'action primary\';s.textContent=\'Shuffle\';s.onclick=()=>press(s,()=>post(\'/api/play/playlist\',{name:p.name,shuffle:true}),\'Mixing…\').then(()=>stat(\'Shuffling \'+p.name));r.append(o,a,s);l.append(r)})}\nfunction render(sel,songs,q,checks=false){const l=$(sel);l.replaceChildren();const found=songs.filter(s=>matches(s,q));if(!found.length){const e=document.createElement(\'div\');e.className=\'card details\';e.textContent=q?\'No matches\':\'Nothing here yet\';l.append(e);return}found.forEach(s=>{if(checks){const x=document.createElement(\'label\');x.className=\'check\';const c=document.createElement(\'input\');c.type=\'checkbox\';c.value=s.path;const t=document.createElement(\'span\');t.innerHTML=\'<span class="name"></span><span class="meta"></span>\';t.querySelector(\'.name\').textContent=s.title;t.querySelector(\'.meta\').textContent=s.relative;x.append(c,t);l.append(x)}else{const b=document.createElement(\'button\');b.className=\'item\';b.innerHTML=\'<span class="name"></span><span class="meta"></span>\';b.querySelector(\'.name\').textContent=s.title;b.querySelector(\'.meta\').textContent=s.relative;b.onclick=()=>press(b,()=>post(\'/api/play/song\',{path:s.path}),\'Playing…\').then(()=>stat(\'Playing \'+s.title));l.append(b)}})}\nasync function openSongs(){all=(await api(\'/api/songs\')).songs;show(\'songs\');render(\'#song-list\',all,$(\'#song-search\').value)}async function openList(n){current=n;inside=(await api(\'/api/playlist?name=\'+encodeURIComponent(n))).songs;$(\'#playlist-title\').textContent=n;show(\'playlist\');render(\'#playlist-songs\',inside,$(\'#playlist-search\').value)}async function openAdd(){const existing=new Set(inside.map(x=>x.path));all=(await api(\'/api/songs\')).songs.filter(s=>!existing.has(s.path));show(\'add\');render(\'#add-list\',all,$(\'#add-search\').value,true)}\nasync function poll(){if($(\'#pc\').classList.contains(\'hidden\'))return;try{const d=await api(\'/api/player\'),seek=$(\'#seek\'),vol=$(\'#volume\'),shape=$(\'#play-shape\');$(\'#now-title\').textContent=d.title||\'Nothing playing\';$(\'#now-details\').textContent=d.path;seek.max=d.duration||0;if(document.activeElement!==seek)seek.value=d.currentTime||0;seek.style.setProperty(\'--fill\',(d.duration?d.currentTime/d.duration*100:0)+\'%\');$(\'#elapsed\').textContent=fmt(d.currentTime);$(\'#duration\').textContent=fmt(d.duration);if(document.activeElement!==vol)vol.value=d.volume;vol.style.setProperty(\'--fill\',(d.volume||0)+\'%\');$(\'#repeat\').textContent=\'Repeat \'+({off:\'Off\',all:\'All\',one:\'1\'}[d.repeat]||\'Off\');$(\'#repeat\').classList.toggle(\'active\',d.repeat!==\'off\');shape.setAttribute(\'d\',d.playing?\'M8 5h3v14H8z M14 5h3v14h-3z\':\'M8 5.5 19 12 8 18.5z\')}catch(e){stat(e.message,true)}}\n$(\'#mode-system\').onclick=()=>chooseMode(\'system\',$(\'#mode-system\'));$(\'#mode-airplay\').onclick=()=>chooseMode(\'airplay\',$(\'#mode-airplay\'));$(\'#open-pc\').onclick=()=>press($(\'#open-pc\'),async()=>{await post(\'/api/mode/pc\');show(\'pc\');await loadLists();await poll()},\'Opening…\');$(\'#pc-back\').onclick=()=>show(\'profiles\');$(\'#all-songs\').onclick=openSongs;$(\'#profile-search\').oninput=loadProfiles;$(\'#song-search\').oninput=()=>render(\'#song-list\',all,$(\'#song-search\').value);$(\'#playlist-search\').oninput=()=>render(\'#playlist-songs\',inside,$(\'#playlist-search\').value);$(\'#add-search\').oninput=()=>render(\'#add-list\',all,$(\'#add-search\').value,true);document.querySelectorAll(\'[data-back]\').forEach(b=>b.onclick=()=>show(b.dataset.back));$(\'#add-back\').onclick=()=>show(\'playlist\');$(\'#add-selected\').onclick=()=>press($(\'#add-selected\'),async()=>{const paths=[...document.querySelectorAll(\'#add-list input:checked\')].map(x=>x.value);if(!paths.length)throw Error(\'Select at least one song\');await post(\'/api/playlist/add\',{name:current,paths});await openList(current);stat(\'Songs added\')},\'Adding…\');$(\'#new-playlist\').onclick=async()=>{const name=prompt(\'New playlist name\');if(name)try{await post(\'/api/playlist/create\',{name});await loadLists();stat(\'Playlist created\')}catch(e){stat(e.message,true)}};\ndocument.querySelectorAll(\'[data-cmd]\').forEach(b=>b.onclick=()=>press(b,()=>post(\'/api/player/command\',{command:b.dataset.cmd}),\'…\').then(poll));$(\'#seek\').oninput=()=>{$(\'#seek\').style.setProperty(\'--fill\',($(\'#seek\').max?$(\'#seek\').value/$(\'#seek\').max*100:0)+\'%\');$(\'#elapsed\').textContent=fmt(Number($(\'#seek\').value))};$(\'#seek\').onchange=()=>post(\'/api/player/seek\',{seconds:Number($(\'#seek\').value)});let vt=null;$(\'#volume\').oninput=()=>{const v=Number($(\'#volume\').value);$(\'#volume\').style.setProperty(\'--fill\',v+\'%\');clearTimeout(vt);vt=setTimeout(()=>post(\'/api/player/volume\',{volume:v}).catch(e=>stat(e.message,true)),75)};$(\'#repeat\').onclick=async()=>{const d=await api(\'/api/player\');await post(\'/api/player/repeat\',{mode:{off:\'all\',all:\'one\',one:\'off\'}[d.repeat]||\'off\'});poll()};$(\'#queue-shuffle\').onclick=()=>press($(\'#queue-shuffle\'),()=>post(\'/api/player/command\',{command:\'shuffle\'}),\'Shuffling…\').then(()=>stat(\'Queue shuffled\'));$(\'#return-airplay\').onclick=()=>press($(\'#return-airplay\'),()=>post(\'/api/mode/airplay\'),\'Switching…\').then(()=>{show(\'profiles\');stat(\'AirPlay restored\')});$(\'#restart-camilla\').onclick=()=>press($(\'#restart-camilla\'),async()=>{await post(\'/api/restart-camilladsp\');await loadProfiles();stat(\'CamillaDSP restarted\')},\'Restarting…\');$(\'#restart-sonobus\').onclick=()=>press($(\'#restart-sonobus\'),()=>post(\'/api/restart-sonobus\'),\'Restarting…\').then(()=>stat(\'SonoBus restarted\'));$(\'#restart-airplay\').onclick=()=>press($(\'#restart-airplay\'),()=>post(\'/api/restart-airplay\'),\'Restarting…\').then(()=>stat(\'AirPlay restarted\'));$(\'#restart-vnc\').onclick=()=>press($(\'#restart-vnc\'),()=>post(\'/api/restart-vnc\'),\'Restarting…\').then(()=>stat(\'VNC restarted\'));const systemMedia=(id,command)=>{$(\'#\'+id).onclick=()=>press($(\'#\'+id),()=>post(\'/api/system-media\',{command}),\'…\').then(()=>stat(\'System media command sent\'))};systemMedia(\'system-previous\',\'previous\');systemMedia(\'system-toggle\',\'toggle\');systemMedia(\'system-next\',\'next\');$(\'#system-seek\').oninput=()=>{$(\'#system-seek\').style.setProperty(\'--fill\',($(\'#system-seek\').max?$(\'#system-seek\').value/$(\'#system-seek\').max*100:0)+\'%\');$(\'#system-elapsed\').textContent=fmt(Number($(\'#system-seek\').value))};$(\'#system-seek\').onchange=()=>post(\'/api/system-media/seek\',{seconds:Number($(\'#system-seek\').value)}).then(pollSystemMedia).catch(e=>stat(e.message,true));let systemVolumeTimer=null;$(\'#system-volume\').oninput=()=>{const value=Number($(\'#system-volume\').value);$(\'#system-volume\').style.setProperty(\'--fill\',value+\'%\');clearTimeout(systemVolumeTimer);systemVolumeTimer=setTimeout(()=>post(\'/api/system-volume\',{volume:value}).catch(e=>stat(e.message,true)),75)};Promise.all([loadProfiles(),loadMode(),pollSystemMedia()]).catch(e=>stat(e.message,true));setInterval(poll,1000);setInterval(pollSystemMedia,1000);\n</script></body></html>'

class H(BaseHTTPRequestHandler):
    def data(self,n,t,b):self.send_response(n);self.send_header('Content-Type',t);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(b)
    def out(self,n,d):self.data(n,'application/json; charset=utf-8',json.dumps(d,ensure_ascii=False,separators=(',',':')).encode())
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n<0 or n>1048576:raise ValueError('Invalid body size')
        d=json.loads(self.rfile.read(n)) if n else {}
        if not isinstance(d,dict):raise ValueError('Body must be an object')
        return d
    def do_GET(self):
        u=urlparse(self.path);p=u.path;q=parse_qs(u.query)
        try:
            if p=='/':self.data(200,'text/html; charset=utf-8',PAGE.encode());return
            if p=='/api/profiles':r={'profiles':[x.name for x in profiles()],'active':active(),'running':alive(rpid(CAMPID),'camilladsp')}
            elif p=='/api/mode':r={'mode':audio_mode(),'sonobus':bool(sonopids())}
            elif p=='/api/system-media':r=system_media_state()
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
            if p=='/api/select':r=switch_profile(d.get('profile'))
            elif p=='/api/restart-camilladsp':r=restart_camilla()
            elif p=='/api/restart-sonobus':r=restart_sonobus()
            elif p=='/api/restart-airplay':airplay(True);r={'restarted':True}
            elif p=='/api/restart-vnc':r=restart_vnc_service()
            elif p=='/api/system-media':r=system_media(d.get('command'))
            elif p=='/api/system-media/seek':r=system_media_seek(d.get('seconds'))
            elif p=='/api/system-volume':r=set_system_volume(d.get('volume'))
            elif p=='/api/mode/pc':r=enter_pc()
            elif p=='/api/mode/system':r=set_source_mode('system')
            elif p=='/api/mode/airplay':r=return_airplay()
            elif p=='/api/player/command':
                c=d.get('command'); cmds={'toggle':['cycle','pause'],'next':['playlist-next','force'],'previous':['playlist-prev','force'],'shuffle':['playlist-shuffle']}
                if c not in cmds:raise ValueError('Invalid command')
                mpv(cmds[c]);r={'command':c}
            elif p=='/api/player/seek':v=float(d.get('seconds'));mpv(['set_property','time-pos',v]);r={'seconds':v}
            elif p=='/api/player/volume':v=float(d.get('volume'));mpv(['set_property','volume',max(0,min(100,v))]);r={'volume':v}
            elif p=='/api/player/repeat':r={'mode':set_repeat(d.get('mode'))}
            elif p=='/api/play/song':x=song(d.get('path'));mpv(['loadfile',str(x),'replace']);r=sobj(x)
            elif p=='/api/play/playlist':r=play_list(d.get('name'),bool(d.get('shuffle')))
            elif p=='/api/playlist/create':r=create_list(d.get('name'))
            elif p=='/api/playlist/remove':r=remove_list(d.get('name'))
            elif p=='/api/playlist/add':r=add_to_list(d.get('name'),d.get('paths'))
            else:self.out(404,{'ok':False,'error':'Not found'});return
            self.out(200,{'ok':True,**r})
        except (ValueError,TypeError,KeyError,FileNotFoundError,json.JSONDecodeError) as e:self.out(400,{'ok':False,'error':str(e)})
        except Exception as e:self.out(500,{'ok':False,'error':str(e)})
    def log_message(self,*a):pass
class S(ThreadingHTTPServer):allow_reuse_address=True;daemon_threads=True

def reset_previous_runtime():
    """Stop only the processes owned by this server before replacement."""
    ensure()
    stop_mpv()
    stop_sonobus()
    stop_camilla()



def start_runtime():
    """Start CamillaDSP and SonoBus, then restore the saved source mode."""
    ensure()
    alsa100()

    selected_profile = active()
    if not selected_profile:
        available_profiles = profiles()
        if not available_profiles:
            raise RuntimeError("No CamillaDSP profiles available")
        selected_profile = available_profiles[0].name

    start_camilla(profile(selected_profile))
    restart_sonobus()
    apply_source_mode(audio_mode())


def cleanup():
    # PipeWire and the selected source mode are systemd-managed.
    # Only stop processes owned directly by this server.
    stop_mpv()
    stop_sonobus()
    stop_camilla()


def main():
    ensure()

    current_pid = os.getpid()
    previous_pid = rpid(SERVERPID)

    if (
        previous_pid
        and previous_pid != current_pid
        and alive(previous_pid)
    ):
        try:
            os.kill(
                previous_pid,
                signal.SIGTERM,
            )

        except OSError:
            pass

        # Wait for the old process to finish its complete cleanup,
        # including stop_camilla(), before starting replacements.
        for _ in range(100):
            if not alive(previous_pid):
                break

            time.sleep(0.1)

        if alive(previous_pid):
            try:
                os.kill(
                    previous_pid,
                    signal.SIGKILL,
                )

            except OSError:
                pass

            for _ in range(30):
                if not alive(previous_pid):
                    break

                time.sleep(0.1)

        if alive(previous_pid):
            raise RuntimeError(
                "Previous webserver did not stop cleanly"
            )

    SERVERPID.unlink(
        missing_ok=True
    )

    # The previous server has fully exited, so its cleanup can
    # no longer kill processes started below.
    reset_previous_runtime()
    start_runtime()

    server = S(
        (
            "0.0.0.0",
            PORT,
        ),
        H,
    )

    SERVERPID.write_text(
        str(current_pid) + "\n"
    )

    def shut(*_args):
        threading.Thread(
            target=server.shutdown,
            daemon=True,
        ).start()

    signal.signal(
        signal.SIGTERM,
        shut,
    )

    signal.signal(
        signal.SIGHUP,
        shut,
    )

    print(
        "CamillaDSP web remote "
        f"listening on 0.0.0.0:{PORT}",
        flush=True,
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        pass

    finally:
        server.server_close()
        cleanup()

        try:
            recorded_pid = rpid(
                SERVERPID
            )

            if recorded_pid == current_pid:
                SERVERPID.unlink(
                    missing_ok=True
                )

        except OSError:
            pass

if __name__ == "__main__":
    main()
