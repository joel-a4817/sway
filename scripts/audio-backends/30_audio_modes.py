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
    if isinstance(text,bytes):text=text.decode('utf-8')
    if not isinstance(text,str):raise TypeError(f'atomic text must be str, not {type(text).__name__}')
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        temporary.write_text(text,encoding='utf-8')
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)

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
    atomic(path,yaml.safe_dump(data,sort_keys=False,allow_unicode=True,encoding=None))
    return {'profile':selected,'chunksize':chunksize,'peakLimiter':enabled}

def configure_both_output_filterless():
    path=PROFILES/BOTH_FILTER
    if not path.is_file():raise RuntimeError(f'Missing both-output profile: {path}')
    data=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    filters=data.setdefault('filters',{});pipeline=data.setdefault('pipeline',[])
    filters.pop('media_control_sync_delay',None)
    pipeline[:]=[step for step in pipeline if not (isinstance(step,dict) and 'media_control_sync_delay' in step.get('names',[]))]
    matrix=both_output_matrix()
    data['title']='Filterless / split mono both-output'
    data['description']='One mono source channel to SonoBus and the opposite mono source channel to PC; branch delay is applied only in the PC monitor'
    atomic(path,yaml.safe_dump(data,sort_keys=False,allow_unicode=True,encoding=None))
    return {'path':str(path),'milliseconds':both_output_local_delay_ms(),'externalChannel':matrix['left'],'pcChannel':matrix['right']}

def verify_both_output_sync():
    mode=audio_mode()
    if mode not in ('ipad_both','laptop_both'):return {'active':False,'reason':'not-both-output'}
    offset=both_output_local_delay_ms();policy=effective_mode_policy(mode);matrix=both_output_matrix()
    expected_device=('CamillaDSP SonoBus External Delayed' if offset<0 else 'CamillaDSP SonoBus '+('Left' if matrix['left']=='external' else 'Right'))
    if policy.get('sendChannels')!=1 or policy.get('inputChannelsMask')!='1' or policy.get('inputDevice')!=expected_device:
        raise RuntimeError('Both-output SonoBus policy is not isolated to exactly one mono channel')
    return {'active':True,'mode':mode,'externalMono':matrix['left'],'pcMono':matrix['right'],'externalDevice':expected_device,'sendChannels':1,'pcDelayMilliseconds':offset}

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
    if name not in ('ipad_both','laptop_both'):return policy
    matrix=both_output_matrix();external_is_left=matrix['left']=='external'
    policy['sendChannels']=1
    policy['inputDevice']=('CamillaDSP SonoBus External Delayed' if both_output_local_delay_ms()<0 else 'CamillaDSP SonoBus '+('Left' if external_is_left else 'Right'))
    policy['inputChannelsMask']='1'
    policy['externalChannel']='left' if external_is_left else 'right'
    policy['pcChannel']='right' if external_is_left else 'left'
    policy['description']=policy['description']+'; exactly one mono channel to SonoBus and the opposite mono channel to PC output'
    return policy

def _read_json(path,default):
    try:return json.loads(path.read_text())
    except (OSError,ValueError,TypeError):return default
def _write_json(path,value):atomic(path,json.dumps(value,indent=2,ensure_ascii=False)+'\n')
