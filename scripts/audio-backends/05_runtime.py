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
def pw_device(name,graph=None):
    device=next((row for row in pw_objects('Device',graph) if pw_props(row).get('device.name')==name),None)
    if device is None:raise RuntimeError('Audio device disappeared: '+str(name))
    return device

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
    # Generic default recovery must be physical. Filtered routing selects the
    # DSP sink explicitly through restore_dsp_desktop_sink(); treating it as a
    # fallback is what allowed stale CamillaDSP routing to persist.
    saved=saved_output_route().get('sink')
    if saved and saved!='camilladsp' and any(pw_props(x).get('node.name')==saved for x in sinks):return saved
    if len(sinks)==1:return str(pw_props(sinks[0]).get('node.name'))
    raise RuntimeError('Could not identify the current default sink')

def pw_move_streams(sink):
    graph=pw_graph();target=pw_sink(sink,graph)
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
            # A paused or idle stream may have no links until the user plays audio.
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
        # target.node is the durable routing request. Paused and idle clients
        # may not create a new live Link until playback resumes, so absence of
        # an immediate link must not make a valid sink switch or startup fail.

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
    # Both launchers must be safe on a clean account. Persist only the neutral
    # logical master here; topology, filter and mode remain live/default driven.
    if not MASTER_VOLUME.exists():
        atomic(MASTER_VOLUME,'100.00%\n')
    legacy=HOME/'.local/state/sway/audio'
    old_server=legacy/'camilladsp-webremote'
    # One-time migration. Never resurrect a deliberately cleared stop marker.
    marker=STATE/'state-migrated'
    if marker.exists():return
    for source,destination in ((old_server,STATE),(legacy,SWITCH_STATE)):
        if not source.is_dir():continue
        for item in source.iterdir():
            if not item.is_file() or item.is_symlink():continue
            if source==legacy and item.name not in ('master-volume','media-control.log','media-control.lock','audio-cards-last.txt','camilladsp-local.log'):continue
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
PIPEWIRE_UNITS=('pipewire.socket','pipewire-pulse.socket','wireplumber.service')
def ensure_pipewire_ready():
    for unit in PIPEWIRE_UNITS:user_service('start',unit)
    for _ in range(100):
        if run([exe('wpctl'),'status'],False,3,media_env()).returncode==0:return
        time.sleep(.1)
    raise RuntimeError('PipeWire did not become ready')
def ensure_system_audio_exposed():
    # This service exposes the stable CamillaDSP PipeWire sink. NixOS keeps
    # the service enabled; the backend only verifies/restarts the exposure.
    if run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode:
        user_service('start',SYSTEM_AUDIO_SERVICE)
    for _ in range(100):
        result=run([exe('pactl'),'list','short','sinks'],False,3,media_env())
        if result.returncode==0 and any(line.split('	')[1:2]==['camilladsp'] for line in result.stdout.splitlines()):
            return
        time.sleep(.1)
    raise RuntimeError('Persistent CamillaDSP system-audio sink did not appear')
AIRPLAY_SERVICES=('nqptp.service','shairport-sync.service')
def airplay_health():
    result=run(['systemctl','is-active',*AIRPLAY_SERVICES],False,5)
    states=result.stdout.splitlines()
    services={service:index<len(states) and states[index].strip()=='active'
              for index,service in enumerate(AIRPLAY_SERVICES)}
    return {'active':all(services.values()),'services':services}
def require_airplay_services():
    health=airplay_health();failed=[name for name,active in health['services'].items() if not active]
    if failed:raise RuntimeError('AirPlay 2 service did not become active: '+', '.join(failed))
    return health
def airplay(start):
    action='restart' if start else 'stop'; r=run(['systemctl',action,*AIRPLAY_SERVICES],False)
    if r.returncode:raise RuntimeError(r.stderr.strip() or 'Could not change AirPlay services')
    if start:require_airplay_services()
def apply_source_services(source,direct=False):
    if source=='airplay':
        # Keep the system-audio sink exposed. The NixOS service is neutral and
        # no longer changes defaults, so it is safe beside AirPlay.
        ensure_system_audio_exposed()
        if MODES[audio_mode()][1]!='airplay' and alive(rpid(MPVPID),'mpv'):stop_mpv()
        health=airplay_health()
        for service,active in health['services'].items():
            if active:continue
            r=run(['systemctl','start',service],False,30)
            if r.returncode:raise RuntimeError(r.stderr.strip() or f'Could not start {service}')
        require_airplay_services()
    elif source=='system':
        if any(run(['systemctl','is-active','--quiet',service],False,5).returncode==0
               for service in AIRPLAY_SERVICES):airplay(False)
        if not direct and run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode:
            user_service('start',SYSTEM_AUDIO_SERVICE)
    else:raise ValueError('Invalid audio source')


