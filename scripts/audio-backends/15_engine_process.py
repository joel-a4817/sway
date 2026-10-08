def bridge_endpoint_pids():
    """Find this user's exact no-filter capture and playback endpoints."""
    endpoints={("arecord","hw:Loopback,1,0"),("aplay","hw:Loopback,0,1")}
    found=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:
            if entry.stat().st_uid!=os.getuid():continue
            args=(entry/'cmdline').read_bytes().split(b'\0')
            if not args:continue
            program=Path(os.fsdecode(args[0])).name
            if b'-D' not in args:continue
            device=os.fsdecode(args[args.index(b'-D')+1])
            if (program,device) in endpoints:found.append(int(entry.name))
        except (OSError,ValueError,IndexError):continue
    return found


def stop_bypass():
    pid=rpid(BYPASSPID)
    if alive(pid):
        try:
            args=Path(f'/proc/{pid}/cmdline').read_bytes()
            if b'camilladsp_input' not in args or b'camilladsp_output_shared' in args:
                raise RuntimeError('No-filter PID belongs to another process')
            os.killpg(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    deadline=time.monotonic()+3
    while bridge_endpoint_pids() and time.monotonic()<deadline:time.sleep(.05)
    # State files can be deleted while the shell and one pipeline child survive.
    # Terminate both exact endpoints, not just aplay, before starting CamillaDSP.
    survivors=bridge_endpoint_pids()
    for child in survivors:
        try:os.kill(child,signal.SIGTERM)
        except ProcessLookupError:pass
    deadline=time.monotonic()+2
    while bridge_endpoint_pids() and time.monotonic()<deadline:time.sleep(.05)
    survivors=bridge_endpoint_pids()
    if survivors:
        for child in survivors:
            try:os.kill(child,signal.SIGKILL)
            except ProcessLookupError:pass
        deadline=time.monotonic()+1
        while bridge_endpoint_pids() and time.monotonic()<deadline:time.sleep(.05)
    if bridge_endpoint_pids():
        raise RuntimeError('No-filter bridge still owns CamillaDSP ALSA capture/playback endpoints')
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
    # ALSA can release the Loopback PCM shortly after the previous process
    # disappears. Retry only that transient startup failure; all other bridge
    # errors remain fatal and visible.
    command='set -o pipefail; "$1" -q -D hw:Loopback,1,0 -r 96000 -f S32_LE -c 2 -t raw | "$2" -q -D hw:Loopback,0,1 -r 96000 -f S32_LE -c 2 -t raw'
    log_path=STATE/'no-filter-bridge.log'
    transient=('Device or resource busy','Input/output error')
    for attempt in range(3):
        stop_bypass()
        if attempt:time.sleep(.2*attempt)
        with log_path.open('wb',buffering=0) as log:
            proc=subprocess.Popen([exe('bash'),'-c',command,'camilladsp_input',exe('arecord'),exe('aplay')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            if proc.poll() is not None:break
            time.sleep(.05)
        if proc.poll() is None:
            BYPASSPID.write_text(str(proc.pid)+'\n')
            return
        detail=log_tail(log_path)[-1500:]
        stop_bypass()
        if attempt<2 and any(message in detail for message in transient):continue
        raise RuntimeError('No-filter bridge exited: '+detail)

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
def stop_camilla_for_no_filter():
    """Stop the sole owned engine even when all tracking state was deleted."""
    stop_camilla(include_stale=True)
    remaining=other_camilla_processes()
    if not remaining:return
    if len(remaining)==1 and owned_camilla_engine(remaining[0]):
        # Both server startup and Media Control reach this shared path. Adopt
        # the verified survivor only long enough for the normal tracked stop.
        CAMPID.write_text(str(remaining[0])+'\n')
        stop_camilla(include_stale=True)
        remaining=other_camilla_processes()
    if remaining:
        raise RuntimeError('CamillaDSP still running while applying No filter (PID '+
                           ', '.join(map(str,remaining))+')')

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

def owned_camilla_engine(pid):
    """Recognize the one local engine without relying on deleted state files."""
    try:
        entry=Path(f'/proc/{pid}')
        if entry.stat().st_uid!=os.getuid():return False
        args=[part.decode('utf-8') for part in (entry/'cmdline').read_bytes().split(b'\0') if part]
        if not args or Path(args[0]).resolve()!=CAMILLA.resolve():return False
        if f'--port={CAM_WS_PORT}' not in args:return False
        with socket.create_connection(('127.0.0.1',CAM_WS_PORT),timeout=.5):pass
        return True
    except (OSError,ValueError,RuntimeError,socket.timeout):return False

def reusable_camilla(pid):
    """Recognize this engine by executable, websocket and active profile."""
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
    logpath=STATE/'camilladsp.log'
    transient=('Device or resource busy','Input/output error')
    for attempt in range(3):
        # Every filter transition must release the complete bypass pipeline,
        # including stale children whose PID state was deleted.
        stop_bypass()
        if attempt:time.sleep(.25*attempt)
        competing=other_camilla_processes()
        if competing:
            raise RuntimeError('CamillaDSP already running (PID '+', '.join(map(str,competing))+'); refusing a second instance. Check the local switch or another service.')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',CAM_WS_PORT))==0:
                raise RuntimeError('CamillaDSP websocket port 8767 is already occupied; refusing to start another instance')
        marker='\n===== start '+time.strftime('%Y-%m-%d %H:%M:%S')+' profile='+target.name+' attempt='+str(attempt+1)+' =====\n'
        with logpath.open('ab',buffering=0) as log:
            log.write(marker.encode())
            process=subprocess.Popen(
                [str(CAMILLA),f'--port={CAM_WS_PORT}','--gain=0.0',str(target)],stdin=subprocess.DEVNULL,
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
            )
        CAMILLA_PROCESS=process;CAMPID.write_text(str(process.pid)+'\n')
        deadline=time.monotonic()+2.0
        while time.monotonic()<deadline:
            code=process.poll()
            if code is not None:break
            time.sleep(.05)
        if process.poll() is None:
            ACTIVE.write_text(target.name+'\n')
            return process.pid
        code=process.returncode
        CAMILLA_PROCESS=None;CAMPID.unlink(missing_ok=True)
        content=log_tail(logpath)
        tail=content[content.rfind('===== start '):][-4000:].strip()
        if attempt<2 and any(message in tail for message in transient):
            stop_bypass()
            continue
        raise RuntimeError(
            f'CamillaDSP exited with status {code} while starting {target.name}'+
            (f': {tail}' if tail else '')
        )

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
def media_change():
    if getattr(MEDIA_TRANSACTION,'active',False):
        yield
        return
    global MASTER_RESTORING
    pause_for_normalization()
    saved_master=save_master_after_pause()
    previous_restoring=MASTER_RESTORING
    MASTER_RESTORING=True
    MEDIA_TRANSACTION.active=True
    try:
        yield
        if not STOPPED.exists():normalize_audio_volumes(saved_master)
    finally:
        try:pause_for_normalization()
        finally:
            MEDIA_TRANSACTION.stage_sink=None
            MEDIA_TRANSACTION.active=False
            MASTER_RESTORING=previous_restoring

def media_transaction(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        with media_change():return function(*args,**kwargs)
    return wrapped

@media_transaction
def restart_vnc():
    user_service('restart','camilladsp-wayvnc.service')
    return {'restarted':True}

@media_transaction
def restart_airplay():
    if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before restarting AirPlay')
    airplay(True)
    return {'restarted':True}

def reconcile_sonobus_after_engine_transition(password=None):
    """Reopen SonoBus after CamillaDSP and bypass exchange the producer."""
    mode=audio_mode()
    if mode not in MODES:return
    policy=MODE_POLICIES[mode]
    if not MODES[mode][3]:
        if sonopids():stop_sonobus()
        return
    state=groups_state();group=state['profiles'][state['active']]
    if group.get('passwordRequired') and not password:
        # The current authenticated process can keep its group connection. Its
        # ALSA capture endpoint is stable across the producer transition.
        if not sonopids():
            raise RuntimeError('SonoBus group requires its password after the filter transition')
        configure_sonobus(policy)
        return
    restart_sonobus(password,policy,normalize=False)

@media_transaction
def switch_profile(name):
    with LOCK:
        if name==NO_FILTER:
            stop_local_monitor()
            stop_bypass()
            stop_camilla_for_no_filter()
            ACTIVE.write_text(NO_FILTER+'\n')
            invalidate_cache('profiles')
            if STOPPED.exists():
                return {'profile':NO_FILTER,'pid':None,'mode':mode_state()}
            mode=audio_mode()
            local=MODES[mode][2]
            route=choose_output_route(dict(saved_output_route(),_remembered=True)) if local else None
            try:
                # Use the same complete route application as audio start and
                # output commit. This owns source services, bypass, monitor,
                # physical default, stream movement and final verification.
                result=apply_mode(mode,restore_camilla=False,output=route,
                                  output_resolved=route is not None)
                reconcile_sonobus_after_engine_transition()
                if mode=='laptop_laptop':direct_no_filter(route)
                ACTIVE.write_text(NO_FILTER+'\n')
            except Exception:
                stop_local_monitor()
                stop_bypass()
                raise
            return {'profile':NO_FILTER,'pid':None,'mode':result}
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
            reconcile_sonobus_after_engine_transition()
            ACTIVE.write_text(target.name+'\n')
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
        return {'profile':target.name,'pid':pid,'mode':mode_state()}
@media_transaction
def restart_camilla():
    with LOCK:
        if selected_filter()==NO_FILTER:
            return {'profile':NO_FILTER,'pid':None,'restarted':False}
        if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before restarting CamillaDSP')
        name=active()
        if not name:raise RuntimeError('No active profile')
        target=profile(name)
        stop_local_monitor()
        stop_camilla(include_stale=True)
        pid=start_camilla(target)
        if MODES[audio_mode()][1]=='system':restore_dsp_desktop_sink()
        if MODES[audio_mode()][2]:restore_selected_local_output()
        return {'profile':name,'pid':pid,'restarted':True}

# This is the single saved master shared by the server and Media Control.
