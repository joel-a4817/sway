def _physical_output_route(requested=None):
    """Resolve a real playback destination; never auto-select software I/O."""
    topology=audio_topology()
    physical=[]
    blocked=('[N/A] ','[INT] ','[MON] ','[LOOP] ','[VIRT] ')
    def usable(row):
        label=str(row.get('label') or '')
        name=str(row.get('name') or '')
        return (not row.get('internal') and not label.startswith(blocked)
                and name not in ('camilladsp','')
                and 'loopback' not in label.casefold()
                and 'loopback' not in name.casefold())
    for card in topology.get('cards',[]):
        if not usable(card):continue
        for sink in card.get('sinks',[]):
            if usable(sink):physical.append((card,sink))
    if not physical:
        raise RuntimeError('No physical playback output is available')
    if isinstance(requested,dict) and requested:
        selected=choose_output_route(requested)
        match=next(((card,sink) for card,sink in physical
                    if card.get('name')==selected.get('card')
                    and sink.get('name')==selected.get('sink')),None)
        if match is None:
            raise RuntimeError('Selected output is internal; select a physical playback output')
        return selected
    saved=topology.get('saved') or {}
    if saved:
        try:
            selected=choose_output_route(dict(saved,_remembered=True))
            if any(card.get('name')==selected.get('card') and sink.get('name')==selected.get('sink')
                   for card,sink in physical):
                return selected
        except (OSError,RuntimeError,ValueError):
            pass
    # Dynamic fallback: first available non-internal card/sink in topology order.
    card,sink=physical[0]
    return choose_output_route({'card':card['name'],'sink':sink['name']})

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
        selected=_physical_output_route(output if output_resolved else output)
        if direct:direct_no_filter(selected)
        # Profile, route and default-sink changes may invalidate ALSA Loopback.
        # Rebuild the bypass only after the final physical route is stable.
        if selected_filter()==NO_FILTER:
            stop_bypass()
            start_bypass()
        start_local_monitor(selected,resolved=True)
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
