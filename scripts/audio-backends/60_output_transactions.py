def _preview_owner_alive(snapshot):
    if snapshot.get('owner')!='cli':return True
    try:
        pid=int(snapshot['ownerPid']);start=int(snapshot['ownerStart'])
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()
        return fields[0]!='Z' and int(fields[19])==start
    except (OSError,ValueError,KeyError,IndexError,TypeError):return False

def _recover_stale_cli_preview():
    snapshot=_read_json(OUTPUT_PREVIEW,None)
    if not isinstance(snapshot,dict):return
    if snapshot.get('owner')=='cli' and not _preview_owner_alive(snapshot):
        cancel_output_preview('cli',recover=True)
    elif snapshot.get('owner')=='web' and time.time()-float(snapshot.get('renewed',0))>WEB_PREVIEW_LEASE:
        cancel_output_preview('web',recover=True)

def watch_output_preview(stop):
    while not stop.wait(5):
        try:
            with LOCK:_recover_stale_cli_preview()
        except (OSError,ValueError,RuntimeError) as error:
            print('Output preview recovery: '+str(error),flush=True)

def renew_output_preview(token):
    with LOCK:
        snapshot=_read_json(OUTPUT_PREVIEW,None)
        if not isinstance(snapshot,dict) or snapshot.get('owner')!='web' or snapshot.get('token')!=token:
            raise RuntimeError('This browser does not own the output selection')
        if time.time()-float(snapshot.get('renewed',0))>WEB_PREVIEW_LEASE:
            _recover_stale_cli_preview()
            raise RuntimeError('Output selection expired; reopen the picker')
        snapshot['renewed']=time.time()
        _write_json(OUTPUT_PREVIEW,snapshot)
        return {'renewed':True}

def _require_output_preview(origin,token=None):
    snapshot=_read_json(OUTPUT_PREVIEW,None)
    if not isinstance(snapshot,dict) or snapshot.get('owner')!=origin:
        raise RuntimeError('Begin an output selection in this picker first')
    if origin=='web' and (not token or snapshot.get('token')!=token):
        raise RuntimeError('This browser does not own the output selection')
    if origin=='web' and time.time()-float(snapshot.get('renewed',0))>WEB_PREVIEW_LEASE:
        _recover_stale_cli_preview()
        raise RuntimeError('Output selection expired; reopen the picker')
    if origin=='cli' and (not _preview_owner_alive(snapshot) or
                          str(snapshot.get('ownerPid'))!=str(token)):
        raise RuntimeError('Media Control picker is no longer running')
    return snapshot

def begin_output_preview(origin='web',owner_pid=None,owner_start=None):
    with LOCK:
        if audio_mode() not in MODES or not MODES[audio_mode()][2]:
            raise RuntimeError('Laptop output can only be configured while the current audio output includes Laptop')
        _recover_stale_cli_preview()
        current=_read_json(OUTPUT_PREVIEW,None)
        if isinstance(current,dict):
            owner=current.get('owner')
            if owner not in ('web','cli'):
                raise RuntimeError('Cannot recover output selection with unknown owner')
            cancel_output_preview(owner,recover=True)
        elif OUTPUT_PREVIEW.exists():
            OUTPUT_PREVIEW.unlink(missing_ok=True)
        if origin=='cli' and (not owner_pid or not owner_start):raise ValueError('Missing CLI picker identity')
        top=audio_topology()
        token=uuid.uuid4().hex if origin=='web' else None
        inputs={c['name']:list(c['activeRoutes']) for c in input_topology()}
        pulse_origins,native_origins=input_stream_origins()
        _write_json(OUTPUT_PREVIEW,{'owner':origin,'ownerPid':owner_pid,'ownerStart':owner_start,'token':token,'renewed':time.time(),'cards':{c['name']:{'profile':c['activeProfile'],
                        'routes':list(c['activeRoutes'])} for c in top['cards']},
                        'inputs':inputs,'pulseOrigins':pulse_origins,'nativeOrigins':native_origins,
                        'mode':audio_mode(),'output':saved_output_route(),
                        'localMonitor':alive(rpid(LOCALMONPID))})
        try:
            state=output_state()
        except Exception:
            OUTPUT_PREVIEW.unlink(missing_ok=True)
            raise
        if token:state['previewToken']=token
        return state
def cancel_output_preview(origin='web',token=None,recover=False):
    with LOCK:
        snapshot=_read_json(OUTPUT_PREVIEW,None)
        if isinstance(snapshot,dict) and not recover:_require_output_preview(origin,token)
        if isinstance(snapshot,dict) and recover and snapshot.get('owner')!=origin:
            raise RuntimeError('Another output picker owns this selection')
        if not isinstance(snapshot,dict):return output_state()
        if all((lambda live,old:live['activeProfile']==old['profile'] and
                set(old['routes'])==set(live['activeRoutes']))(
                next((c for c in audio_topology()['cards'] if c['name']==name),{'activeProfile':None,'activeRoutes':[]}),prior)
               for name,prior in snapshot.get('cards',{}).items()) and not snapshot.get('staged'):
            OUTPUT_PREVIEW.unlink(missing_ok=True)
            return output_state()
        failures=[]
        for card,prior in snapshot['cards'].items():
            try:
                current=_find_card(audio_topology(),card)
                if prior['profile'] is not None and current['activeProfile']!=prior['profile']:
                    if any(x['index']==prior['profile'] for x in current['profiles']):
                        set_output_profile(card,prior['profile'])
                    else:
                        result=run([exe('wpctl'),'set-profile',str(current['index']),str(prior['profile'])],False,12,media_env())
                        if result.returncode:raise RuntimeError(result.stderr.strip() or 'Could not restore original profile')
                restore_exact_device_routes(card,prior['routes'],'output')
                if card in snapshot.get('inputs',{}):
                    restore_exact_device_routes(card,snapshot['inputs'][card],'input')
            except (RuntimeError,ValueError,OSError) as error:failures.append(card+': '+str(error))
        for sid,source in (snapshot.get('pulseOrigins',{}) if snapshot.get('staged') else {}).items():
            result=run([exe('pactl'),'move-source-output',str(sid),str(source)],False,8,media_env())
            if result.returncode:failures.append('recording stream '+str(sid)+': '+result.stderr.strip())
        for sid,target in (snapshot.get('nativeOrigins',{}) if snapshot.get('staged') else {}).items():
            result=run([exe('pw-metadata'),'-n','default',str(sid),'target.node',str(target),'Spa:Id'],False,8,media_env())
            if result.returncode:failures.append('native recording stream '+str(sid)+': '+result.stderr.strip())
        old=snapshot.get('output') or {}
        if snapshot.get('staged') and not snapshot.get('localMonitor'):
            stop_local_monitor()
        if snapshot.get('localMonitor') and old.get('sink') and not STOPPED.exists():
            try:
                current=saved_output_route()
                if current.get('sink')!=old['sink'] or not alive(rpid(LOCALMONPID)) or snapshot.get('staged'):
                    selected=choose_output_route(dict(old,_remembered=True))
                    if selected['sink']!=old['sink']:raise RuntimeError('Saved sink is unavailable')
                    start_local_monitor(selected,resolved=True)
            except (RuntimeError,ValueError,OSError) as error:failures.append('monitor: '+str(error))
        if failures:raise RuntimeError('Output selection rollback incomplete: '+'; '.join(failures))
        OUTPUT_PREVIEW.unlink(missing_ok=True)
        return output_state()

def _stage_local_route(card, profile, route=None, mode=None,origin='web',token=None):
    preview=_require_output_preview(origin,token)
    # Device/profile/route changes invalidate an active ALSA capture handle.
    # Quiesce the bridge before the first topology mutation so arecord cannot
    # exit with EIO while WirePlumber rebuilds the physical card nodes.
    if not preview.get('bridgeSuspended'):
        preview['bypassWasRunning']=alive(rpid(BYPASSPID))
        preview['bridgeSuspended']=True
        _write_json(OUTPUT_PREVIEW,preview)
        stop_local_monitor()
        stop_bypass()
    current=_find_card(audio_topology(),card)
    chosen=_profile_choice(current,profile)
    if current.get('internal') or _labelled_output_option(current) or _labelled_output_option(chosen):
        raise ValueError('That playback profile cannot be used')
    original=(preview.get('cards') or {}).get(card)
    if original is None:raise RuntimeError('Playback card was not in the preview snapshot')
    if current['activeProfile']!=chosen['index']:
        set_output_profile(card,chosen['index'])
    current=_find_card(audio_topology(),card)
    remembered=remembered_output(card,str(chosen['index']))
    compatible=[r for r in current['routes'] if r['available'] not in ('no','false','unavailable')
                and (not r['profiles'] or chosen['index'] in r['profiles'])]
    if route not in (None,''):
        selected_route=_route_choice(current,route,chosen['index'])
    else:
        selected_route=(next((r for r in compatible if str(r['index'])==str(remembered.get('port'))),None)
                        or next((r for r in compatible if r['index'] in current['activeRoutes']),None)
                        or (compatible[0] if compatible else None))
    if current['routes'] and selected_route is None:raise RuntimeError('No usable route for selected profile')
    if selected_route and selected_route['index'] not in current['activeRoutes']:
        set_output_route(card,selected_route['index'])
    current=_find_card(audio_topology(),card)
    devices={str(x) for x in selected_route['devices']} if selected_route else set()
    sinks=[row for row in current['sinks'] if
           (not devices or row['profileDevice'] is None or str(row['profileDevice']) in devices)]
    if not sinks:raise RuntimeError('Selected profile and route expose no playback sink')
    saved=saved_output_route()
    preferred=(remembered.get('sink'),saved.get('sink') if saved.get('card')==card else None)
    sink=next((row for name in preferred for row in sinks if row['name']==name),None)
    if sink is None:
        # Stage a usable sink without hiding the other live sinks from the Sink menu.
        graph=pw_graph()
        priorities={pw_props(node).get('node.name'):pw_props(node).get('priority.session')
                    for node in pw_objects('Node',graph)}
        def priority(row):
            try:return int(priorities.get(row['name']) or 0)
            except (ValueError,TypeError):return 0
        sink=max(sinks,key=priority)
    selected={'card':card,'profile':str(chosen['index']),'port':str(selected_route['index']) if selected_route else '',
              'sink':sink['name'],'label':sink['label'],'sinkCard':card,'sinkProfile':str(chosen['index'])}
    # Preview uses the full live route but must not overwrite committed choices.
    if not STOPPED.exists() and MODES[mode or audio_mode()][2]:
        start_local_monitor(selected,resolved=True,persist=False)
    master=master_sink()
    MEDIA_TRANSACTION.stage_sink=(selected['sink'] if master=='@DEFAULT_SINK@' or (selected_filter()==NO_FILTER and (mode or audio_mode())=='laptop_laptop') else master)
    snapshot=_read_json(OUTPUT_PREVIEW,{})
    snapshot['staged']=True
    snapshot['bridgeSuspended']=bool(preview.get('bridgeSuspended'))
    snapshot['bypassWasRunning']=bool(preview.get('bypassWasRunning'))
    if origin=='web':snapshot['renewed']=time.time()
    _write_json(OUTPUT_PREVIEW,snapshot)
    result=output_state()
    result['stageSelection']=selected
    return result


def _labelled_output_option(row):
    label=str(row.get('label') or '')
    return bool(row.get('internal')) or label.startswith(
        ('[N/A] ','[INT] ','[MON] ','[LOOP] ','[VIRT] '))

def select_output_device_stage(card,origin='web',token=None):
    # Both UIs commit the same usable profile at Device Select, before Profile UI.
    with LOCK, (audio_start_change() if STOPPED.exists() else media_change()):
        item=_find_card(audio_topology(),card)
        if item.get('internal') or _labelled_output_option(item):
            raise ValueError('That playback device is informational and cannot be selected')
        usable=[row for row in item['profiles'] if row['available'] not in ('no','false')
                and row['name'].strip().lower()!='off' and not _labelled_output_option(row)]
        if not usable:raise RuntimeError('No usable playback profile for this device')
        active=next((row for row in usable if row['index']==item['activeProfile']),None)
        remembered=remembered_card_profile(card)
        chosen=(next((row for row in usable if str(row['index'])==remembered),None)
                or active or usable[0])
        result=_stage_local_route(card,chosen['index'],origin=origin,token=token)
        result['deviceStageProfile']=chosen['index']
        return result
def apply_output_profile_stage(card,profile,mode=None,origin='web',token=None):
    with LOCK, (audio_start_change() if STOPPED.exists() else media_change()):
        return _stage_local_route(card,profile,mode=mode,origin=origin,token=token)

def apply_output_route_stage(card,profile,route,mode=None,origin='web',token=None):
    with LOCK, (audio_start_change() if STOPPED.exists() else media_change()):
        return _stage_local_route(card,profile,route,mode=mode,origin=origin,token=token)

def choose_output_route(requested=None):
    topology=audio_topology();cards=topology['cards']
    if not cards:raise RuntimeError('No playback device in PipeWire graph')
    explicit=isinstance(requested,dict) and not requested.get('_remembered')
    requested=requested if isinstance(requested,dict) else dict(topology['saved'],_remembered=True)
    wanted=str(requested.get('sink') or '')
    sinks=[sink for card in cards for sink in card['sinks']]
    selected=next((sink for sink in sinks if sink['name']==wanted and
                   (not requested.get('card') or sink['cardName']==requested['card'])),None)
    if selected is None:
        if wanted and explicit:raise RuntimeError('Selected output unavailable: '+wanted)
        try:default=pw_default()
        except RuntimeError:default=''
        selected=next((sink for sink in sinks if sink['name']==default and (not requested.get('card') or sink['cardName']==requested['card'])),None)
        if selected is None:
            # Startup may have several HDMI/speaker sinks while the configured
            # default is the virtual CamillaDSP sink. Prefer the saved device;
            # never silently choose an unrelated card.
            preferred=str(requested.get('card') or '')
            candidates=[x for x in sinks if x.get('cardName')==preferred] if preferred else sinks
            if len(candidates)==1:selected=candidates[0]
            elif not explicit and candidates:
                graph=pw_graph()
                by_name={pw_props(x).get('node.name'):pw_props(x) for x in pw_objects('Node',graph)}
                def priority(row):
                    props=by_name.get(row['name'],{})
                    try:return int(props.get('priority.session') or 0)
                    except (ValueError,TypeError):return 0
                ranked=sorted(candidates,key=priority,reverse=True)
                if len(ranked)==1 or priority(ranked[0])>priority(ranked[1]):selected=ranked[0]
            if selected is None:raise RuntimeError('Select a playback output')
    owner=sink_owner(selected,cards)
    if owner is None:raise RuntimeError('Selected sink has no output-device owner')
    if requested.get('card') and requested['card']!=owner['name'] and explicit:
        raise RuntimeError('Selected output belongs to a different device')
    return {'card':owner['name'],'profile':str(owner['activeProfile']),
            'sink':selected['name'],'port':str(requested.get('port') or ''),
            'label':selected['label'],'sinkCard':owner['name'],
            'sinkProfile':str(owner['activeProfile'])}

def activate_output(request,mode='laptop_laptop',password=None,origin='web',token=None):
    if not isinstance(request,dict):raise ValueError('Invalid output request')
    card=str(request.get('card') or '');profile=request.get('profile');route=request.get('port')
    if not card or profile is None or not request.get('sink'):
        raise ValueError('Select an exposed playback device, profile and sink')
    with LOCK, (audio_start_change() if STOPPED.exists() else media_change()):
        preview=_require_output_preview(origin,token)
        if STOPPED.exists():
            ensure_pipewire_ready()
        before=audio_topology();original=_find_card(before,card)
        old_profile=original['activeProfile'];old_routes=list(original['activeRoutes'])
        previous_output=saved_output_route();previous_mode=audio_mode()
        try:
            (STATE/'output-preview').touch()
            set_output_profile(card,profile,topology=before)
            if route not in (None,''):
                set_output_route(card,route)
            else:
                current=_find_card(audio_topology(),card)
                valid=[r for r in current['routes'] if r['index'] in current['activeRoutes']
                       and (not r['profiles'] or current['activeProfile'] in r['profiles'])
                       and r['available'] not in ('no','false','unavailable') and r['name'].strip().lower() not in ('off','[out] off')]
                if current['routes']:
                    if len(valid)!=1:
                        raise RuntimeError('The last known route is unavailable for this profile; select an exposed route')
                    route=valid[0]['index']
            target=str(request.get('sink') or '')
            deadline=time.monotonic()+5
            while True:
                owner=_find_card(audio_topology(),card)
                candidates=list(owner['sinks'])
                if target and any(row['name']==target for row in candidates):
                    choice=_route_choice(owner,route,owner['activeProfile']) if owner['routes'] else {'devices':[]}
                    devices={str(v) for v in choice.get('devices',[])}
                    row=next(row for row in candidates if row['name']==target)
                    if not devices or row.get('profileDevice') is None or str(row['profileDevice']) in devices:break
                    raise RuntimeError('Selected sink does not belong to the chosen output route')
                if not target and candidates:
                    chosen=None
                    if route not in (None,'') and owner['routes']:
                        choice=_route_choice(owner,route,owner['activeProfile'])
                        devices={str(v) for v in choice.get('devices',[])}
                        if devices:
                            nodes=pw_objects('Node',pw_graph())
                            matched=[row for row in candidates if any(
                                pw_props(node).get('node.name')==row['name'] and
                                str(pw_props(node).get('card.profile.device','')) in devices
                                for node in nodes)]
                            if len(matched)==1:chosen=matched[0]
                            if matched:candidates=matched
                            elif all(row.get('profileDevice') is not None for row in candidates):
                                raise RuntimeError('Chosen route has no matching playback sink')
                    if chosen is None:
                        remembered=remembered_output(card,str(owner['activeProfile'])).get('sink')
                        chosen=next((row for row in candidates if row['name']==remembered),None)
                    if chosen is None:
                        try:default=pw_default()
                        except RuntimeError:default=''
                        chosen=next((row for row in candidates if row['name']==default),None)
                    if chosen is None and candidates and not request.get('sink'):
                        nodes={pw_props(node).get('node.name'):pw_props(node)
                               for node in pw_objects('Node',pw_graph())}
                        def rank(row):
                            try:return int(nodes.get(row['name'],{}).get('priority.session') or 0)
                            except (ValueError,TypeError):return 0
                        chosen=max(candidates,key=rank)
                    if chosen is not None:target=chosen['name'];break
                if time.monotonic()>=deadline:
                    raise RuntimeError('Selected route did not expose one unambiguous playback sink')
                time.sleep(.1)
            selected=choose_output_route({'card':card,'sink':target,'port':str(route or '')})
            result=apply_mode(mode,password=password,output=selected,output_resolved=True)
            # A live playback link can still be silent when the chosen sink is
            # muted. Unmute only the explicitly selected sink, at commit time.
            chosen_node=pw_sink(target)
            mute=run([exe('wpctl'),'get-volume',str(chosen_node['id'])],False,8,media_env())
            if mute.returncode:raise RuntimeError(mute.stderr.strip() or 'Could not inspect selected sink mute')
            if re.search(r'\[MUTED\]',mute.stdout,re.I):
                unmute=run([exe('wpctl'),'set-mute',str(chosen_node['id']),'0'],False,8,media_env())
                if unmute.returncode:raise RuntimeError(unmute.stderr.strip() or 'Could not unmute selected sink')
                check=run([exe('wpctl'),'get-volume',str(chosen_node['id'])],False,8,media_env())
                if check.returncode or re.search(r'\[MUTED\]',check.stdout,re.I):
                    raise RuntimeError('Selected sink remained muted after output switch')
            if not STOPPED.exists() and MODES[mode][2]:
                actual=saved_output_route()
                if actual.get('sink')!=selected['sink'] or not alive(rpid(LOCALMONPID)):
                    raise RuntimeError('Output switch was not confirmed by the local playback monitor')
            (STATE/'output-preview').unlink(missing_ok=True)
            OUTPUT_PREVIEW.unlink(missing_ok=True)
            return result
        except Exception as failure:
            (STATE/'output-preview').unlink(missing_ok=True)
            rollback=[]
            try:
                current=_find_card(audio_topology(),card)
                if old_profile is not None and current['activeProfile']!=old_profile:
                    set_output_profile(card,old_profile)
                restore_exact_device_routes(card,old_routes,'output')
                if previous_output.get('sink') and MODES[previous_mode][2] and not STOPPED.exists():
                    restored=choose_output_route(dict(previous_output,_remembered=True))
                    if selected_filter()==NO_FILTER and previous_mode=='laptop_laptop':direct_no_filter(restored)
                    start_local_monitor(restored,resolved=True)
                    MODE.write_text(previous_mode+'\n')
                    normalize_with_media()
            except Exception as error:rollback.append(str(error))
            if rollback:
                raise RuntimeError(str(failure)+'; previous output could not be fully restored: '+'; '.join(rollback)) from failure
            raise
OUTPUT_CHOICES=STATE/'output-choices.json'
CARD_CHOICES=STATE/'output-card-profiles.json'
def remembered_card_profile(card):
    saved=_read_json(CARD_CHOICES,{})
    return str(saved.get(card) or '') if isinstance(saved,dict) else ''

def remembered_output(card,profile):
    choices=_read_json(OUTPUT_CHOICES,{})
    if not isinstance(choices,dict):return {}
    entry=choices.get(card+'\n'+profile,{})
    return entry if isinstance(entry,dict) else {}
def remember_output(route):
    choices=_read_json(OUTPUT_CHOICES,{})
    if not isinstance(choices,dict):choices={}
    choices[route['card']+'\n'+route['profile']]={key:route.get(key,'') for key in ('card','profile','sink','port')}
    _write_json(OUTPUT_CHOICES,choices)
    card_profiles=_read_json(CARD_CHOICES,{})
    if not isinstance(card_profiles,dict):card_profiles={}
    card_profiles[route['card']]=route['profile']
    _write_json(CARD_CHOICES,card_profiles)
def output_state():
    topology=audio_topology();topology['selected']=topology['saved'].copy()
    choices=_read_json(OUTPUT_CHOICES,{})
    topology['remembered']=choices if isinstance(choices,dict) else {}
    topology['rememberedProfiles']=_read_json(CARD_CHOICES,{})
    return topology
def start_local_monitor(route=None,resolved=False,persist=True):
    selected=route if resolved else choose_output_route(route)
    sink=selected['sink'];pw_sink(sink)
    stop_local_monitor()
    command='set -o pipefail; "$1" -q -D camilladsp_output_shared -r 96000 -f S32_LE -c 2 -t raw | "$2" --playback --device="$3" --rate=96000 --format=s32le --channels=2'
    args=[exe('bash'),'-c',command,'monitor',exe('arecord'),exe('pacat'),sink]
    with (STATE/'local-monitor.log').open('ab',buffering=0) as log:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=media_env())
    LOCALMONPID.write_text(str(process.pid)+'\n')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if process.poll() is not None:break
        graph=pw_graph()
        streams=[x for x in pw_objects('Node',graph) if pw_props(x).get('media.class')=='Stream/Output/Audio'
                 and str(pw_props(x).get('application.process.id') or '').isdigit()]
        for stream in streams:
            pid=int(pw_props(stream)['application.process.id'])
            try:
                if os.getpgid(pid)!=process.pid:continue
            except OSError:continue
            links=[x for x in pw_objects('Link',graph) if str((x.get('info') or {}).get('output-node-id'))==str(stream['id'])]
            target=pw_sink(sink,graph)
            if any(str((x.get('info') or {}).get('input-node-id'))==str(target['id']) for x in links):
                if persist:
                    _write_json(LOCALSINK,selected);remember_output(selected)
                return {'pid':process.pid,**selected}
        time.sleep(.1)
    stop_local_monitor()
    raise RuntimeError('Local playback stream not linked to '+sink+'; check '+str(STATE/'local-monitor.log'))
