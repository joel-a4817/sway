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
