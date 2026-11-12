def sonopids():
    out=set()
    r=run(['pgrep','-x','(sonobus|SonoBus)'],False)
    for x in r.stdout.split():
        try:out.add(int(x))
        except ValueError:pass
    return out
def stop_sonobus():
    global SONOBUS_PROCESS
    targets=set(sonopids())
    if SONOBUS_PROCESS is not None and SONOBUS_PROCESS.poll() is None:
        targets.add(SONOBUS_PROCESS.pid)
    for pid in targets:
        try:os.killpg(pid,signal.SIGTERM)
        except OSError:
            try:os.kill(pid,signal.SIGTERM)
            except OSError:pass
    deadline=time.monotonic()+4.0
    while any(alive(pid) for pid in targets) and time.monotonic()<deadline:
        time.sleep(.05)
    for pid in targets:
        if alive(pid):
            try:os.killpg(pid,signal.SIGKILL)
            except OSError:
                try:os.kill(pid,signal.SIGKILL)
                except OSError:pass
    deadline=time.monotonic()+1.0
    while any(alive(pid) for pid in targets) and time.monotonic()<deadline:
        time.sleep(.05)
    SONOBUS_PROCESS=None


def atomic(path,text):
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    temporary.write_text(text)
    os.replace(temporary,path)

GROUPS=STATE/'sonobus-groups.json'; LOCALMONPID=STATE/'local-monitor.pid'; EXTERNALDELAYPID=STATE/'external-delay.pid'; LOCALSINK=STATE/'local-output-route.json'; OUTPUT_MATRIX=STATE/'both-output-matrix'; BOTH_OUTPUT_DELAY=STATE/'both-output-sync-offset-ms'; LEGACY_BOTH_OUTPUT_DELAY=STATE/'both-output-local-delay-ms'
MODES={
'ipad_external':('iPad → external','airplay',False,True),
'laptop_external':('Laptop → external','system',False,True),
'ipad_ipad':('iPad only','airplay',False,True),
'ipad_laptop':('iPad → laptop','airplay',True,False),
'ipad_both':('iPad → iPad + laptop','airplay',True,True),
'laptop_ipad':('Laptop → iPad','system',False,True),
'laptop_laptop':('Laptop only','system',True,False),
'laptop_both':('Laptop → iPad + laptop','system',True,True),
}
MODE_POLICIES={
'ipad_external': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'AirPlay through CamillaDSP to external SonoBus receivers',
},
'laptop_external': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to external SonoBus receivers',
},
'ipad_ipad': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'iPad AirPlay through CamillaDSP and SonoBus back to the iPad only',
},
'ipad_laptop': {
    'sendMute':True,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'AirPlay through CamillaDSP to laptop output only',
},
'ipad_both': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'AirPlay through CamillaDSP to iPad and the highest-priority physical laptop output',
},
'laptop_ipad': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to iPad',
},
'laptop_laptop': {
    'sendMute':True,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to laptop output only',
},
'laptop_both': {
    'sendMute':False,'receiveMute':True,
    'inputDevice':'CamillaDSP SonoBus','outputDevice':'SonoBus Silent Output',
    'description':'Laptop audio through CamillaDSP to iPad and the highest-priority physical laptop output',
},
}


def both_output_local_delay_ms():
    try:value=float((BOTH_OUTPUT_DELAY if BOTH_OUTPUT_DELAY.exists() else LEGACY_BOTH_OUTPUT_DELAY).read_text().strip())
    except (OSError,ValueError):value=0.0
    return max(-500.0,min(500.0,value))
def set_both_output_local_delay_ms(value):
    value=float(value)
    if not -500.0<=value<=500.0:raise ValueError('Both-output sync offset must be between -500 and 500 ms')
    atomic(BOTH_OUTPUT_DELAY,('{:.3f}'.format(value).rstrip('0').rstrip('.') or '0')+'\n')
    return {'milliseconds':value}

def active_profile_runtime_status():
    mode=audio_mode();selected=selected_filter()
    if mode in ('ipad_both','laptop_both'):
        return {'available':False,'reason':'both-output','profile':BOTH_FILTER}
    if selected==NO_FILTER:
        return {'available':False,'reason':'no-filter','profile':selected}
    path=profile(selected);data=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    devices=data.get('devices') or {};playback=devices.get('playback') or {}
    return {'available':True,'profile':selected,'chunksize':int(devices.get('chunksize',0) or 0),
            'peakLimiter':playback.get('device')=='camilladsp_peak_limited'}

def configure_active_profile_runtime(chunksize,peak_limiter):
    mode=audio_mode()
    if mode in ('ipad_both','laptop_both'):
        raise RuntimeError('Chunksize and peak limiter are unavailable while a both-output mode is active')
    selected=selected_filter()
    if selected==NO_FILTER:
        raise RuntimeError('Chunksize and peak limiter require an active CamillaDSP filter')
    chunksize=int(chunksize)
    if chunksize<1:raise ValueError('Chunksize must be a positive whole number')
    enabled=bool(peak_limiter)
    path=profile(selected);data=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    devices=data.setdefault('devices',{});devices['chunksize']=chunksize
    playback=devices.setdefault('playback',{})
    playback['device']='camilladsp_peak_limited' if enabled else 'hw:Loopback,0,1'
    atomic(path,yaml.safe_dump(data,sort_keys=False,allow_unicode=True))
    validate_camilla_profile(path)
    return {'profile':selected,'chunksize':chunksize,'peakLimiter':enabled}

def configure_both_output_filterless():
    path=PROFILES/BOTH_FILTER
    if not path.is_file():raise RuntimeError(f'Missing both-output profile: {path}')
    data=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(data,dict):raise RuntimeError('00-filterless.yml is not a YAML mapping')
    offset=both_output_local_delay_ms();matrix=both_output_matrix()
    delayed='pc' if offset>0 else ('external' if offset<0 else None)
    channel=0 if matrix['left']==delayed else 1
    filters=data.setdefault('filters',{})
    pipeline=data.setdefault('pipeline',[])
    filters.pop('media_control_sync_delay',None)
    pipeline[:]=[step for step in pipeline if not (isinstance(step,dict) and step.get('type')=='Filter' and 'media_control_sync_delay' in step.get('names',[]))]
    if delayed:
        filters['media_control_sync_delay']={'type':'Delay','parameters':{'delay':abs(float(offset)),'unit':'ms','subsample':False}}
        pipeline.append({'type':'Filter','channels':[channel],'names':['media_control_sync_delay']})
    data['title']='Filterless / Both-output sync'
    data['description']=f'Pass-through both-output profile; signed offset {offset} ms; delayed destination {delayed or "none"}'
    atomic(path,yaml.safe_dump(data,sort_keys=False,allow_unicode=True))
    validate_camilla_profile(path)
    return {'path':str(path),'milliseconds':offset,'delayed':delayed,'channel':channel if delayed else None}

def verify_both_output_sync():
    mode=audio_mode()
    if mode not in ('ipad_both','laptop_both'):
        return {'active':False,'reason':'not-both-output'}
    path=PROFILES/BOTH_FILTER
    data=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    offset=both_output_local_delay_ms();matrix=both_output_matrix();policy=effective_mode_policy(mode)
    delayed='pc' if offset>0 else ('external' if offset<0 else None)
    expected_channel=(0 if matrix['left']==delayed else 1) if delayed else None
    definition=(data.get('filters') or {}).get('media_control_sync_delay')
    stages=[step for step in (data.get('pipeline') or []) if isinstance(step,dict) and 'media_control_sync_delay' in step.get('names',[])]
    if delayed:
        if not isinstance(definition,dict):raise RuntimeError('Both-output delay filter is missing from 00-filterless.yml')
        parameters=definition.get('parameters') or {}
        if definition.get('type')!='Delay' or float(parameters.get('delay',-1))!=abs(float(offset)) or parameters.get('unit')!='ms':
            raise RuntimeError('Both-output delay filter parameters do not match the saved offset')
        if len(stages)!=1 or stages[0].get('channels')!=[expected_channel]:
            raise RuntimeError('Both-output delay pipeline channel does not match the output matrix')
    elif definition is not None or stages:
        raise RuntimeError('Zero offset still has a delay stage in 00-filterless.yml')
    external_channel='left' if matrix['left']=='external' else 'right'
    expected_mask='1'
    expected_device='CamillaDSP SonoBus Left' if external_channel=='left' else 'CamillaDSP SonoBus Right'
    if policy.get('sendChannels')!=1 or policy.get('inputChannelsMask')!=expected_mask or policy.get('inputDevice')!=expected_device:
        raise RuntimeError('SonoBus policy does not match the processed external channel')
    validate_camilla_profile(path)
    return {'active':True,'milliseconds':offset,'delayed':delayed,'delayChannel':expected_channel,
            'externalChannel':external_channel,'sendChannels':1,'inputChannelsMask':expected_mask,
            'inputDevice':policy['inputDevice'],'profile':str(path)}

def both_output_matrix():
    default={'left':'external','right':'pc'}
    try:data=json.loads(OUTPUT_MATRIX.read_text())
    except (OSError,ValueError,TypeError):return default
    if not isinstance(data,dict):return default
    left=data.get('left');right=data.get('right')
    if {left,right}!={'external','pc'}:return default
    return {'left':left,'right':right}

def set_both_output_matrix(left,right):
    if {left,right}!={'external','pc'}:
        raise ValueError('One channel must use external output and the other PC output')
    value={'left':left,'right':right}
    _write_json(OUTPUT_MATRIX,value)
    return value

def effective_mode_policy(name):
    policy=dict(MODE_POLICIES[name])
    if name not in ('ipad_both','laptop_both'):
        policy['sendChannels']=2
        policy['inputChannelsMask']='11'
        return policy
    matrix=both_output_matrix()
    external_channel='left' if matrix['left']=='external' else 'right'
    # The ALSA endpoint itself exposes exactly one channel.  Do not rely on
    # SonoBus/JUCE's input mask to split a stereo capture device.
    policy['sendChannels']=1
    policy['inputDevice']='CamillaDSP SonoBus Left' if external_channel=='left' else 'CamillaDSP SonoBus Right'
    policy['inputChannelsMask']='1'
    policy['externalChannel']=external_channel
    policy['pcChannel']='right' if external_channel=='left' else 'left'
    policy['description']=policy['description']+'; '+external_channel+' channel to SonoBus, '+policy['pcChannel']+' channel to PC output'
    return policy

def _read_json(path,default):
    try:return json.loads(path.read_text())
    except (OSError,ValueError,TypeError):return default
def _write_json(path,value):atomic(path,json.dumps(value,indent=2,ensure_ascii=False)+'\n')
