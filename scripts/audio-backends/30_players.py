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
    # paused on restart; do not start audio after a reboot.
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
def mpv_output_args():
    # Direct No-filter Laptop-only playback has no consumer on camilladsp_input.
    # Follow the physical PipeWire default in that one path; every filtered or
    # shared route continues to use the stable direct-ALSA DSP ingress.
    if _direct_no_filter_path():
        return ['--ao=pipewire']
    return ['--ao=alsa','--audio-device=alsa/camilladsp_input',
            '--audio-samplerate=96000','--audio-channels=stereo','--audio-format=s32']
def mpv_output_matches(pid=None):
    pid=rpid(MPVPID) if pid is None else pid
    if not alive(pid,'mpv'):return False
    try:
        args={part.decode('utf-8','replace') for part in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if part}
    except OSError:return False
    wanted=mpv_output_args()
    return all(argument in args for argument in wanted) and not (
        wanted==['--ao=pipewire'] and '--audio-device=alsa/camilladsp_input' in args)
def ensure_mpv():
    pid=rpid(MPVPID)
    if alive(pid,'mpv') and MPVSOCK.exists() and mpv_output_matches(pid):return
    stop_mpv();log=MPVLOG.open('ab',buffering=0)
    cmd=[exe('mpv'),'--idle=yes','--no-video','--no-terminal','--keep-open=no',*mpv_output_args(),f'--input-ipc-server={MPVSOCK}',f'--volume={(master_volume() if _shared_system_path() else 100.0):.2f}','--volume-max=100']
    try:q=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    finally:log.close()
    MPVPID.write_text(f'{q.pid}\n');deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if MPVSOCK.exists() and q.poll() is None:
            try:restore_mpv_queue()
            except (OSError,ValueError,RuntimeError):pass
            sync_mpv_for_audio_path(master_volume())
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
    wanted_file='inf' if m=='one' else 'no'
    wanted_playlist='inf' if m=='all' else 'no'
    with PLAYERLOCK:
        mpv(['set_property','loop-file',wanted_file])
        mpv(['set_property','loop-playlist',wanted_playlist])
        actual_file=mpv_direct(['get_property','loop-file'])
        actual_playlist=mpv_direct(['get_property','loop-playlist'])
        if actual_file!=wanted_file or actual_playlist!=wanted_playlist:
            raise RuntimeError('MPV repeat mode was not confirmed')
        save_mpv_queue(force=True)
    return m

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
        'repeat':repeat,'canShuffleQueue':len(values.get('playlist') or [])>1,
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
