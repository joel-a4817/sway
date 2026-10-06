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
    if local:
        selected=output if output_resolved else _physical_output_route(output)
        # Even pre-resolved callers are revalidated against the live physical set.
        selected=_physical_output_route(dict(selected,_remembered=True))
        if direct:direct_no_filter(selected)
        # Rebuild the bypass only after the final physical route is stable.
        if selected_filter()==NO_FILTER:
            stop_bypass()
            start_bypass()
        start_local_monitor(selected,resolved=True)
        # Service, profile and monitor creation can update PipeWire metadata.
        # In direct No-filter mode the final committed default must still be
        # the selected physical sink, never the CamillaDSP virtual sink.
        if direct:direct_no_filter(selected)
    elif alive(rpid(LOCALMONPID)):stop_local_monitor()
    if sono:
        if not sonobus_matches(policy):restart_sonobus(password,policy,normalize=False)
    elif sonopids():stop_sonobus()
    MODE.write_text(name+'\n')
    STOPPED.unlink(missing_ok=True)
    if source=='system' and not direct and QUEUE_FILE.is_file() and not alive(rpid(MPVPID),'mpv'):
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
