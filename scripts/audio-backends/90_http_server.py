class H(BaseHTTPRequestHandler):
    def data(self,n,t,b):
        try:
            self.send_response(n);self.send_header('Content-Type',t);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','private, max-age=3600' if n==200 and t=='image/jpeg' else 'no-store');self.end_headers();self.wfile.write(b)
        except (BrokenPipeError,ConnectionResetError):
            self.close_connection=True
    def out(self,n,d):self.data(n,'application/json; charset=utf-8',json.dumps(d,separators=(',',':')).encode())
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n<0 or n>65536:raise ValueError('Request body must be 64 KiB or less')
        d=json.loads(self.rfile.read(n)) if n else {}
        if not isinstance(d,dict):raise ValueError('Body must be object')
        return d
    def do_GET(self):
        u=urlparse(self.path);p=u.path;q=parse_qs(u.query)
        try:
            if p=='/':self.data(200,'text/html; charset=utf-8',PAGE.encode());return
            if p=='/api/system-cover':
                image=system_cover_bytes(q.get('v',[''])[0])
                if not image:self.data(404,'text/plain; charset=utf-8',b'No cover');return
                self.data(200,'image/jpeg',image);return
            if p=='/api/cover':
                try:track=song(q.get('path',[''])[0])
                except (ValueError,OSError):self.data(404,'text/plain; charset=utf-8',b'Not found');return
                image=cover_bytes(track)
                if not image:self.data(404,'text/plain; charset=utf-8',b'No cover');return
                self.data(200,'image/jpeg',image);return
            if p=='/api/state':r=full_state()
            elif p=='/api/volatile':r=volatile_state()
            elif p=='/api/profiles':r={'profiles':[NO_FILTER]+[x.name for x in profiles()],'filters':listening_filters(),'active':active(),'running':alive(rpid(CAMPID),'camilladsp')}
            elif p=='/api/mode':r=mode_state()
            elif p=='/api/away-display':r=away_display_state()
            elif p=='/api/outputs':r=output_state()
            elif p=='/api/system-media':r=system_state()
            elif p=='/api/player':r=player_state()
            elif p=='/api/playlists':r={'playlists':lists()}
            elif p=='/api/songs':r={'songs':song_objects(files())}
            elif p=='/api/playlist':n=q.get('name',[''])[0];r={'name':n,'songs':song_objects(files(pdir(n)))}
            else:self.out(404,{'ok':False,'error':'Not found'});return
            self.out(200,{'ok':True,**r})
        except Exception as e:self.out(500,{'ok':False,'error':str(e)})
    def do_POST(self):
        p=urlparse(self.path).path
        try:
            d=self.body()
            if p=='/api/output-preview-begin':r=begin_output_preview()
            elif p=='/api/output-preview-renew':r=renew_output_preview(d.get('token'))
            elif p=='/api/output-preview-cancel':r=cancel_output_preview(token=d.get('token'))
            elif p=='/api/output-device-stage':r=select_output_device_stage(d.get('card'),origin=('cli' if d.get('origin')=='cli' and self.client_address[0] in ('127.0.0.1','::1') else 'web'),token=d.get('token'))
            elif p=='/api/output-profile-stage':r=apply_output_profile_stage(d.get('card'),d.get('profile'),d.get('mode'),origin=('cli' if d.get('origin')=='cli' and self.client_address[0] in ('127.0.0.1','::1') else 'web'),token=d.get('token'))
            elif p=='/api/output-route-stage':r=apply_output_route_stage(d.get('card'),d.get('profile'),d.get('route'),d.get('mode'),origin=('cli' if d.get('origin')=='cli' and self.client_address[0] in ('127.0.0.1','::1') else 'web'),token=d.get('token'))
            elif p=='/api/output-activate':r=activate_output(d.get('output'),mode=d.get('mode'),password=d.get('password'),origin=('cli' if d.get('origin')=='cli' and self.client_address[0] in ('127.0.0.1','::1') else 'web'),token=d.get('token'))
            elif p=='/api/away-display':r=toggle_away_display()
            elif p=='/api/mode':r=set_mode(d.get('mode'),d.get('password'),d.get('output'))
            elif p=='/api/groups/save':r=save_group(d)
            elif p=='/api/select':r=switch_profile(d.get('profile'))
            elif p=='/api/audio-toggle':r=toggle_audio_services()
            elif p=='/api/ensure-audio-baseline':r=ensure_physical_audio_baseline()
            elif p=='/api/restart-sonobus':r=restart_sonobus_action(d.get('password'))
            elif p=='/api/restart-airplay':r=restart_airplay()
            elif p=='/api/restart-vnc':r=restart_vnc()
            elif p=='/api/system-media':r=system_media(d.get('command'))
            elif p=='/api/system-media/seek':r=system_seek(d.get('seconds'))
            elif p=='/api/system-volume':r=set_system_volume(d.get('volume'))
            elif p=='/api/mode/pc':r={'player':'mpv','mode':mode_state()}
            elif p=='/api/mode/system':r=set_mode('laptop_external',d.get('password'))
            elif p=='/api/mode/airplay':r=set_mode('ipad_external',d.get('password'))
            elif p=='/api/player/command':
                c=d.get('command');cmd={'toggle':['cycle','pause'],'next':['playlist-next','force'],'previous':['playlist-prev','force']}.get(c)
                if not cmd:raise ValueError('Invalid command')
                mpv(cmd);save_mpv_queue(force=True);r={'command':c}
            elif p=='/api/player/shuffle':r=shuffle_current_playlist()
            elif p=='/api/player/seek':v=max(0,float(d.get('seconds')));mpv(['seek',v,'absolute+exact']);save_mpv_queue(force=True);r={'seconds':v}
            elif p=='/api/player/volume':r=set_master_volume(d.get('volume'))
            elif p=='/api/player/repeat':r={'mode':set_repeat(d.get('mode'))};save_mpv_queue(force=True)
            elif p=='/api/play/song':r=play_song(d.get('path'))
            elif p=='/api/play/playlist':r=play_list(d.get('name'),bool(d.get('shuffle')))
            else:self.out(404,{'ok':False,'error':'Not found'});return
            self.out(200,{'ok':True,**r})
        except (ValueError,TypeError,KeyError,FileNotFoundError,json.JSONDecodeError) as e:self.out(400,{'ok':False,'error':str(e)})
        except Exception as e:self.out(500,{'ok':False,'error':str(e)})
    def log_message(self,*a):pass
class S(ThreadingHTTPServer):allow_reuse_address=True;daemon_threads=True

