def _ensure_no_filter_physical_default():
    """Keep desktop volume keys on the active physical sink in direct bypass."""
    if not _direct_no_filter_path() or STOPPED.exists():
        return None
    physical=saved_output_route().get('sink')
    if not physical or physical=='camilladsp':
        return None
    pw_sink(physical)
    try:current=pw_default()
    except RuntimeError:current=''
    if current==physical:
        return physical
    target=pw_sink(physical)
    result=run([exe('wpctl'),'set-default',str(target['id'])],False,8,media_env())
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Could not restore the physical No filter master sink')
    pw_move_streams(physical)
    if pw_default()!=physical:
        raise RuntimeError('Physical No filter master sink did not remain the PipeWire default')
    return physical

def _audio_path():
    mode=audio_mode()
    return MODES.get(mode,('', '',False,False))

def _direct_no_filter_path():
    _,source,local,sono=_audio_path()
    return selected_filter()==NO_FILTER and source=='system' and local and not sono

def _shared_system_path():
    _,source,_,sono=_audio_path()
    return source=='system' and (selected_filter()!=NO_FILTER or sono)

def master_sink():
    if _direct_no_filter_path():
        name=saved_output_route().get('sink')
        if name and any(row.get('name')==name for row in _pipewire_json('sinks')):return name
    if _shared_system_path():
        try:pw_sink('camilladsp');return 'camilladsp'
        except RuntimeError:pass
    saved=saved_output_route().get('sink')
    if saved:
        try:pw_sink(saved);return saved
        except RuntimeError:pass
    return '@DEFAULT_SINK@'

def _saved_master_volume():
    try:value=float(MASTER_VOLUME.read_text().strip().rstrip('%'))
    except (OSError,ValueError):return None
    if not math.isfinite(value) or not 0<=value<=100:
        return None
    return value

def _live_master_volume():
    # Avoid @DEFAULT_SINK@ when PipeWire metadata is temporarily ambiguous.
    # Prefer the path's explicit master, then the saved physical sink. If there
    # is exactly one exposed sink, it is unambiguous even without a default.
    target=master_sink()
    if target=='@DEFAULT_SINK@':
        rows=_pipewire_json('sinks')
        names=[str(row.get('name') or '') for row in rows if row.get('name')]
        saved=saved_output_route().get('sink')
        if saved and saved in names:target=saved
        elif _shared_system_path() and 'camilladsp' in names:target='camilladsp'
        elif len(names)==1:target=names[0]
        else:raise RuntimeError('No unambiguous live master sink is available')
    result=run(['__wp_control__','get-sink-volume',target],False,5,media_env())
    match=re.search(r'(\d+(?:\.\d+)?)%',result.stdout)
    if result.returncode or not match:
        raise RuntimeError(result.stderr.strip() or 'Could not read the live master volume')
    value=float(match.group(1))
    if not math.isfinite(value) or not 0<=value<=100:
        raise RuntimeError('Live master volume is outside 0-100%')
    return value

def master_volume():
    _ensure_no_filter_physical_default()
    protected=(MASTER_RESTORING or STOPPED.exists() or (STATE/'output-preview').exists())
    if protected:
        saved=_saved_master_volume()
        if saved is not None:return saved
        # With no persisted master, use the actual live gain. Never invent 50%.
        return _live_master_volume()
    try:
        value=_live_master_volume()
        MASTER_VOLUME.parent.mkdir(parents=True,exist_ok=True)
        with LOCK:
            saved=_saved_master_volume()
            # Recheck under the lock: a poll may have read a newly recreated
            # sink just before a route transaction acquired it.
            if MASTER_RESTORING or STOPPED.exists() or (STATE/'output-preview').exists():
                return saved if saved is not None else value
            if time.monotonic()<MASTER_WRITE_UNTIL:
                return saved if saved is not None else value
            if saved is None or abs(saved-value)>=.5:
                atomic(MASTER_VOLUME,f'{value:.2f}%\n')
                sync_mpv_for_audio_path(value)
        return value
    except (OSError,FileNotFoundError,subprocess.TimeoutExpired,RuntimeError) as live_error:
        saved=_saved_master_volume()
        if saved is not None:return saved
        raise RuntimeError('Master volume is unavailable from both live audio and persisted state: '+str(live_error)) from live_error

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

def sync_mpv_for_audio_path(value):
    if not (alive(rpid(MPVPID),'mpv') and MPVSOCK.exists()):return
    # MPV writes directly to camilladsp_input, bypassing PipeWire sink gain.
    # Match the logical master whenever the shared Laptop path feeds
    # CamillaDSP or the No-filter bridge; direct Laptop output stays at unity.
    wanted=float(value) if _shared_system_path() else 100.0
    try:mpv_direct(['set_property','volume',wanted])
    except (OSError,RuntimeError,ValueError) as error:
        raise RuntimeError(f'Could not sync MPV for active audio path: {error}') from error

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
    _ensure_no_filter_physical_default()
    # Normalize other sinks on route changes, not on each slider update.
    # CamillaDSP Main remains at unity.
    name=master_sink()
    if name=='@DEFAULT_SINK@':
        name=pw_default()
    if not name:raise RuntimeError('No playback sink for master volume')
    # Keep the inactive path at unity, including slider updates. In filtered
    # mode MPV bypasses the PipeWire sink, so its gain follows the saved master.
    # In shared No filter, camilladsp ingress and direct-ALSA MPV carry
    # the same logical master; downstream physical outputs remain at unity.
    inactive=saved_output_route().get('sink') if name=='camilladsp' else 'camilladsp'
    if inactive and inactive!=name:
        try:pw_sink(inactive)
        except RuntimeError:inactive=None
        if inactive:
            result=run(['__wp_control__','set-sink-volume',inactive,'100%'],False,8,media_env())
            if result.returncode:raise RuntimeError(f'Could not set {inactive} to 100%: {result.stderr.strip()}')
    if normalize_sinks:
        for sink in _pipewire_json('sinks'):
            target=str(sink.get('name') or '') if isinstance(sink,dict) else ''
            if not target or target==name or target==inactive:continue
            result=run(['__wp_control__','set-sink-volume',target,'100%'],False,8,media_env())
            if result.returncode:raise RuntimeError(f'Could not set {target} to 100%: {result.stderr.strip()}')
    # CamillaDSP Main is a normalization stage, so set it before restoring
    # the logical master. The master sink and direct-ALSA MPV are restored last.
    if alive(rpid(CAMPID),'camilladsp'):
        if selected_filter()!=NO_FILTER:wait_for_camilla_config(profile(selected_filter()))
        try:camilla_command({'SetVolume':0.0})
        except (OSError,ValueError) as error:raise RuntimeError(f'Could not set CamillaDSP Main to unity: {error}') from error
    result=run(['__wp_control__','set-sink-volume',name,f'{value:.2f}%'],False,8,media_env())
    if result.returncode:raise RuntimeError(f'Could not set {name} to {value:.2f}%: {result.stderr.strip()}')
    sync_mpv_for_audio_path(value)

