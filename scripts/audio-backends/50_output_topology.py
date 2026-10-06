def stop_local_monitor():
    pid=rpid(LOCALMONPID)
    if alive(pid):
        try:os.killpg(pid,signal.SIGTERM)
        except OSError:pass
        deadline=time.monotonic()+2
        while alive(pid) and time.monotonic()<deadline:time.sleep(.05)
        if alive(pid):
            try:os.killpg(pid,signal.SIGKILL)
            except OSError:pass
    LOCALMONPID.unlink(missing_ok=True)
def _pipewire_json(kind):
    graph=pw_graph();nodes=pw_objects('Node',graph);devices={str(x['id']):x for x in pw_objects('Device',graph)}
    if kind=='cards':
        return [{'index':x['id'],'name':pw_props(x).get('device.name',''),
                 'properties':pw_props(x),'profiles':[{'name':'current','description':'Current live profile','sinks':1,'available':'yes'}],
                 'active_profile':{'name':'current'}} for x in devices.values() if pw_props(x).get('device.name','').startswith('alsa_card.')]
    if kind in ('sinks','sources'):
        klass='Audio/Sink' if kind=='sinks' else 'Audio/Source'
        return [{'index':x['id'],'name':pw_props(x).get('node.name',''),
                 'description':pw_props(x).get('node.description') or pw_props(x).get('node.nick') or pw_props(x).get('node.name',''),
                 'card':pw_props(x).get('device.id'),'properties':pw_props(x),
                 'ports':[{'name':'','description':'Current live route','available':'yes'}],
                 'active_port':{'name':''}} for x in nodes if pw_props(x).get('media.class')==klass]
    klass='Stream/Output/Audio' if kind=='sink-inputs' else 'Stream/Input/Audio'
    return [{'index':x['id'],'properties':pw_props(x),'sink':None} for x in nodes if pw_props(x).get('media.class')==klass]

def saved_output_route():
    data=_read_json(LOCALSINK,{})
    if not isinstance(data,dict):data={}
    return {key:str(data.get(key) or '') for key in ('card','profile','sink','port')}

def _software_playback_object(props):
    """Classify software-only playback objects from live PipeWire properties."""
    media_class=str(props.get('media.class') or '').casefold()
    device_class=str(props.get('device.class') or '').casefold()
    driver=str(props.get('alsa.driver_name') or '').casefold()
    factory=str(props.get('factory.name') or '').casefold()
    virtual=str(props.get('node.virtual') or props.get('device.virtual') or '').casefold()
    return (media_class.endswith('/virtual') or device_class in ('monitor','virtual')
            or driver=='snd_aloop' or virtual in ('true','1','yes')
            or 'support.null-audio-sink' in factory)

def _internal_audio_tag(props):
    if str(props.get('alsa.driver_name') or '').casefold()=='snd_aloop':return '[LOOP] '
    if str(props.get('device.class') or '').casefold()=='monitor':return '[MON] '
    return '[INT] '

def _internal_audio(props):
    return _software_playback_object(props)

def _physical_devices(graph):
    return {str(item['id']):item for item in pw_objects('Device',graph)
            if pw_props(item).get('media.class')=='Audio/Device'
            and str(pw_props(item).get('device.name') or '')}

def _device_options(item):
    params=(item.get('info') or {}).get('params') or {}
    def rows(key):
        value=params.get(key) or []
        return value if isinstance(value,list) else []
    profiles=[{'index':int(row['index']),'name':str(row.get('name') or ''),
               'label':str(row.get('description') or row.get('name') or ''),
               'available':str(row.get('available','unknown'))}
              for row in rows('EnumProfile') if isinstance(row,dict) and isinstance(row.get('index'),int)
              and str(row.get('name') or '').strip().lower()!='off'
]
    routes=[{'index':int(row['index']),'name':str(row.get('name') or ''),
             'label':str(row.get('description') or row.get('name') or ''),
             'available':str(row.get('available','unknown')),
             'profiles':row.get('profiles') if isinstance(row.get('profiles'),list) else [],
             'devices':row.get('devices') if isinstance(row.get('devices'),list) else []}
            for row in rows('EnumRoute') if isinstance(row,dict)
            and isinstance(row.get('index'),int) and str(row.get('direction')).lower()=='output'
            and str(row.get('name') or '').strip().lower() not in ('off','[out] off')
]
    current=next((row for row in rows('Profile') if isinstance(row,dict)),{})
    active_routes=[int(row['index']) for row in rows('Route')
                   if isinstance(row,dict) and isinstance(row.get('index'),int)
                   and str(row.get('direction')).lower()=='output']
    return profiles,routes,current.get('index'),active_routes

def _wait_card(card, predicate, timeout=6):
    deadline=time.monotonic()+timeout
    while True:
        try:
            item=_find_card(audio_topology(),card)
            if predicate(item):return item
        except RuntimeError:pass
        if time.monotonic()>=deadline:raise RuntimeError('Playback device did not settle after profile/route change: '+card)
        time.sleep(.1)

def _route_node(card, choice):
    graph=pw_graph()
    device=pw_device(card,graph)
    nodes=[x for x in pw_objects('Node',graph) if str(pw_props(x).get('device.id'))==str(device['id'])
           and pw_props(x).get('media.class')=='Audio/Sink']
    # wpctl set-route needs the playback node with card.profile.device, not the card ID.
    devices=choice.get('devices') or []
    matched=[x for x in nodes if any(str(pw_props(x).get('card.profile.device',''))==str(d) for d in devices)]
    candidates=matched if devices else nodes
    if len(candidates)!=1:
        raise RuntimeError('Cannot uniquely identify a playback node for route '+str(choice['index'])+' on '+card)
    return candidates[0]['id']
def audio_topology():
    graph=pw_graph();devices=_physical_devices(graph);cards=[]
    nodes=pw_objects('Node',graph)
    for key,item in devices.items():
        props=pw_props(item);name=str(props.get('device.name') or '')
        internal=_internal_audio(props)
        profiles,routes,active,active_routes=_device_options(item)
        sinks=[]
        for node in nodes:
            data=pw_props(node)
            if data.get('media.class')!='Audio/Sink' or str(data.get('device.id'))!=key:continue
            sink_name=str(data.get('node.name') or '')
            if not sink_name:continue
            restricted=internal or _internal_audio(data)
            label=str(data.get('node.description') or data.get('node.nick') or sink_name)
            sinks.append({'name':sink_name,'label':(_internal_audio_tag(data) if restricted else '')+label,
                          'internal':restricted,'cardName':name,'cardIndex':key,
                          'deviceProfile':str(data.get('device.profile.name') or ''),
                          'profileDevice':data.get('card.profile.device')})
        label=str(props.get('device.description') or name)
        cards.append({'name':name,'index':key,'label':(_internal_audio_tag(props) if internal else '')+label,
                      'internal':internal,'profiles':profiles,'routes':routes,
                      'activeProfile':active,'activeRoutes':active_routes,'sinks':sinks})
    # Only exposed playback sinks belong in Audio output. Unowned virtual
    # playback nodes remain visible as internal choices; sources stay in Input.
    owned={sink['name'] for card in cards for sink in card['sinks']}
    for node in nodes:
        data=pw_props(node)
        if data.get('media.class')!='Audio/Sink':continue
        name=str(data.get('node.name') or '')
        if not name or name in owned:continue
        label=str(data.get('node.description') or data.get('node.nick') or name)
        cardname='internal:'+str(node['id'])
        cards.append({'name':cardname,'index':node['id'],
                      'label':_internal_audio_tag(data)+label,'internal':True,
                      'profiles':[{'index':0,'name':'current','label':'Current live profile','available':'yes'}],
                      'routes':[],'activeProfile':0,
                      'activeRoutes':[],'sinks':[{'name':name,
                      'label':_internal_audio_tag(data)+label,'internal':True,
                      'cardName':cardname,'cardIndex':str(node['id']),
                      'deviceProfile':'','profileDevice':None}]})
    cards.sort(key=lambda row:(row['internal'],row['label'].casefold()))
    return {'cards':cards,'saved':saved_output_route()}
def sink_owner(sink,cards):
    return next((card for card in cards if card['name']==sink.get('cardName')),None)

def _find_card(topology,name):
    card=next((item for item in topology['cards'] if item['name']==name),None)
    if card is None:raise RuntimeError('Playback device unavailable: '+str(name))
    return card

def _profile_choice(card,value):
    try:index=int(value)
    except (TypeError,ValueError):raise ValueError('Select a valid device profile')
    row=next((p for p in card['profiles'] if p['index']==index and p['available'] not in ('no','false')),None)
    if row is None or row['name'].strip().lower()=='off':raise ValueError('Playback profile is unavailable for this device')
    return row

def _route_choice(card,value,profile_index=None):
    try:index=int(value)
    except (TypeError,ValueError):raise ValueError('Select a valid output route')
    row=next((r for r in card['routes'] if r['index']==index and r['available'] not in ('no','false')
              and r['name'].strip().lower() not in ('off','[out] off')
              and (profile_index is None or not r['profiles'] or profile_index in r['profiles'])),None)
    if row is None:raise ValueError('Route is unavailable for this profile')
    return row

def set_output_profile(card,profile,topology=None):
    topology=topology or audio_topology();item=_find_card(topology,card)
    choice=_profile_choice(item,profile)
    if item.get('internal'):
        if choice['index']!=item['activeProfile']:raise RuntimeError('This software sink has no independently selectable profile')
        return audio_topology()
    if item['activeProfile']!=choice['index']:
        result=run([exe('wpctl'),'set-profile',str(item['index']),str(choice['index'])],False,12,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not change output profile')
    _wait_card(card,lambda row:row['activeProfile']==choice['index'])
    return audio_topology()
def set_output_route(card,route):
    item=_find_card(audio_topology(),card)
    choice=_route_choice(item,route,item['activeProfile'])
    if choice['index'] in item['activeRoutes']:
        return audio_topology()
    node=_route_node(card,choice)
    result=run([exe('wpctl'),'set-route',str(node),str(choice['index'])],False,12,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not change output route')
    _wait_card(card,lambda row:choice['index'] in row['activeRoutes'])
    return audio_topology()
# Input selection: live WirePlumber device parameters, with a reversible
# preview for profiles/routes that create their source nodes only when active.
