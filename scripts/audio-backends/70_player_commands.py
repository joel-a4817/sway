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


def shuffle_current_playlist():
    with PLAYERLOCK:
        entries=mpv_direct(['get_property','playlist']) or []
        position=mpv_direct(['get_property','playlist-pos'])
        current_path=mpv_direct(['get_property','path'])
        if not isinstance(position,int) or not 0<=position<len(entries) or not current_path:
            raise ValueError('No active MPV queue to shuffle')
        remaining=len(entries)-position-1
        if remaining<2:
            raise ValueError('At least two remaining tracks are needed to shuffle')
        # Drop only entries that have already played. The current entry remains
        # loaded while MPV shuffles the current+remaining queue.
        for _ in range(position):
            mpv(['playlist-remove',0])
        mpv(['playlist-shuffle'])
        shuffled=mpv_direct(['get_property','playlist']) or []
        current_index=next((index for index,row in enumerate(shuffled)
                            if isinstance(row,dict) and row.get('current')),None)
        if current_index is None:
            current_index=next((index for index,row in enumerate(shuffled)
                                if isinstance(row,dict) and row.get('filename')==current_path),None)
        if current_index is None:
            raise RuntimeError('MPV lost the current track while shuffling')
        # playlist-shuffle preserves playback but may move the current entry to
        # another queue index. Move it to the front so every remaining track is
        # still scheduled after it.
        if current_index:
            mpv(['playlist-move',current_index,0])
        active_path=mpv_direct(['get_property','path'])
        final_position=mpv_direct(['get_property','playlist-pos'])
        if active_path!=current_path or final_position!=0:
            raise RuntimeError('MPV did not preserve the current track while shuffling')
        save_mpv_queue(force=True)
        return {'shuffled':True,'remaining':remaining}
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
    playlist=MUSIC/f'{name}.m3u'
    if playlist.is_file():
        paths=[]
        for line in playlist.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            target=Path(line).expanduser()
            if not target.is_absolute():target=playlist.parent/target
            paths.append(str(song(target)))
        if not paths:raise ValueError('Playlist is empty')
    else:
        directory=pdir(name)
        if not directory.is_dir():raise FileNotFoundError('Playlist not found: '+name)
        tracks=files(directory)
        if not tracks:raise ValueError('Playlist is empty')
        paths=[str(track.resolve()) for track in tracks]
        atomic(playlist,'#EXTM3U\n'+'\n'.join(os.path.relpath(path,MUSIC) for path in paths)+'\n')
    with PLAYERLOCK:
        QUEUE_SOURCE.unlink(missing_ok=True)
        if shuffle and len(paths)>1:
            # MPV's shuffle option randomizes playlist entries as loadlist
            # expands them, before the first entry is selected for playback.
            mpv(['set_property','shuffle',True])
        try:
            mpv(['loadlist',str(playlist),'replace'])
        finally:
            if shuffle and len(paths)>1:
                mpv(['set_property','shuffle',False])
        mpv(['set_property','playlist-pos',0])
        mpv(['set_property','pause',False])
        live=mpv_direct(['get_property','playlist']) or []
        live_paths=[str(song(row.get('filename'))) for row in live if isinstance(row,dict)]
        if len(live_paths)!=len(paths) or set(live_paths)!=set(paths):
            raise RuntimeError('MPV did not load the complete playlist')
        _write_json(QUEUE_SOURCE,{'playlist':name,'files':live_paths})
        save_mpv_queue(force=True)
    deadline=time.monotonic()+5
    last={}
    while time.monotonic()<deadline:
        last=player_state()
        if last.get('available'):
            return {'playlist':name,'shuffled':bool(shuffle and len(paths)>1),'state':last}
        time.sleep(.05)
    return {'playlist':name,'shuffled':bool(shuffle and len(paths)>1),'state':last,'pending':True}
DISPLAY_FILE=STATE/'display-output.json'
DISPLAY_LOCK=threading.RLock()
