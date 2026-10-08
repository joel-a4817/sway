def _physical_output_route(requested=None):
    """Compatibility entry point for the one authoritative physical resolver."""
    return choose_output_route(requested)
def apply_physical_no_filter_fallback(output=None):
    """Recover to one deterministic safe graph used by every caller."""
    requested=dict(output or {},_remembered=True)
    selected=_physical_output_route(requested)
    ACTIVE.write_text(NO_FILTER+'\n')
    invalidate_cache('profiles')
    # apply_mode performs the authoritative CamillaDSP shutdown, bypass
    # rebuild, physical monitor start and stream move.
    apply_mode('laptop_laptop',output=selected,output_resolved=True)
    direct_no_filter(selected)
    MODE.write_text('laptop_laptop\n')
    return selected

def audio_baseline_missing():
    route=saved_output_route()
    return (not ACTIVE.is_file() or not MODE.is_file()
            or not route.get('card') or not route.get('sink'))
def seed_master_from_physical_output(route=None):
    """Persist the real physical sink gain when no logical master exists."""
    saved=_saved_master_volume()
    if saved is not None:return route,saved
    ensure_pipewire_ready()
    candidate=route if isinstance(route,dict) and route.get('sink') else _physical_output_route(None)
    result=run(['__wp_control__','get-sink-volume',candidate['sink']],False,5,media_env())
    match=re.search(r'(\d+(?:\.\d+)?)%',result.stdout)
    if result.returncode or not match:
        raise RuntimeError(result.stderr.strip() or 'Could not read clean-state physical master volume')
    live=float(match.group(1))
    if not math.isfinite(live) or not 0<=live<=100:
        raise RuntimeError('Clean-state physical master volume is outside 0-100%')
    MASTER_VOLUME.parent.mkdir(parents=True,exist_ok=True)
    atomic(MASTER_VOLUME,f'{live:.2f}%\n')
    return candidate,live

def ensure_physical_audio_baseline(force=False):
    """Build a deterministic clean-state baseline from live physical audio."""
    with LOCK:
        if STOPPED.exists():return {'repaired':False,'stopped':True}
        if not force and not audio_baseline_missing():
            return {'repaired':False,'route':saved_output_route(),'mode':mode_state()}
        ensure_pipewire_ready()
        # Seed the same authoritative master used by server startup before the
        # transaction snapshot. State deletion must never require a PW default.
        candidate,_=seed_master_from_physical_output()
        with media_change():
            route=apply_physical_no_filter_fallback(candidate)
        if selected_filter()!=NO_FILTER or audio_mode()!='laptop_laptop':
            raise RuntimeError('Clean-state audio baseline did not persist No filter laptop mode')
        if saved_output_route().get('sink')!=route['sink'] or pw_default()!=route['sink']:
            raise RuntimeError('Clean-state audio baseline did not persist the physical default')
        return {'repaired':True,'route':route,'mode':mode_state()}
def mode_state():
    if STOPPED.exists():
        return {'mode':'stopped','label':'No source selected','source':'',
            'localWanted':False,'sonobusWanted':False,'camilla':False,
            'sonobus':False,'localMonitor':False,'systemAudio':False,'engineRunning':False,'engineLabel':'Audio stopped',
            'airplay':False,'localOutput':saved_output_route(),
            'localOutputLabel':'','sonobusPolicy':{}}
    name=audio_mode();label,source,local,sono=MODES[name];policy=MODE_POLICIES[name]
    local_pid=rpid(LOCALMONPID);filter_name=selected_filter()
    camilla_running=alive(rpid(CAMPID),'camilladsp');bypass_running=alive(rpid(BYPASSPID))
    route=_read_json(LOCALSINK,{})
    if not isinstance(route,dict):route={}
    selected={key:str(route.get(key) or '') for key in ('card','profile','sink','port')}
    airplay_state=airplay_health()
    return {
        'mode':name,'label':label,'source':source,'localWanted':local,'sonobusWanted':sono,
        'camilla':camilla_running,'engineRunning':(bypass_running if filter_name==NO_FILTER else camilla_running),'engineLabel':('No filter bridge' if filter_name==NO_FILTER else 'CamillaDSP'),'sonobus':bool(sonopids()),'localMonitor':alive(local_pid),
        'systemAudio':run(['systemctl','--user','is-active',SYSTEM_AUDIO_SERVICE],False,5).stdout.strip()=='active',
        'airplay':airplay_state['active'],'airplayServices':airplay_state['services'],
        'localOutput':selected,'localOutputLabel':(route.get('label') or 'Not selected'),'sonobusPolicy':policy,
    }
def apply_mode(name,password=None,restore_camilla=True,output=None,output_resolved=False):
    if name not in MODES:raise ValueError('Invalid audio mode')
    label,source,local,sono=MODES[name];policy=MODE_POLICIES[name]
    mpv_was_running=alive(rpid(MPVPID),'mpv')
    if STOPPED.exists():ensure_pipewire_ready()
    direct=(selected_filter()==NO_FILTER and source=='system' and not sono and local)
    if selected_filter()==NO_FILTER:
        # Every No-filter entry path uses the same authoritative shutdown.
        # This stops tracked and stale CamillaDSP processes and verifies that
        # no CamillaDSP process remains before the bypass bridge starts.
        stop_camilla_for_no_filter()
    elif restore_camilla and not alive(rpid(CAMPID),'camilladsp'):
        saved=selected_filter()
        if saved==NO_FILTER:raise RuntimeError('No filter cannot start CamillaDSP')
        start_camilla(profile(saved))
    apply_source_services(source,direct=direct)
    # Every shared Laptop-source route must feed camilladsp_input. In No
    # filter the bypass bridge replaces CamillaDSP, but the desktop ingress is
    # still the same camilladsp PipeWire sink.
    if source=='system' and not direct:
        restore_dsp_desktop_sink()
    if source=='system' and not direct:
        if run(['systemctl','--user','is-active','--quiet',SYSTEM_AUDIO_SERVICE],False,5).returncode:
            raise RuntimeError('CamillaDSP desktop sink service is not active')
    selected=None
    if local:
        selected=output if output_resolved else _physical_output_route(output)
        # Even pre-resolved callers are revalidated against the live physical set.
        selected=_physical_output_route(dict(selected,_remembered=True))
    if direct:
        # Laptop -> Laptop with No filter is genuinely direct PipeWire playback.
        # Do not open either Loopback endpoint: there is no CamillaDSP/SonoBus
        # producer-consumer path to bypass, and doing so created the arecord EIO.
        stop_local_monitor()
        stop_bypass()
        direct_no_filter(selected)
        # start_local_monitor normally persists the selected route. Direct
        # No-filter deliberately has no monitor, so persist the same complete
        # route here before clean-state verification or later startup reads it.
        _write_json(LOCALSINK,selected)
        remember_output(selected)
    else:
        # Shared Laptop routes still enter through camilladsp_input. When the
        # engine is bypassed, bridge input pair 0 to processed-output pair 1.
        if selected_filter()==NO_FILTER:
            stop_bypass()
            start_bypass()
        if local:start_local_monitor(selected,resolved=True)
        elif alive(rpid(LOCALMONPID)):stop_local_monitor()
    if sono:
        if not sonobus_matches(policy):restart_sonobus(password,policy,normalize=False)
    elif sonopids():stop_sonobus()
    MODE.write_text(name+'\n')
    STOPPED.unlink(missing_ok=True)
    if source=='system' and (mpv_was_running or QUEUE_FILE.is_file()):
        # AO selection is fixed at MPV startup. Recreate MPV only when the
        # active graph needs a different backend; queue restoration stays paused.
        try:ensure_mpv()
        except (OSError,RuntimeError):pass
    return mode_state()
def set_mode(name,password=None,output=None):
    with LOCK, (audio_start_change() if STOPPED.exists() else media_change()):
        if name not in MODES:raise ValueError('Invalid audio mode')
        was_stopped=STOPPED.exists()
        if was_stopped and MODES[name][2]:
            ensure_pipewire_ready()
        selected=_physical_output_route(output) if MODES[name][2] else None
        if was_stopped and alive(rpid(CAMPID),'camilladsp'):
            stop_local_monitor();stop_camilla(include_stale=True)
        return apply_mode(name,password,output=selected,output_resolved=selected is not None)
