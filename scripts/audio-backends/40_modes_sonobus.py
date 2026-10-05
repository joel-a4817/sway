def groups_state():
    default={'active':'default','profiles':{'default':{'group':'rt4817-camilladsp','username':'rt4817','server':'aoo.sonobus.net:10998','passwordRequired':False}}}
    data=_read_json(GROUPS,default); profiles=data.get('profiles') if isinstance(data,dict) else None
    if not isinstance(profiles,dict) or not profiles:return default
    active=data.get('active');return {'active':active if active in profiles else next(iter(profiles)),'profiles':profiles}
def save_group(data):
    key=re.sub(r'[^A-Za-z0-9_.-]+','-',str(data.get('key','')).strip()).strip('-')
    group=str(data.get('group','')).strip();user=str(data.get('username','')).strip();server=str(data.get('server','aoo.sonobus.net:10998')).strip()
    if not key or not group or not user or not server:raise ValueError('Profile, group, username and server are required')
    state=groups_state();state['profiles'][key]={'group':group,'username':user,'server':server,'passwordRequired':bool(data.get('passwordRequired'))};state['active']=key;_write_json(GROUPS,state);return state
def audio_mode():
    try:value=MODE.read_text().strip()
    except OSError:value='ipad_external'
    if value=='airplay':value='ipad_external'
    if value=='system':value='laptop_external'
    if value=='external_roundtrip':value='laptop_external'
    return value if value in MODES else 'ipad_external'
def _sonobus_audio_setup(text):
    """Return the decoded DEVICESETUP stored in VALUE[name=audioSetup].val."""
    import xml.etree.ElementTree as ET
    root=ET.fromstring(text)
    value=next((node for node in root.iter('VALUE') if node.get('name')=='audioSetup'),None)
    if value is None:return root,None,None
    raw=value.get('val') or ''
    device=None
    if raw.strip():
        try:
            parsed=ET.fromstring(raw)
            if parsed.tag=='DEVICESETUP':device=parsed
        except ET.ParseError:pass
    return root,value,device

def configure_sonobus(policy=None):
    if not SONOSET.is_file():raise FileNotFoundError(SONOSET)
    import xml.etree.ElementTree as ET
    policy=dict(policy or MODE_POLICIES[audio_mode()])
    text=SONOSET.read_text(encoding='utf-8')
    root,value,device=_sonobus_audio_setup(text)
    if value is None:
        value=ET.SubElement(root,'VALUE',{'name':'audioSetup','val':''})
    if device is None:
        device=ET.Element('DEVICESETUP')
    attributes={
        'deviceType':'ALSA',
        'audioOutputDeviceName':policy['outputDevice'],
        'audioInputDeviceName':policy['inputDevice'],
        'audioDeviceRate':'96000.0',
        'audioDeviceBufferSize':'512',
    }
    for key,wanted in attributes.items():device.set(key,str(wanted))
    value.set('val',ET.tostring(device,encoding='unicode',short_empty_elements=True))
    parameters={
        'sendchannels':'2.0','defsendqual':'5.0','mastinmute':'0.0',
        'mastsendmute':'1.0' if policy.get('sendMute') else '0.0',
        'mastrecvmute':'1.0' if policy.get('receiveMute',True) else '0.0',
        'dry':'0.0','wet':'0.9999999403953552',
    }
    found={}
    for node in root.iter('PARAM'):
        key=node.get('id')
        if key in parameters:
            node.set('value',parameters[key]);found[key]=found.get(key,0)+1
    missing=[key for key in parameters if not found.get(key)]
    if missing:raise RuntimeError('SonoBus settings are missing PARAM entry: '+', '.join(missing))
    body=ET.tostring(root,encoding='unicode',short_empty_elements=True)
    output='<?xml version="1.0" encoding="UTF-8"?>\n\n'+body+'\n'
    temporary=SONOSET.with_name(SONOSET.name+'.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        handle.write(output);handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,SONOSET)
    written=SONOSET.read_text(encoding='utf-8')
    _,written_value,written_device=_sonobus_audio_setup(written)
    if written_value is None or written_device is None:
        raise RuntimeError('SonoBus audioSetup DEVICESETUP did not persist')
    verification={}
    for key,wanted in attributes.items():
        if written_device.get(key)!=wanted:
            raise RuntimeError(f'SonoBus device setting {key} did not persist as {wanted}')
        verification[key]=wanted
    check_root=ET.fromstring(written)
    for key,wanted in parameters.items():
        matches=[node.get('value') for node in check_root.iter('PARAM') if node.get('id')==key]
        if not matches or any(item!=wanted for item in matches):
            raise RuntimeError(f'SonoBus setting {key} did not persist as {wanted}')
        verification[key]=wanted
    return {'policy':policy,'settings':verification}
def sonobus_matches(policy):
    if not sonopids() or not SONOSET.is_file():return False
    try:
        import xml.etree.ElementTree as ET
        text=SONOSET.read_text(encoding='utf-8')
        root,value,device=_sonobus_audio_setup(text)
        if value is None or device is None:return False
        wanted={
            'deviceType':'ALSA','audioInputDeviceName':policy['inputDevice'],
            'audioOutputDeviceName':policy['outputDevice'],
            'audioDeviceRate':'96000.0','audioDeviceBufferSize':'512',
        }
        if any(device.get(key)!=expected for key,expected in wanted.items()):return False
        params={node.get('id'):node.get('value') for node in root.iter('PARAM')}
        if params.get('mastsendmute')!=('1.0' if policy.get('sendMute') else '0.0'):return False
        if params.get('mastrecvmute')!=('1.0' if policy.get('receiveMute',True) else '0.0'):return False
        state=groups_state();group=state['profiles'][state['active']]
        expected=['--group='+group['group'],'--username='+group['username'],'--connectionserver='+group['server']]
        for pid in sonopids():
            args=[x.decode('utf-8','replace') for x in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\\0') if x]
            if all(x in args for x in expected):return True
    except (OSError,KeyError,TypeError,ValueError,ET.ParseError):pass
    return False
def restart_sonobus(password=None,policy=None,normalize=False):
    global SONOBUS_PROCESS
    with LOCK:
        stop_sonobus();applied=configure_sonobus(policy)
        state=groups_state();item=state['profiles'][state['active']]
        # Keep the regular SonoBus application running detached from the web
        # request. Do not add -q/--headless: this build only behaves correctly
        # when the normal application process is running.
        command=[str(SONOBUS),'--group='+item['group'],'--username='+item['username'],'--connectionserver='+item['server']]
        if any(arg in ('-q','--headless') for arg in command):
            raise RuntimeError('Headless SonoBus startup is disabled')
        if item.get('passwordRequired'):
            if not password:raise ValueError('The selected SonoBus group requires a password')
            command.append('--group-password='+str(password))
        logpath=STATE/'sonobus.log'
        with logpath.open('ab',buffering=0) as log:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=media_env())
        SONOBUS_PROCESS=process
        deadline=time.monotonic()+3.0
        while time.monotonic()<deadline:
            code=process.poll()
            if code is not None:
                SONOBUS_PROCESS=None
                tail=log_tail(logpath)[-2500:]
                raise RuntimeError('SonoBus failed to start: '+tail)
            if process.pid in sonopids():break
            time.sleep(.05)
        if normalize and not STOPPED.exists():normalize_with_media()
        return {'pid':process.pid,'profile':state['active'],'group':item['group'],**applied}

@media_transaction
def restart_sonobus_action(password=None):
    if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before restarting SonoBus')
    return restart_sonobus(password,MODE_POLICIES[audio_mode()],normalize=False)

