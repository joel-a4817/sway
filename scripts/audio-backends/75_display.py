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
