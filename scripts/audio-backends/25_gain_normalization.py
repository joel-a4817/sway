def pause_for_normalization():
    # Pause players currently playing; never resume them automatically.
    if alive(rpid(MPVPID),'mpv') and MPVSOCK.exists():
        try:
            if mpv_direct(['get_property','pause']) is False:
                mpv_direct(['set_property','pause',True])
        except (OSError,ValueError,RuntimeError):pass
    external=Path('/tmp/mpvsocket')
    if external.is_socket() and external!=MPVSOCK:
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(1);client.connect(str(external))
                client.sendall(b'{"command":["get_property","pause"]}\n')
                reply=json.loads(client.makefile('rb').readline())
            if reply.get('error')=='success' and reply.get('data') is False:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(1);client.connect(str(external))
                    client.sendall(b'{"command":["set_property","pause",true]}\n')
                    json.loads(client.makefile('rb').readline())
        except (OSError,ValueError,RuntimeError):pass
    try:players=mpris_players()
    except (OSError,RuntimeError,subprocess.TimeoutExpired):players=[]
    for player in players:
        try:
            if mpris_status(player)!='Playing':continue
            # MPRIS Pause is asynchronous; do not reject the route change.
            run([exe('playerctl'),'--player',player,'pause'],False,5,media_env())
        except (OSError,RuntimeError,subprocess.TimeoutExpired):
            # An unsupported or vanished player must not abort a working route.
            continue
def pause_for_audio_stop():
    ensure()
    # Preserve the queue before pausing; restoration must remain paused.
    save_mpv_queue(force=True)
    pause_for_normalization()

@contextmanager
def audio_start_change():
    pause_for_audio_stop()
    MEDIA_TRANSACTION.starting=True
    try:
        yield
        if not STOPPED.exists():normalize_audio_volumes()
    finally:
        try:pause_for_normalization()
        finally:MEDIA_TRANSACTION.starting=False
    # Stop and start deliberately leave playback paused.

def normalize_with_media():
    with LOCK:
        if STOPPED.exists():return {'skipped':'audio stopped'}
        if getattr(MEDIA_TRANSACTION,'active',False) or getattr(MEDIA_TRANSACTION,'starting',False):
            return {'deferred':'normalization belongs to active transaction'}
        with media_change():return {'normalized':True}
def _alsa_raw_gain_rows(text):
    # Raw card controls can expose gain stages absent from the simple mixer.
    for block in re.split(r'(?=^numid=\d+,)',text,flags=re.M):
        head=re.match(r'numid=(\d+),iface=([^,]+),name=([^\n]+)',block)
        if not head:continue
        type_row=re.search(r'type=(INTEGER|INTEGER64),access=([^,\n]+),values=(\d+),min=(-?\d+),max=(-?\d+)',block)
        if not type_row or len(type_row.group(2))<2 or type_row.group(2)[1]!='w':continue
        if not re.search(r'\| dB(?:scale|linear|minmax|range)',block,re.I):continue
        yield head.group(1),head.group(2),head.group(3),int(type_row.group(3)),int(type_row.group(4)),int(type_row.group(5)),block

def _alsa_zero_raw_value(card,numid):
    # Ask ALSA itself to convert 0 dB for this card/control ID.
    import ctypes,ctypes.util
    candidates=[ctypes.util.find_library('asound'), 'libasound.so.2']
    candidates.extend(str(x) for x in Path('/nix/store').glob('*-alsa-lib-*/lib/libasound.so.2'))
    lib=None
    for candidate in candidates:
        if not candidate:continue
        try:lib=ctypes.CDLL(candidate);break
        except OSError:continue
    if lib is None:raise RuntimeError('libasound is unavailable for ALSA dB conversion')
    pointer=ctypes.c_void_p;number=ctypes.c_long
    lib.snd_ctl_open.argtypes=[ctypes.POINTER(pointer),ctypes.c_char_p,ctypes.c_int]
    lib.snd_ctl_open.restype=ctypes.c_int
    lib.snd_ctl_close.argtypes=[pointer]
    lib.snd_ctl_elem_id_malloc.argtypes=[ctypes.POINTER(pointer)]
    lib.snd_ctl_elem_id_malloc.restype=ctypes.c_int
    lib.snd_ctl_elem_id_free.argtypes=[pointer]
    lib.snd_ctl_elem_id_set_numid.argtypes=[pointer,ctypes.c_uint]
    lib.snd_ctl_convert_from_dB.argtypes=[pointer,pointer,number,ctypes.POINTER(number),ctypes.c_int]
    lib.snd_ctl_convert_from_dB.restype=ctypes.c_int
    lib.snd_ctl_convert_to_dB.argtypes=[pointer,pointer,number,ctypes.POINTER(number)]
    lib.snd_ctl_convert_to_dB.restype=ctypes.c_int
    ctl=pointer();ident=pointer()
    if lib.snd_ctl_open(ctypes.byref(ctl),('hw:'+str(card)).encode(),0)<0:
        raise RuntimeError('Cannot open ALSA card '+str(card))
    try:
        if lib.snd_ctl_elem_id_malloc(ctypes.byref(ident))<0:
            raise RuntimeError('Cannot allocate ALSA control ID')
        try:
            lib.snd_ctl_elem_id_set_numid(ident,int(numid))
            value=number()
            if lib.snd_ctl_convert_from_dB(ctl,ident,0,ctypes.byref(value),0)<0:
                raise RuntimeError('ALSA does not expose dB conversion')
            measured=number()
            if lib.snd_ctl_convert_to_dB(ctl,ident,value.value,ctypes.byref(measured))<0 or measured.value!=0:
                raise RuntimeError('Exact 0 dB is not representable by this control')
            return value.value
        finally:lib.snd_ctl_elem_id_free(ident)
    finally:lib.snd_ctl_close(ctl)

def normalize_audio_volumes():
    # Gain transaction only. Player pausing belongs to the caller.
    errors=[]
    try:cards=Path('/proc/asound/cards').read_text()
    except OSError as error:raise RuntimeError('Could not enumerate ALSA cards: '+str(error)) from error
    card_ids=re.findall(r'^\s*(\d+)\s+\[',cards,re.M)
    if not card_ids:errors.append('No ALSA cards could be enumerated')
    for card in card_ids:
        listing=run([exe('amixer'),'-c',card,'scontrols'],False,8)
        if listing.returncode:
            errors.append(f'ALSA card {card}: {listing.stderr.strip() or "could not list simple controls"}')
        else:
            for name,index in re.findall(r"^Simple mixer control '([^']+)',(\d+)$",listing.stdout,re.M):
                control=f'{name},{index}'
                info=run([exe('amixer'),'-c',card,'sget',control],False,8)
                if info.returncode:
                    errors.append(f'ALSA card {card} {control}: could not inspect simple control')
                    continue
                for direction in ('playback','capture'):
                    if not re.search(rf'^{direction} channels:',info.stdout,re.I|re.M):continue
                    readings=[float(db) for line in info.stdout.splitlines() if direction in line.lower()
                              for db in re.findall(r'\[([-+]?\d+(?:\.\d+)?)dB\]',line)]
                    if not readings:continue
                    result=run([exe('amixer'),'-c',card,'sset',control,direction,'0dB'],False,8)
                    if result.returncode:
                        errors.append(f'ALSA card {card} {control} {direction}: {result.stderr.strip() or "0dB unavailable"}')
                        continue
                    check=run([exe('amixer'),'-c',card,'sget',control],False,8)
                    verified=[float(db) for line in check.stdout.splitlines() if direction in line.lower()
                              for db in re.findall(r'\[([-+]?\d+(?:\.\d+)?)dB\]',line)] if not check.returncode else []
                    if len(verified)!=len(readings) or any(db!=0.0 for db in verified):
                        errors.append(f'ALSA card {card} {control} {direction}: 0dB not verified')
        raw=run([exe('amixer'),'-c',card,'contents'],False,8)
        if raw.returncode:
            errors.append(f'ALSA card {card}: {raw.stderr.strip() or "could not list raw controls"}')
            continue
        for numid,iface,name,count,minimum,maximum,block in _alsa_raw_gain_rows(raw.stdout):
            ident='numid='+numid
            # The simple mixer above already handles these same raw gains.
            current=re.search(r'^\s*: values=([-\d,]+)',block,re.M)
            if current is None:
                errors.append(f'ALSA card {card} {ident} ({iface} {name}): raw gain unreadable')
                continue
            values=[int(v) for v in current.group(1).split(',')]
            if len(values)!=count:
                errors.append(f'ALSA card {card} {ident}: raw channel count mismatch')
                continue
            if maximum==minimum:continue
            try:target=_alsa_zero_raw_value(card,numid)
            except (OSError,ValueError,RuntimeError) as error:
                errors.append(f'ALSA card {card} {ident} ({iface} {name}): {error}')
                continue
            if any(value!=target for value in values):
                change=run([exe('amixer'),'-c',card,'cset',ident,','.join([str(target)]*count)],False,8)
                if change.returncode:
                    errors.append(f'ALSA card {card} {ident}: {change.stderr.strip() or "raw gain write failed"}')
                    continue
            check=run([exe('amixer'),'-c',card,'cget',ident],False,8)
            found=re.search(r'^\s*: values=([-\d,]+)',check.stdout,re.M) if not check.returncode else None
            if not found or [int(v) for v in found.group(1).split(',')]!=[target]*count:
                errors.append(f'ALSA card {card} {ident}: raw 0dB not verified')
    # Never restore the master if any ALSA gain failed verification.
    if errors:raise RuntimeError('Audio normalization incomplete: '+'; '.join(errors[:8])+
                                 (f'; {len(errors)-8} more' if len(errors)>8 else ''))
    # The only PipeWire write in normalization is the logical master, last.
    # First startup has no persisted master yet. Use the gain subsystem's safe
    # restoring fallback and persist it before applying it to any live path.
    try:
        try:
            value=float(MASTER_VOLUME.read_text().strip().rstrip('%'))
            if not math.isfinite(value) or not 0<=value<=100:raise ValueError('saved master outside 0-100%')
        except (OSError,ValueError):
            value=master_volume()
            MASTER_VOLUME.parent.mkdir(parents=True,exist_ok=True)
            atomic(MASTER_VOLUME,f'{value:.2f}%\n')
        target=getattr(MEDIA_TRANSACTION,'stage_sink',None) or master_sink()
        if target=='@DEFAULT_SINK@':target=pw_default()
        inactive=saved_output_route().get('sink') if target=='camilladsp' else 'camilladsp'
        if inactive and inactive!=target:
            try:inactive_node=pw_sink(inactive)
            except RuntimeError:inactive_node=None
            if inactive_node is not None:
                unity=run([exe('wpctl'),'set-volume',str(inactive_node['id']),'100%'],False,8,media_env())
                if unity.returncode:raise RuntimeError(unity.stderr.strip() or 'Could not set inactive playback sink to unity')
        result=run([exe('wpctl'),'set-volume',str(pw_sink(target)['id']),f'{value:.2f}%'],False,8,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not restore saved master')
        sync_mpv_for_audio_path(value)
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as error:
        errors.append('saved master: '+str(error))
    if errors:raise RuntimeError('Audio normalization incomplete: '+'; '.join(errors[:8])+
                                 (f'; {len(errors)-8} more' if len(errors)>8 else ''))
    return {'masterVolume':value}
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
