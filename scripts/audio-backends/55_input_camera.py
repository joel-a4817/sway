def _param_rows(item,key):
    rows=((item.get('info') or {}).get('params') or {}).get(key) or []
    return rows if isinstance(rows,list) else []

def _available_option(row):
    return str(row.get('available','unknown')).lower() not in ('no','false','unavailable','not available','0')

def input_topology():
    graph=pw_graph();result=[]
    for device in pw_objects('Device',graph):
        props=pw_props(device)
        if props.get('media.class')!='Audio/Device':continue
        name=str(props.get('device.name') or '')
        if not name:continue
        routes=[{'index':r['index'],'name':str(r.get('name') or ''),
                 'label':str(r.get('description') or r.get('name') or ''),
                 'available':str(r.get('available','unknown')),'profiles':r.get('profiles') or [],
                 'devices':r.get('devices') or []}
                for r in _param_rows(device,'EnumRoute') if isinstance(r,dict)
                and isinstance(r.get('index'),int) and str(r.get('direction')).lower()=='input'
                and str(r.get('name') or '').lower() not in ('off','[in] off')]
        # EnumProfile is the authoritative menu. A route may only appear after
        # the profile is activated; never use current EnumRoute to hide it.
        profiles=[{'index':r['index'],'name':str(r.get('name') or ''),
                   'label':str(r.get('description') or r.get('name') or ''),
                   'available':str(r.get('available','unknown'))}
                  for r in _param_rows(device,'EnumProfile') if isinstance(r,dict)
                  and isinstance(r.get('index'),int)
                  and str(r.get('name') or '').lower()!='off']
        active=next((r.get('index') for r in _param_rows(device,'Profile') if isinstance(r,dict)),None)
        active_routes=[r['index'] for r in _param_rows(device,'Route') if isinstance(r,dict)
                       and isinstance(r.get('index'),int) and str(r.get('direction')).lower()=='input']
        sources=[]
        for node in pw_objects('Node',graph):
            np=pw_props(node);nn=str(np.get('node.name') or '')
            if np.get('media.class')=='Audio/Source' and str(np.get('device.id'))==str(device['id']) and nn:
                sources.append({'name':nn,'label':('[MON] ' if nn.endswith('.monitor') or str(np.get('device.class') or '').lower()=='monitor' else '')+str(np.get('node.description') or nn),
                                'profileDevice':np.get('card.profile.device')})
        if profiles or routes or sources:
            result.append({'name':name,'label':(_internal_audio_tag(props) if _internal_audio(props) else '')+str(props.get('device.description') or name),
                           'profiles':profiles,'routes':routes,'activeProfile':active,
                           'activeRoutes':active_routes,'sources':sources})
    owned={x['name'] for card in result for x in card['sources']}
    for node in pw_objects('Node',graph):
        props=pw_props(node);name=str(props.get('node.name') or '')
        if props.get('media.class')!='Audio/Source' or not name or name in owned:continue
        result.append({'name':'virtual:'+name,'label':('[MON] ' if name.endswith('.monitor') or str(props.get('device.class') or '').lower()=='monitor' else '[VIRT] ')+str(props.get('node.description') or name),
                       'profiles':[{'index':0,'name':'current','label':'Current live profile','available':'yes'}],
                       'routes':[],'activeProfile':0,'activeRoutes':[],
                       'sources':[{'name':name,'label':('[MON] ' if name.endswith('.monitor') or str(props.get('device.class') or '').lower()=='monitor' else '')+str(props.get('node.description') or name),'profileDevice':None}]})
    # Pulse compatibility can expose sink monitors as sources without an
    # Audio/Source node. Show these too, with their live descriptions.
    pulse=run([exe('pactl'),'-f','json','list','sources'],False,8,media_env())
    if pulse.returncode:raise RuntimeError(pulse.stderr.strip() or 'Cannot enumerate input sources')
    known={source['name'] for row in result for source in row['sources']}
    for source in json.loads(pulse.stdout):
        name=str(source.get('name') or '')
        if not name or name in known:continue
        props=source.get('properties') or {}
        label=str(source.get('description') or name)
        monitor=(name.endswith('.monitor') or source.get('monitor_of_sink') not in (None,'',4294967295)
                 or str(props.get('device.class') or '').lower()=='monitor')
        tag='[MON] ' if monitor else '[VIRT] '
        result.append({'name':'virtual:'+name,'label':tag+label,
                       'profiles':[], 'routes':[], 'activeProfile':None,'activeRoutes':[],
                       'sources':[{'name':name,'label':tag+label,'profileDevice':None}]})
        known.add(name)
    return result

def _input_card(name):
    row=next((d for d in input_topology() if d['name']==name),None)
    if row is None:raise RuntimeError('Input device disappeared: '+name)
    return row

def _input_route_node(card,route):
    graph=pw_graph();device=pw_device(card,graph)
    nodes=[n for n in pw_objects('Node',graph) if pw_props(n).get('media.class')=='Audio/Source'
           and str(pw_props(n).get('device.id'))==str(device['id'])
           and (not route['devices'] or str(pw_props(n).get('card.profile.device')) in
                {str(x) for x in route['devices']})]
    if len(nodes)!=1:raise RuntimeError('Cannot uniquely identify source for input route')
    return nodes[0]['id']

def _input_set_profile(card,index):
    graph=pw_graph();device=pw_device(card,graph)
    r=run([exe('wpctl'),'set-profile',str(device['id']),str(index)],False,12,media_env())
    if r.returncode:raise RuntimeError(r.stderr.strip() or 'Input profile failed')
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        if _input_card(card)['activeProfile']==index:return
        time.sleep(.1)
    raise RuntimeError('Input profile did not become active')

def _input_set_route(card,index):
    current=_input_card(card)
    route=next((r for r in current['routes'] if r['index']==index),None)
    if route is None:raise RuntimeError('Input route disappeared')
    node=_input_route_node(card,route)
    r=run([exe('wpctl'),'set-route',str(node),str(index)],False,12,media_env())
    if r.returncode:raise RuntimeError(r.stderr.strip() or 'Input route failed')
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        if index in _input_card(card)['activeRoutes']:return
        time.sleep(.1)
    raise RuntimeError('Input route did not become active')

def _input_ports(source):
    r=run([exe('pactl'),'-f','json','list','sources'],False,8,media_env())
    if r.returncode:raise RuntimeError(r.stderr.strip() or 'Cannot enumerate input ports')
    item=next((x for x in json.loads(r.stdout) if x.get('name')==source),None)
    if item is None:raise RuntimeError('Input source disappeared')
    ap=item.get('active_port')
    return ([{'name':x['name'],'label':x.get('description') or x['name'],
              'available':x.get('availability','unknown')} for x in item.get('ports') or [] if x.get('name')],
            ap.get('name','') if isinstance(ap,dict) else str(ap or ''))

_OPTION_TAGS=(('[N/A] ','N/A'),('[INT] ','INT'),('[MON] ','MON'),
              ('[LOOP] ','LOOP'),('[VIRT] ','VIRT'))
def _input_label(row):
    label=str(row.get('label') or row.get('name') or row.get('index') or '')
    match=next(((prefix,status) for prefix,status in _OPTION_TAGS if label.startswith(prefix)),None)
    status,label=(match[1],label[len(match[0]):]) if match else ('',label)
    if label.startswith('Monitor of '):label=label[11:]
    if not _available_option(row):status='N/A'
    return status,label
def _input_selectable_option(row):
    return _input_label(row)[0]==''
def _print_input_row(number,row):
    import textwrap
    status,label=_input_label(row);prefix=(f'[{status}] ' if status else '')+f'[{number}] '
    width=max(1,shutil.get_terminal_size((80,24)).columns-len(prefix))
    lines=textwrap.wrap(label,width=width,break_long_words=True,break_on_hyphens=False) or ['']
    print(prefix+lines[0]);padding=' '*len(prefix)
    for line in lines[1:]:print(padding+line)
def _pick_input(label,rows,current=None):
    if not rows:raise RuntimeError('No '+label+' choices are exposed')
    selected=next((row for row in rows if str(row.get('index',row.get('name')))==str(current)
                   and _input_selectable_option(row)),None)
    print('\n'+label)
    if selected:_print_input_row(0,dict(selected,label='Current ('+_input_label(selected)[1]+')'))
    else:print('[N/A] [0] Current')
    for number,row in enumerate(rows,1):_print_input_row(number,row)
    while True:
        answer=input('Select: ').strip()
        if answer.lower() in ('q','quit',''):raise KeyboardInterrupt('Selection cancelled')
        if answer.isdecimal():
            number=int(answer)
            if number==0 and selected is not None:return selected
            if 1<=number<=len(rows):
                choice=rows[number-1]
                if _input_selectable_option(choice):return choice
                print('That item cannot be selected.')
                continue
        print('Select an available number.')
def verify_preserved_output(route):
    """Fail closed if an input profile/route disturbed the selected playback path."""
    if not route.get('card') or not route.get('sink') or STOPPED.exists():return
    card=_find_card(audio_topology(),route['card'])
    if route.get('profile') and str(card['activeProfile'])!=str(route['profile']):
        raise RuntimeError('Input selection changed the saved playback profile')
    if route.get('port') and str(route['port']) not in {str(x) for x in card['activeRoutes']}:
        raise RuntimeError('Input selection changed the saved playback route')
    sink=next((x for x in card['sinks'] if x['name']==route['sink']),None)
    if sink is None:raise RuntimeError('Input selection removed the saved playback sink')
    if route.get('port'):
        selected=next((x for x in card['routes'] if str(x['index'])==str(route['port'])),None)
        if selected is None or not _available_option(selected):
            raise RuntimeError('Saved playback route is unavailable after input selection')
        devices={str(x) for x in selected['devices']}
        if devices and sink['profileDevice'] is not None and str(sink['profileDevice']) not in devices:
            raise RuntimeError('Saved playback sink is incompatible with its route')
    if not MODES[audio_mode()][2]:return
    pid=rpid(LOCALMONPID)
    if not alive(pid):raise RuntimeError('Local playback monitor stopped during input selection')
    graph=pw_graph();target=pw_sink(route['sink'],graph)
    for stream in pw_objects('Node',graph):
        props=pw_props(stream)
        if props.get('media.class')!='Stream/Output/Audio':continue
        process=props.get('application.process.id')
        try:
            if os.getpgid(int(process))!=pid:continue
        except (ValueError,TypeError,OSError):continue
        if any(str((link.get('info') or {}).get('output-node-id'))==str(stream['id']) and
               str((link.get('info') or {}).get('input-node-id'))==str(target['id'])
               for link in pw_objects('Link',graph)):return
    raise RuntimeError('Local playback monitor is not linked to the saved sink')



def _input_stage(card, profile=None, route=None):
    current=_input_card(card)
    if not card.startswith('virtual:') and current['profiles']:
        choices=[row for row in current['profiles'] if _available_option(row) and row['name'].strip().lower()!='off']
        if not choices:raise RuntimeError('No usable input profile')
        saved=_read_json(SWITCH_STATE/'input-last-choices.json',{})
        previous=saved.get(card,{}) if isinstance(saved,dict) else {}
        if not isinstance(previous,dict):previous={}
        wanted=profile if profile is not None else previous.get('profile')
        chosen=(next((row for row in choices if str(row['index'])==str(wanted)),None)
                or next((row for row in choices if row['index']==current['activeProfile']),None)
                or choices[0])
        if chosen['index']!=current['activeProfile']:
            _input_set_profile(card,chosen['index'])
        current=_input_card(card)
        profile=chosen['index']
    else:
        previous={}
        profile=current['activeProfile']
    routes=[row for row in current['routes'] if _available_option(row) and
            (not row['profiles'] or profile is None or str(profile) in {str(x) for x in row['profiles']})]
    if routes:
        wanted=route if route is not None else previous.get('route')
        chosen=(next((row for row in routes if str(row['index'])==str(wanted)),None)
                or next((row for row in routes if row['index'] in current['activeRoutes']),None)
                or routes[0])
        if chosen['index'] not in current['activeRoutes']:
            _input_set_route(card,chosen['index'])
        route=chosen['index']
    else:route=None
    current=_input_card(card)
    sources=[row for row in current['sources'] if _input_selectable_option(row)]
    if route is not None:
        selected_route=next(row for row in routes if row['index']==route)
        if selected_route['devices']:
            devices={str(x) for x in selected_route['devices']}
            sources=[row for row in sources if row['profileDevice'] is None or str(row['profileDevice']) in devices]
    if not sources:raise RuntimeError('Input profile/route exposes no source')
    preferred=(previous.get('source'),run([exe('pactl'),'get-default-source'],False,5,media_env()).stdout.strip())
    source=next((row for name in preferred for row in sources if row['name']==name),None) or sources[0]
    ports,active_port=_input_ports(source['name'])
    valid=[row for row in ports if _available_option(row)]
    wanted=previous.get('port')
    port=(next((row for row in valid if row['name']==wanted),None)
          or next((row for row in valid if row['name']==active_port),None)
          or (valid[0] if valid else None))
    if port and port['name']!=active_port:
        result=run([exe('pactl'),'set-source-port',source['name'],port['name']],False,8,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not apply input port')
        if _input_ports(source['name'])[1]!=port['name']:raise RuntimeError('Input port did not become active')
    graph=pw_graph()
    node=next((x for x in pw_objects('Node',graph) if pw_props(x).get('node.name')==source['name']
               and pw_props(x).get('media.class')=='Audio/Source'),None)
    command=([exe('wpctl'),'set-default',str(node['id'])] if node else
             [exe('pactl'),'set-default-source',source['name']])
    result=run(command,False,8,media_env())
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not apply input source')
    if run([exe('pactl'),'get-default-source'],False,5,media_env()).stdout.strip()!=source['name']:
        raise RuntimeError('Input source did not become default')
    return {'profile':profile,'route':route,'source':source['name'],'port':port['name'] if port else ''}

def restore_exact_device_routes(card, expected, direction):
    """Restore routes by their PipeWire profile-device slot, without resetting a shared card."""
    reader=_input_card if direction=='input' else lambda name:_find_card(audio_topology(),name)
    setter=_input_set_route if direction=='input' else set_output_route
    expected=set(expected)
    current=reader(card)
    available={r['index']:r for r in current['routes']}
    if not expected.issubset(available):
        raise RuntimeError('Original '+direction+' route disappeared on '+card)
    if not expected and current['activeRoutes']:
        graph=pw_graph()
        device=pw_device(card,graph)
        enum=[row for row in _param_rows(device,'EnumRoute') if isinstance(row,dict)
              and str(row.get('direction')).lower()==('input' if direction=='input' else 'output')]
        for active_index in list(current['activeRoutes']):
            active=next((row for row in enum if row.get('index')==active_index),None)
            slots=set(active.get('devices') or []) if active else set()
            off=[row for row in enum if isinstance(row.get('index'),int)
                 and str(row.get('name','')).strip().lower() in ('off','[in] off','[out] off')
                 and slots and slots.intersection(row.get('devices') or [])]
            if len(off)==1:
                node=(_input_route_node(card,{'devices':list(slots),'index':off[0]['index']})
                      if direction=='input' else _route_node(card,{'devices':list(slots),'index':off[0]['index']}))
                result=run([exe('wpctl'),'set-route',str(node),str(off[0]['index'])],False,12,media_env())
                if result.returncode:raise RuntimeError(result.stderr.strip() or 'Off route rejected')
                current=reader(card)
        if current['activeRoutes']:
            raise RuntimeError('Original empty '+direction+' route set not restored on '+card+
                               '; active='+str(current['activeRoutes']))
    # A route belongs to one or more profile-device slots. Setting the original
    # route on that slot replaces the staged route without changing the profile.
    for extra in set(current['activeRoutes'])-expected:
        row=available.get(extra)
        slots=set(row.get('devices') or []) if row else set()
        replacements=[index for index in expected
                      if slots and slots.intersection(available[index].get('devices') or [])]
        if len(replacements)!=1:
            raise RuntimeError('Cannot safely replace extra '+direction+' route '+str(extra)+
                               ' on '+card+'; original route for its device slot is ambiguous')
        replacement=available[replacements[0]]
        node=(_input_route_node(card,replacement) if direction=='input' else _route_node(card,replacement))
        result=run([exe('wpctl'),'set-route',str(node),str(replacements[0])],False,12,media_env())
        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Route replacement rejected')
    for index in expected-set(reader(card)['activeRoutes']):setter(card,index)
    actual=set(reader(card)['activeRoutes'])
    if actual!=expected:
        raise RuntimeError('Original '+direction+' routes not restored on '+card+
                           '; extra='+str(sorted(actual-expected))+', missing='+str(sorted(expected-actual)))

def input_stream_origins():
    """Capture recording targets before any profile, route or default changes."""
    sources=run([exe('pactl'),'-f','json','list','sources'],False,8,media_env())
    streams=run([exe('pactl'),'-f','json','list','source-outputs'],False,8,media_env())
    if sources.returncode or streams.returncode:
        raise RuntimeError('Cannot snapshot recording streams before input selection')
    names={str(row.get('index')):row.get('name') for row in json.loads(sources.stdout)}
    origins={}
    for row in json.loads(streams.stdout):
        index=row.get('index')
        if not isinstance(index,int):continue
        name=names.get(str(row.get('source')))
        if not name:raise RuntimeError('Cannot identify original source for recording stream '+str(index))
        origins[index]=name
    graph=pw_graph();native={}
    for node in pw_objects('Node',graph):
        if pw_props(node).get('media.class')!='Stream/Input/Audio':continue
        targets={str((link.get('info') or {}).get('output-node-id')) for link in pw_objects('Link',graph)
                 if str((link.get('info') or {}).get('input-node-id'))==str(node['id'])}
        if len(targets)>1:raise RuntimeError('Recording stream has multiple original source targets: '+str(node['id']))
        if targets:native[node['id']]=next(iter(targets))
    return origins,native

def select_input_interactive(finalize=None):
    with LOCK:
        card=_pick_input('Audio input | Device',input_topology())['name']
        original=_input_card(card)
        old_source=getattr(MEDIA_TRANSACTION,'input_default',None) or run([exe('pactl'),'get-default-source'],False,5,media_env()).stdout.strip()
        saved_output=saved_output_route()
        playback_before=getattr(MEDIA_TRANSACTION,'input_playback_before',None)
        if playback_before is None and saved_output.get('card')==card and not STOPPED.exists():
            live_playback=_find_card(audio_topology(),card)
            playback_before={'profile':live_playback['activeProfile'],'routes':list(live_playback['activeRoutes']),
                             'monitor':alive(rpid(LOCALMONPID))}
        prior_streams,prior_native=getattr(MEDIA_TRANSACTION,'input_stream_origins',None) or input_stream_origins()
        moved=[];native_moved=[];success=False
        old_port=None;chosen_source=None
        stage_original=getattr(MEDIA_TRANSACTION,'input_original',None)
        stage_ports=getattr(MEDIA_TRANSACTION,'input_ports',{})
        if stage_original is not None:original=stage_original
        try:
            if stage_original is not None:
                _input_stage(card)
            live=_input_card(card)
            profile=(_pick_input('Audio input | Profile',live['profiles'],live['activeProfile'])
                     if live['profiles'] else {'index':live['activeProfile'],'name':'current'})
            if profile['index'] is not None and _input_card(card)['activeProfile']!=profile['index'] and not card.startswith('virtual:'):
                _input_set_profile(card,profile['index'])
            current=_input_card(card)
            routes=[r for r in current['routes'] if _available_option(r) and (not r['profiles'] or profile['index'] is None or str(profile['index']) in {str(x) for x in r['profiles']})]
            route=_pick_input('Audio input | Route',routes,current['activeRoutes'][0] if current['activeRoutes'] else None) if routes else None
            if route and route['index'] not in _input_card(card)['activeRoutes']:
                _input_set_route(card,route['index'])
            current=_input_card(card)
            if saved_output.get('card')==card:verify_preserved_output(saved_output)
            candidates=[row for row in current['sources'] if _input_selectable_option(row)]
            if route and route['devices']:
                candidates=[x for x in candidates if x['profileDevice'] is None or str(x['profileDevice']) in {str(v) for v in route['devices']}]
            source=_pick_input('Audio input | Source',candidates,old_source)
            chosen_source=source['name']
            if not any(row['name']==chosen_source for row in _input_card(card)['sources']):
                raise RuntimeError('Selected source disappeared before port selection')
            ports,old_port=_input_ports(chosen_source)
            port=_pick_input('Audio input | Port',ports,old_port) if ports else None
            # Capture selection is not a playback-gain transaction. Keep
            # players running and do not normalize playback or capture gains.
            if port:
                r=run([exe('pactl'),'set-source-port',chosen_source,port['name']],False,8,media_env())
                if r.returncode:raise RuntimeError(r.stderr.strip() or 'Input port failed')
                if _input_ports(chosen_source)[1]!=port['name']:raise RuntimeError('Input port not confirmed')
            graph=pw_graph();device_id=None if card.startswith('virtual:') else pw_device(card,graph)['id']
            node=next((n for n in pw_objects('Node',graph) if pw_props(n).get('media.class')=='Audio/Source' and pw_props(n).get('node.name')==chosen_source and (device_id is None or str(device_id)==str(pw_props(n).get('device.id')))),None)
            if node is None and not card.startswith('virtual:'):
                raise RuntimeError('Selected source disappeared')
            command=([exe('wpctl'),'set-default',str(node['id'])] if node is not None else
                     [exe('pactl'),'set-default-source',chosen_source])
            r=run(command,False,8,media_env())
            if r.returncode:raise RuntimeError(r.stderr.strip() or 'Input default failed')
            if run([exe('pactl'),'get-default-source'],False,5,media_env()).stdout.strip()!=chosen_source:
                raise RuntimeError('Input default not confirmed')
            r=run([exe('pactl'),'-f','json','list','source-outputs'],False,8,media_env())
            if r.returncode:raise RuntimeError('Cannot enumerate recording streams')
            for item in json.loads(r.stdout):
                index=item.get('index')
                if not isinstance(index,int):continue
                if index not in prior_streams:raise RuntimeError('Recording stream appeared after input snapshot: '+str(index))
                r=run([exe('pactl'),'move-source-output',str(index),chosen_source],False,8,media_env())
                if r.returncode:raise RuntimeError('Recording stream '+str(index)+' could not be moved')
                moved.append(index)
            r=run([exe('pactl'),'-f','json','list','source-outputs'],False,8,media_env())
            if r.returncode:raise RuntimeError('Cannot verify recording streams')
            source_rows=run([exe('pactl'),'-f','json','list','sources'],False,8,media_env())
            if source_rows.returncode:raise RuntimeError('Cannot verify selected source index')
            pulse_source=next((x for x in json.loads(source_rows.stdout) if x.get('name')==chosen_source),None)
            if pulse_source is None:raise RuntimeError('Selected source vanished during verification')
            target=pulse_source['index']
            verified_streams={item.get('index'):item for item in json.loads(r.stdout)}
            for index in moved:
                if index not in verified_streams:raise RuntimeError('Recording stream '+str(index)+' disappeared during verification')
            for item in verified_streams.values():
                if item.get('index') in moved and str(item.get('source'))!=str(target):
                    raise RuntimeError('Recording stream '+str(item['index'])+' did not reach selected source')
            # Native PipeWire capture streams are not necessarily visible to pactl.
            # Verify the complete live graph, and retarget any remaining active stream.
            graph=pw_graph()
            for stream in pw_objects('Node',graph):
                if pw_props(stream).get('media.class')!='Stream/Input/Audio':continue
                if node is None:
                    if any(str((link.get('info') or {}).get('input-node-id'))==str(stream['id'])
                           for link in pw_objects('Link',graph)):
                        raise RuntimeError('Native recording stream cannot be retargeted to a Pulse-only monitor source')
                    continue
                sid=stream['id']
                links=[x for x in pw_objects('Link',graph) if str((x.get('info') or {}).get('input-node-id'))==str(sid)]
                if not links:continue
                sources={str((x.get('info') or {}).get('output-node-id')) for x in links}
                if sources=={str(node['id'])}:continue
                if len(sources)!=1:raise RuntimeError('Recording stream '+str(sid)+' has multiple source targets')
                old_node=prior_native.get(sid)
                if old_node is None:raise RuntimeError('Recording stream appeared after input snapshot: '+str(sid))
                result=run([exe('pw-metadata'),'-n','default',str(sid),'target.node',str(node['id']),'Spa:Id'],False,8,media_env())
                if result.returncode:raise RuntimeError('Recording stream '+str(sid)+' cannot be retargeted')
                native_moved.append((sid,old_node))
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    now=pw_graph()
                    if not any(n['id']==sid for n in pw_objects('Node',now)):
                        raise RuntimeError('Recording stream '+str(sid)+' disappeared during selection')
                    targets={str((x.get('info') or {}).get('output-node-id')) for x in pw_objects('Link',now)
                             if str((x.get('info') or {}).get('input-node-id'))==str(sid)}
                    if targets=={str(node['id'])}:break
                    time.sleep(.1)
                else:raise RuntimeError('Recording stream '+str(sid)+' link not confirmed')
            if saved_output.get('card')==card:verify_preserved_output(saved_output)
            choices=_read_json(SWITCH_STATE/'input-last-choices.json',{})
            if not isinstance(choices,dict):choices={}
            choices[card]={'profile':profile['index'],'route':route['index'] if route else None,'source':chosen_source,'port':port['name'] if port else ''}
            if finalize is not None:finalize()
            _write_json(SWITCH_STATE/'input-last-choices.json',choices)
            success=True
            return {'device':card,'profile':profile['index'],'route':route['index'] if route else None,
                    'source':chosen_source,'port':port['name'] if port else None}
        finally:
            if not success:
                failures=[]
                def rollback_step(label,action):
                    try:action()
                    except Exception as error:failures.append(label+': '+str(error))
                def restore_native(sid,old_node):
                    r=run([exe('pw-metadata'),'-n','default',str(sid),'target.node',str(old_node),'Spa:Id'],False,8,media_env())
                    if r.returncode:raise RuntimeError(r.stderr.strip() or 'retarget rejected')
                def restore_profile():
                    if card.startswith('virtual:') or original['activeProfile'] is None:return
                    if _input_card(card)['activeProfile']==original['activeProfile']:return
                    graph=pw_graph()
                    dev=pw_device(card,graph)
                    result=run([exe('wpctl'),'set-profile',str(dev['id']),str(original['activeProfile'])],False,12,media_env())
                    if result.returncode:raise RuntimeError(result.stderr.strip() or 'profile restore rejected')
                    if _input_card(card)['activeProfile']!=original['activeProfile']:
                        raise RuntimeError('original profile not confirmed')
                rollback_step('input profile',restore_profile)
                rollback_step('input route set',lambda:restore_exact_device_routes(card,original['activeRoutes'],'input'))
                def restore_native_checked(sid,old_node):
                    graph=pw_graph()
                    if not any(str(n['id'])==str(old_node) for n in pw_objects('Node',graph)):
                        raise RuntimeError('original source node no longer exists after profile restore')
                    restore_native(sid,old_node)
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline:
                        graph=pw_graph()
                        targets={str((link.get('info') or {}).get('output-node-id'))
                                 for link in pw_objects('Link',graph)
                                 if str((link.get('info') or {}).get('input-node-id'))==str(sid)}
                        if targets=={str(old_node)}:return
                        time.sleep(.1)
                    raise RuntimeError('original capture link not confirmed')
                for sid,old_node in reversed(list(prior_native.items())):
                    rollback_step('native recording stream '+str(sid),
                                  lambda sid=sid,old_node=old_node:restore_native_checked(sid,old_node))
                def restore_pulse(index,source):
                    r=run([exe('pactl'),'move-source-output',str(index),str(source)],False,8,media_env())
                    if r.returncode:raise RuntimeError(r.stderr.strip() or 'stream move rejected')
                for index,source in prior_streams.items():
                    if source is not None:
                        rollback_step('recording stream '+str(index),lambda index=index,source=source:restore_pulse(index,source))
                def restore_default():
                    if not old_source:return
                    current=run([exe('pactl'),'get-default-source'],False,5,media_env())
                    if current.returncode:raise RuntimeError('cannot read default source')
                    if current.stdout.strip()==old_source:return
                    r=run([exe('pactl'),'set-default-source',old_source],False,8,media_env())
                    if r.returncode:raise RuntimeError(r.stderr.strip() or 'default source restore rejected')
                rollback_step('default source',restore_default)
                def restore_port(source,port):
                    r=run([exe('pactl'),'set-source-port',source,port],False,8,media_env())
                    if r.returncode:raise RuntimeError(r.stderr.strip() or 'port restore rejected')
                for staged_source,staged_port in stage_ports.items():
                    if staged_port:
                        rollback_step('staged input port '+staged_source,
                                      lambda source=staged_source,port=staged_port:restore_port(source,port))
                if old_port and chosen_source and chosen_source not in stage_ports:
                    rollback_step('input port '+chosen_source,
                                  lambda:restore_port(chosen_source,old_port))
                if playback_before is not None:
                    def restore_playback():
                        live=_find_card(audio_topology(),card)
                        old_profile=playback_before['profile']
                        if old_profile is not None and live['activeProfile']!=old_profile:
                            set_output_profile(card,old_profile)
                        restore_exact_device_routes(card,playback_before['routes'],'output')
                        if playback_before['monitor']:
                            restored=choose_output_route(dict(saved_output,_remembered=True))
                            if restored['sink']!=saved_output['sink']:
                                raise RuntimeError('Previous playback sink unavailable')
                            start_local_monitor(restored,resolved=True,persist=False)
                        verify_preserved_output(saved_output)
                    rollback_step('playback',restore_playback)
                if failures:raise RuntimeError('Input rollback incomplete: '+', '.join(failures))

def restore_camera_clients(snapshot):
    failures=[]
    for sid,old in reversed(snapshot):
        result=run([exe('pw-metadata'),'-n','default',str(sid),'target.node',str(old),'Spa:Id'],False,8,media_env())
        if result.returncode:
            failures.append(str(sid)+': '+(result.stderr.strip() or 'retarget failed'))
            continue
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            graph=pw_graph()
            if not any(str(n['id'])==str(sid) for n in pw_objects('Node',graph)):
                failures.append(str(sid)+': client disappeared before rollback confirmation')
                break
            targets={str((link.get('info') or {}).get('output-node-id'))
                     for link in pw_objects('Link',graph)
                     if str((link.get('info') or {}).get('input-node-id'))==str(sid)}
            if targets=={str(old)}:break
            time.sleep(.1)
        else:failures.append(str(sid)+': original camera link not restored')
    if failures:raise RuntimeError('Camera clients not restored: '+', '.join(failures))
    return {'restored':True}

def camera_selection_status(node_id):
    graph=pw_graph()
    selected=next((x for x in pw_objects('Node',graph) if x['id']==node_id and
                   pw_props(x).get('media.class')=='Video/Source'),None)
    if selected is None:raise RuntimeError('Camera endpoint disappeared')
    prior=[];failures=[]
    try:
        for stream in pw_objects('Node',graph):
            if pw_props(stream).get('media.class')!='Stream/Input/Video':continue
            sid=stream['id']
            links=[x for x in pw_objects('Link',graph) if str((x.get('info') or {}).get('input-node-id'))==str(sid)]
            if not links:
                failures.append(str(pw_props(stream).get('application.name') or sid)+' (no active link)')
                continue
            sources={str((x.get('info') or {}).get('output-node-id')) for x in links}
            if sources=={str(node_id)}:continue
            if len(sources)!=1:
                failures.append(str(sid)+' (multiple source targets)');continue
            previous=next(iter(sources))
            r=run([exe('pw-metadata'),'-n','default',str(sid),'target.node',str(node_id),'Spa:Id'],False,8,media_env())
            if r.returncode:
                failures.append(str(sid)+' (retarget rejected)');continue
            prior.append((sid,previous))
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                current=pw_graph()
                if not any(n['id']==sid for n in pw_objects('Node',current)):
                    failures.append(str(sid)+' (stream disappeared)');break
                current_sources={str((x.get('info') or {}).get('output-node-id')) for x in pw_objects('Link',current)
                                 if str((x.get('info') or {}).get('input-node-id'))==str(sid)}
                if current_sources=={str(node_id)}:break
                time.sleep(.1)
            else:failures.append(str(sid)+' (link not confirmed)')
        if failures:raise RuntimeError('Camera clients not switched: '+', '.join(failures))
        return {'selected':str(pw_props(selected).get('node.name')),'previousClients':prior}
    except Exception as error:
        rollback=[]
        for sid,old in reversed(prior):
            r=run([exe('pw-metadata'),'-n','default',str(sid),'target.node',old,'Spa:Id'],False,8,media_env())
            if r.returncode:
                rollback.append(str(sid));continue
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                current=pw_graph()
                if not any(n['id']==sid for n in pw_objects('Node',current)):break
                sources={str((x.get('info') or {}).get('output-node-id')) for x in pw_objects('Link',current)
                         if str((x.get('info') or {}).get('input-node-id'))==str(sid)}
                if sources=={old}:break
                time.sleep(.1)
            else:rollback.append(str(sid)+' (link not restored)')
        if rollback:raise RuntimeError(str(error)+'; camera stream rollback failed: '+', '.join(rollback)) from error
        raise

OUTPUT_PREVIEW=SWITCH_STATE/'output-selector-preview.json'
WEB_PREVIEW_LEASE=45

