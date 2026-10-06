def restore_saved_output_for_startup(route):
    route=dict(route or {})
    card=route.get('card');profile_value=route.get('profile')
    if not card or not profile_value:return route
    set_output_profile(card,profile_value)
    current=_find_card(audio_topology(),card)
    compatible=[row for row in current['routes'] if _available_option(row)
                and row['name'].strip().lower() not in ('off','[out] off')
                and (not row['profiles'] or str(current['activeProfile']) in {str(value) for value in row['profiles']})]
    requested=str(route.get('port') or '')
    selected=next((row for row in compatible if str(row['index'])==requested),None)
    if selected is None:
        remembered=remembered_output(card,str(current['activeProfile'])).get('port')
        selected=(next((row for row in compatible if str(row['index'])==str(remembered)),None)
                  or next((row for row in compatible if row['index'] in current['activeRoutes']),None)
                  or (compatible[0] if compatible else None))
    if current['routes'] and selected is None:
        raise RuntimeError('Saved playback profile exposes no available output route')
    if selected is not None and selected['index'] not in current['activeRoutes']:
        set_output_route(card,selected['index'])
        current=_find_card(audio_topology(),card)
    route['profile']=str(current['activeProfile'])
    route['port']=str(selected['index']) if selected is not None else ''
    devices={str(value) for value in selected['devices']} if selected is not None else set()
    sinks=[row for row in current['sinks'] if
           (not devices or row['profileDevice'] is None or str(row['profileDevice']) in devices)]
    if not sinks:raise RuntimeError('Saved playback profile and route expose no playback sink')
    saved_sink=str(route.get('sink') or '')
    chosen=next((row for row in sinks if row['name']==saved_sink),None)
    if chosen is None:
        remembered=remembered_output(card,route['profile']).get('sink')
        chosen=next((row for row in sinks if row['name']==remembered),None)
    if chosen is None:
        graph=pw_graph();priorities={pw_props(node).get('node.name'):pw_props(node).get('priority.session') for node in pw_objects('Node',graph)}
        def priority(row):
            try:return int(priorities.get(row['name']) or 0)
            except (TypeError,ValueError):return 0
        chosen=max(sinks,key=priority)
    route.update(sink=chosen['name'],label=chosen['label'],sinkCard=card,sinkProfile=route['profile'])
    return route

def start_runtime(force=False):
    global MASTER_RESTORING
    MASTER_RESTORING=True
    ensure()
    (STATE/'output-preview').unlink(missing_ok=True)
    ensure_pipewire_ready()
    if STOPPED.exists() and not force:
        if OUTPUT_PREVIEW.exists():_recover_stale_cli_preview()
        MODE.write_text('laptop_laptop\n')
        run(['systemctl','stop','shairport-sync.service','nqptp.service'],False,10)
        return
    if OUTPUT_PREVIEW.exists():_recover_stale_cli_preview()
    # Keep persisted master until the route has been restored; a newly
    # created default sink may temporarily report 100%.
    saved=NO_FILTER if force else selected_filter()
    selected=None
    if saved==NO_FILTER:
        ACTIVE.write_text(NO_FILTER+'\n')
    else:
        selected=profile(saved)
    # Set the startup mode before any bypass, engine or source decisions.
    mode='laptop_laptop'
    MODE.write_text(mode+'\n')
    # Reuse a verified surviving engine; never start a competing instance.
    if saved==NO_FILTER:
        # apply_mode resolves and applies the physical route before rebuilding
        # the bypass bridge. Do not open ALSA Loopback during startup discovery.
        stop_camilla_for_no_filter()
    elif not alive(rpid(CAMPID),'camilladsp'):
        stop_camilla(include_stale=True)
        start_camilla(selected)
    if selected_filter()!=NO_FILTER:
        wait_for_camilla_config(profile(selected_filter()))
    # Both the web picker and Media Control commit to LOCALSINK through
    # start_local_monitor(). Do not replace their last successful selection
    # with PipeWire's temporary default (often the virtual DSP sink).
    saved_route=saved_output_route()
    if saved_route['card'] and saved_route['profile']:
        try:saved_route=restore_saved_output_for_startup(saved_route)
        except (RuntimeError,ValueError,OSError) as error:
            print('Saved output pending: '+str(error),flush=True)
    # Resolve a physical route even when every state file was deleted.
    startup_output=_physical_output_route(saved_route if saved_route.get('card') else None)
    try:
        if saved==NO_FILTER and not saved_route.get('card'):
            # A clean state has one explicit baseline: No filter plus a live
            # physical output. Apply it before any virtual DSP sink can become
            # a persistent default merely because its service was exposed.
            startup_output=apply_physical_no_filter_fallback(startup_output)
        else:
            apply_mode(mode,restore_camilla=False,output=startup_output,output_resolved=True)
    except RuntimeError as error:
        # Every automatic routing recovery has one safe result: No filter,
        # laptop-to-laptop mode and a currently discovered physical output.
        # Do not retain or recreate the virtual CamillaDSP sink as a fallback.
        print('Audio routing fallback: '+str(error),flush=True)
        startup_output=apply_physical_no_filter_fallback(startup_output)
        mode='laptop_laptop'
    except ValueError as error:
        # A saved password-protected group cannot be joined unattended. Keep
        # CamillaDSP and the web UI alive so the password can be entered there.
        if 'requires a password' not in str(error):raise
        label,source,local,sono=MODES[mode]
        apply_source_services(source)
        if local:start_local_monitor()
        else:stop_local_monitor()
        stop_sonobus();MODE.write_text(mode+'\n')
    if MODES[audio_mode()][1]=='system' and QUEUE_FILE.is_file():
        try:ensure_mpv()
        except (OSError,RuntimeError):pass
    if selected_filter()!=NO_FILTER:
        wait_for_camilla_config(profile(selected_filter()))
    try:
        apply_master_volume(master_volume(),normalize_sinks=False)
    except RuntimeError as error:
        if not alive(rpid(LOCALMONPID)) and any(x in str(error) for x in ('No playback sink','Could not identify the current default sink','Playback sink unavailable','Audio node unavailable')):
            print('Saved master pending: '+str(error),flush=True)
        else:raise
    MASTER_RESTORING=False
def stop_audio_services(mark_stopped=True,stop_pipewire=False):
    global MASTER_RESTORING
    # Capture the last live value, then stop engines and clients while
    # preserving PipeWire and the NixOS-managed device graph.
    with LOCK:
        pause_for_audio_stop()
        if not MASTER_RESTORING and not STOPPED.exists():master_volume()
        MASTER_RESTORING=True
        if mark_stopped:STOPPED.touch()
        STOP_ACK.unlink(missing_ok=True)
        try:
            stop_local_monitor()
            stop_mpv()
            stop_sonobus()
            stop_bypass()
            stop_camilla(include_stale=True)
            airplay(False)
            if stop_pipewire:
                for unit in ('wireplumber.service','pipewire-pulse.service','pipewire.service',
                             'pipewire-pulse.socket','pipewire.socket'):
                    user_service('stop',unit)
        finally:
            if mark_stopped:STOP_ACK.touch()
def release_audio_for_output_switch():
    # Transfer ownership without stopping PipeWire or marking the whole system stopped.
    with LOCK:
        STOP_ACK.unlink(missing_ok=True)
        try:
            stop_local_monitor();stop_mpv();stop_sonobus();stop_bypass();stop_camilla(include_stale=True)
            airplay(False)
        except Exception as error:
            (STATE/'audio-start-error').write_text(str(error)+'\n')
        finally:STOP_ACK.touch()
def toggle_audio_services():
    with LOCK:
        if STOPPED.exists():
            with audio_start_change():
                start_runtime(force=True)
                STOPPED.unlink(missing_ok=True)
        else:
            stop_audio_services()
        return {'stopped':STOPPED.exists()}
def cleanup():
    # Host shutdown is not Stop all audio. Leave the local playback graph
    # available to Media Control after selecting the clean laptop mode.
    if STOPPED.exists():return
    with LOCK:
        pause_for_audio_stop()
        try:
            apply_mode('laptop_laptop')
            normalize_audio_volumes()
        except Exception as error:
            print('Could not restore laptop audio during host shutdown: '+str(error),flush=True)
def sonobus_sway_windows(tree):
    # The live Sway tree reports title="SonoBus", X11 class="SonoBus", app_id=null.
    stack=[(tree,None)]
    while stack:
        node,workspace=stack.pop()
        if not isinstance(node,dict):continue
        if node.get('type')=='workspace':workspace=node.get('name')
        for child in (node.get('nodes') or [])+(node.get('floating_nodes') or []):
            stack.append((child,workspace))
        if (node.get('type')!='con' or not isinstance(node.get('id'),int)
                or node.get('name')!='SonoBus'
                or (node.get('window_properties') or {}).get('class')!='SonoBus'):
            continue
        rect=node.get('rect') or {}
        if rect.get('width',0)>0 and rect.get('height',0)>0:
            yield node['id'],workspace

def watch_sonobus_workspace(stop):
    if not os.environ.get('SWAYSOCK'):return
    seen={}
    moved=set()
    while not stop.is_set():
        try:
            result=run([exe('swaymsg'),'-r','-t','get_tree'],False,4)
            if result.returncode:raise RuntimeError(result.stderr.strip() or 'Sway tree unavailable')
            windows=dict(sonobus_sway_windows(json.loads(result.stdout)))
            seen={wid:seen.get(wid,0)+1 for wid in windows}
            moved.intersection_update(windows)
            for wid,workspace in windows.items():
                if wid in moved or seen[wid]<2:continue
                if workspace=='1':moved.add(wid);continue
                reply=run([exe('swaymsg'),'-r',f'[con_id={wid}] move container to workspace number 1'],False,4)
                answers=json.loads(reply.stdout)
                if reply.returncode or not isinstance(answers,list) or not answers or not all(x.get('success') for x in answers):
                    raise RuntimeError(reply.stderr.strip() or f'Sway move failed: {answers}')
                moved.add(wid)
                print(f'Moved SonoBus window {wid} to workspace 1',flush=True)
        except (OSError,ValueError,TypeError,RuntimeError,subprocess.TimeoutExpired) as error:
            print(f'SonoBus workspace watcher: {error}',flush=True)
        stop.wait(.5)

def main():
    me=os.getpid()
    SWITCH_STATE.mkdir(parents=True,exist_ok=True)
    startup_lock=(SWITCH_STATE/'media-control.lock').open('a+')
    fcntl.flock(startup_lock,fcntl.LOCK_EX)
    pause_for_audio_stop()
    legacy_pid=HOME/'.local/state/sway/audio/camilladsp-webremote/web-server.pid'
    previous=rpid(legacy_pid)
    if previous and previous!=me and alive(previous):
        try:os.kill(previous,signal.SIGTERM)
        except OSError:pass
        deadline=time.monotonic()+10
        while alive(previous) and time.monotonic()<deadline:time.sleep(.05)
        if alive(previous):raise RuntimeError('Previous legacy webremote instance did not exit')
    ensure();old=rpid(SERVERPID)
    if old and old!=me and alive(old):
        try:os.kill(old,signal.SIGTERM)
        except OSError:pass
        deadline=time.monotonic()+10
        while alive(old) and time.monotonic()<deadline:time.sleep(.05)
        if alive(old):raise RuntimeError('Previous webremote instance did not complete audio shutdown; refusing unsafe replacement')
    SERVERPID.unlink(missing_ok=True)
    if alive(rpid(BYPASSPID)):stop_bypass()
    # First replacement of an older server may leave its tracked engine alive.
    tracked=rpid(CAMPID)
    if tracked and reusable_camilla(tracked):
        stop_local_monitor();stop_camilla(include_stale=True)
    # A surviving engine may be reused only if it is the tracked engine and
    # reports the selected YAML. Every active startup still reapplies local mode.
    running=other_camilla_processes()
    if running:
        if len(running)!=1:
            raise RuntimeError('Multiple CamillaDSP engines found; refusing unsafe startup')
        verified=reusable_camilla(running[0])
        if not verified or selected_filter()==NO_FILTER:
            raise RuntimeError('Unverified CamillaDSP engine is running; refusing unsafe startup')
        if rpid(CAMPID)!=running[0]:
            CAMPID.write_text(str(running[0])+'\n')
        if selected_filter()!=verified.name:
            raise RuntimeError('Surviving CamillaDSP config differs from selected profile; refusing unsafe startup')
    watcher_stop=threading.Event()
    threading.Thread(target=watch_sonobus_workspace,args=(watcher_stop,),daemon=True).start()
    start_runtime()
    if not STOPPED.exists():normalize_audio_volumes()
    threading.Thread(target=watch_master_volume,args=(watcher_stop,),daemon=True).start()
    threading.Thread(target=watch_output_preview,args=(watcher_stop,),daemon=True).start()
    server=S(('0.0.0.0',PORT),H);SERVERPID.write_text(str(me)+'\n');STOP_CAP.write_text(str(me)+'\n')
    fcntl.flock(startup_lock,fcntl.LOCK_UN)
    startup_lock.close()
    def shut(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shut);signal.signal(signal.SIGHUP,shut)
    signal.signal(signal.SIGUSR1,lambda *_:threading.Thread(target=release_audio_for_output_switch,daemon=True).start())
    print(f'CamillaDSP web remote listening on 0.0.0.0:{PORT}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        watcher_stop.set()
        server.server_close();cleanup()
        if rpid(SERVERPID)==me:SERVERPID.unlink(missing_ok=True)
        if rpid(STOP_CAP)==me:STOP_CAP.unlink(missing_ok=True)
def local_api_post(path,payload=None,timeout=90,error='Request failed'):
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError,URLError
    request=Request(f'http://127.0.0.1:{PORT}{path}',data=json.dumps(payload or {}).encode(),
                    headers={'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(request,timeout=timeout) as response:data=json.load(response)
    except HTTPError as failure:
        try:detail=json.load(failure).get('error')
        except (ValueError,TypeError):detail=None
        raise RuntimeError(detail or error) from failure
    except URLError as failure:raise RuntimeError('Webremote is unavailable: '+str(failure)) from failure
    if not data.get('ok'):raise RuntimeError(data.get('error') or error)
    return data

def topology_cli():
    import sys
    try:
        ensure()
        if len(sys.argv)==2 and sys.argv[1]=='--audio-toggle':
            print(json.dumps(toggle_audio_services()))
            return
        if len(sys.argv)==3 and sys.argv[1]=='--restart-service':
            name=sys.argv[2]
            paths={'1':'/api/restart-camilladsp','2':'/api/restart-sonobus',
                   '3':'/api/restart-airplay','4':'/api/restart-vnc'}
            if name not in paths:raise ValueError('Choose restart 1, 2, 3 or 4')
            if not alive(rpid(SERVERPID)):
                if name=='1':result=restart_camilla()
                elif name=='2':result=restart_sonobus_action()
                elif name=='3':result=restart_airplay()
                else:result=restart_vnc()
                print(json.dumps(result));return
            data=local_api_post(paths[name],timeout=40,error='Restart failed')
            print(json.dumps(data));return
        if len(sys.argv)==2 and sys.argv[1]=='--toggle-away-display':
            print(json.dumps(toggle_away_display()));return
        if len(sys.argv)==2 and sys.argv[1]=='--away-display-status':
            print(json.dumps(away_display_state()));return
        if len(sys.argv)==3 and sys.argv[1]=='--select-filter':
            requested=sys.argv[2]
            available={NO_FILTER,*[item.name for item in profiles()]}
            if requested not in available:raise ValueError('Selected filter is not available')
            if STOPPED.exists():
                ACTIVE.write_text(requested+'\n')
                data={'profile':requested,'stopped':True}
            else:
                current=selected_filter()
                # No filter is an idempotent repair operation: its state label
                # can survive while PipeWire still defaults to the virtual DSP
                # sink. Always rebuild the bypass and physical default.
                if requested==NO_FILTER or requested!=current:
                    switch_profile(requested)
                data={'profile':selected_filter(),'stopped':False}
            print(json.dumps(data,ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--laptop-laptop-boundary':
            ensure()
            # The preview transaction already owns the rollback snapshot. Mark
            # the intended local boundary without starting either engine here;
            # the first output stage acquires ALSA exactly once.
            MODE.write_text('laptop_laptop\n')
            print(json.dumps({'mode':'laptop_laptop','stopped':STOPPED.exists()}))
            return
        if len(sys.argv)==2 and sys.argv[1]=='--normalize-menu-open':
            if STOPPED.exists():
                print(json.dumps({'skipped':'audio stopped'}));return
            print(json.dumps(normalize_with_media()));return
        if len(sys.argv)==2 and sys.argv[1]=='--normalize-audio':
            print(json.dumps(normalize_with_media()));return
        if len(sys.argv)==3 and sys.argv[1]=='--profile-path':
            print(profile(sys.argv[2]));return
        if len(sys.argv)==2 and sys.argv[1]=='--dsp-profiles':
            print(json.dumps([NO_FILTER]+[item.name for item in profiles()],ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--dsp-filter-info':
            print(json.dumps(listening_filters(),ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--selected-filter':
            print(selected_filter());return
        if len(sys.argv)==3 and sys.argv[1]=='--remember-output':
            route=json.loads(sys.argv[2]);
            if not isinstance(route,dict) or not all(isinstance(route.get(key),str) and route[key] for key in ('card','profile','sink')):raise ValueError('Invalid output route')
            remember_output(route);print(json.dumps(route));return
        if len(sys.argv)==3 and sys.argv[1]=='--remembered-profile':
            print(remembered_card_profile(sys.argv[2]));return
        if len(sys.argv)==4 and sys.argv[1]=='--remembered-sink':
            route=remembered_output(sys.argv[2],sys.argv[3])
            print(route.get('sink',''))
            return
        if len(sys.argv)==5 and sys.argv[1]=='--remembered-port':
            route=remembered_output(sys.argv[2],sys.argv[3])
            print(route.get('port','') if route.get('sink')==sys.argv[4] else '')
            return
        if len(sys.argv)==3 and sys.argv[1]=='--sink-label':
            name=sys.argv[2];top=audio_topology();live=next((row for card in top['cards'] for row in card['sinks'] if row['name']==name),None)
            saved=_read_json(LOCALSINK,{})
            print((live or {}).get('label') or (saved.get('label') if isinstance(saved,dict) and saved.get('sink')==name else '') or name);return
        if len(sys.argv)==3 and sys.argv[1]=='--switch-output':
            request=json.loads(sys.argv[2])
            if not isinstance(request,dict) or not request.get('card') or request.get('profile') is None:
                raise ValueError('Choose a playback device and profile')
            server=rpid(SERVERPID)
            if not alive(server) or server==os.getpid():
                if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before selecting an output')
                print(json.dumps(activate_output(request,mode='laptop_laptop',origin='cli',token=os.environ.get('MEDIA_CONTROL_PICKER_PID')),ensure_ascii=False))
                return
            data=local_api_post('/api/output-activate',{'output':request,'mode':'laptop_laptop','origin':'cli','token':os.environ.get('MEDIA_CONTROL_PICKER_PID')},120,'Output switch failed')
            print(json.dumps(data,ensure_ascii=False))
            return
        if len(sys.argv)==4 and sys.argv[1]=='--output-route':
            raise ValueError('Choose profile and route together with --switch-output')
        if len(sys.argv)==3 and sys.argv[1]=='--apply-output-device':
            card=sys.argv[2]
            if not alive(rpid(SERVERPID)):
                print(json.dumps(select_output_device_stage(card,origin='cli',token=os.environ.get('MEDIA_CONTROL_PICKER_PID')),ensure_ascii=False));return
            data=local_api_post('/api/output-device-stage',{'card':card,'origin':'cli','token':os.environ.get('MEDIA_CONTROL_PICKER_PID')},90,'Playback device stage failed')
            print(json.dumps(data,ensure_ascii=False));return
        if (len(sys.argv)==4 and sys.argv[1]=='--apply-output-profile') or (len(sys.argv)==5 and sys.argv[1]=='--apply-output-route'):
            if not alive(rpid(SERVERPID)):
                if STOPPED.exists():raise RuntimeError('Audio is stopped; start audio before selecting an output')
                result=(apply_output_profile_stage(sys.argv[2],sys.argv[3],mode='laptop_laptop',origin='cli',token=os.environ.get('MEDIA_CONTROL_PICKER_PID'))
                        if sys.argv[1]=='--apply-output-profile' else
                        apply_output_route_stage(sys.argv[2],sys.argv[3],sys.argv[4],mode='laptop_laptop',origin='cli',token=os.environ.get('MEDIA_CONTROL_PICKER_PID')))
                print(json.dumps(result,ensure_ascii=False));return
            is_profile=sys.argv[1]=='--apply-output-profile'
            endpoint='/api/output-profile-stage' if is_profile else '/api/output-route-stage'
            payload={'card':sys.argv[2],'profile':sys.argv[3],'mode':'laptop_laptop','origin':'cli','token':os.environ.get('MEDIA_CONTROL_PICKER_PID')}
            if not is_profile:payload['route']=sys.argv[4]
            data=local_api_post(endpoint,payload,90,'Output stage failed')
            print(json.dumps(data,ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--begin-output-preview':
            print(json.dumps(begin_output_preview('cli',int(os.environ['MEDIA_CONTROL_PICKER_PID']),int(os.environ['MEDIA_CONTROL_PICKER_START'])),ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--cancel-output-preview':
            print(json.dumps(cancel_output_preview('cli',token=os.environ.get('MEDIA_CONTROL_PICKER_PID')),ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--interactive-input':
            try:
                result=select_input_interactive(finalize=lambda:normalize_audio_volumes() if not STOPPED.exists() else None)
                print(json.dumps(result,ensure_ascii=False));return
            finally:pause_for_normalization()
        if len(sys.argv)==3 and sys.argv[1]=='--camera-restore-clients':
            print(json.dumps(restore_camera_clients(json.loads(sys.argv[2])),ensure_ascii=False));return
        if len(sys.argv)==3 and sys.argv[1]=='--camera-selection-status':
            print(json.dumps(camera_selection_status(int(sys.argv[2])),ensure_ascii=False));return
        if len(sys.argv)==2 and sys.argv[1]=='--output-topology':
            print(json.dumps(audio_topology(),ensure_ascii=False));return
        if len(sys.argv)==4 and sys.argv[1]=='--output-profile':
            raise ValueError('Choose profile and route together with --switch-output')
        if len(sys.argv)==3 and sys.argv[1]=='--resolve-output':
            print(json.dumps(choose_output_route(json.loads(sys.argv[2])),ensure_ascii=False));return
        raise ValueError('Invalid topology command')
    except KeyboardInterrupt:
        print('Selection cancelled',file=sys.stderr);sys.exit(130)
    except Exception as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
