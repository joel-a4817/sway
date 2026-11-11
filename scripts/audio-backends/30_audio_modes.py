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
    policy['sendChannels']=1
    if both_output_local_delay_ms()<0:
        policy['inputDevice']='CamillaDSP SonoBus Delayed'
    policy['inputChannelsMask']='1' if external_channel=='left' else '10'
    policy['externalChannel']=external_channel
    policy['pcChannel']='right' if external_channel=='left' else 'left'
    policy['description']=policy['description']+'; '+external_channel+' channel to SonoBus, '+policy['pcChannel']+' channel to PC output'
    return policy

def _read_json(path,default):
    try:return json.loads(path.read_text())
    except (OSError,ValueError,TypeError):return default
def _write_json(path,value):atomic(path,json.dumps(value,indent=2,ensure_ascii=False)+'\n')
