import random
import re
import shutil
import subprocess
import threading
import time
import secrets

YTMUSICAPI_REPO=HOME/'ytmusicapi'
YTMUSIC_AUTH=HOME/'.config/ytmusicapi/browser.json'
YTMUSIC_LOCK=threading.RLock()
YTMUSIC_CURRENT={'videoId':'','title':'','url':'','reported':0.0,'quickPickToken':'','liked':False}
YTMUSIC_QUICK_PICK_TOKEN=''
YTMUSIC_LIKED_CACHE={'at':0.0,'ids':set()}
YTMUSIC_GLIDE_LAUNCHER=HOME/'.config/sway/scripts/audio-backends/ytmusic-extension/glide-with-ytmusic-temp-extension'

def _glide_running():
    result=run([exe('pgrep'),'-x','glide'],False,5,media_env())
    return result.returncode==0 and any(row.isdecimal() for row in result.stdout.split())

def _open_ytmusic_url(url):
    url=str(url or '')
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.netloc!='music.youtube.com':
        raise ValueError('Only YouTube Music URLs can be opened through the Glide bridge')
    if _glide_running():
        opener=shutil.which('xdg-open')
        if not opener:raise RuntimeError('xdg-open is unavailable')
        opened=subprocess.run([opener,url],text=True,capture_output=True,timeout=15,env=media_env())
        if opened.returncode:raise RuntimeError(opened.stderr.strip() or 'Could not open YouTube Music')
        return {'startedGlide':False}
    if not YTMUSIC_GLIDE_LAUNCHER.is_file() or not os.access(YTMUSIC_GLIDE_LAUNCHER,os.X_OK):
        raise RuntimeError('YouTube Music Glide launcher is missing or not executable')
    with (STATE/'ytmusic-glide-launch.log').open('ab',buffering=0) as log:
        process=subprocess.Popen([str(YTMUSIC_GLIDE_LAUNCHER),url],stdin=subprocess.DEVNULL,
                                 stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=media_env())
    deadline=time.monotonic()+2.0
    while time.monotonic()<deadline:
        code=process.poll()
        if code is not None:
            raise RuntimeError('Glide extension launcher exited with status '+str(code)+'; check '+str(STATE/'ytmusic-glide-launch.log'))
        if _glide_running():return {'startedGlide':True}
        time.sleep(.05)
    if process.poll() is not None:
        raise RuntimeError('Glide extension launcher exited before Glide started; check '+str(STATE/'ytmusic-glide-launch.log'))
    return {'startedGlide':True,'pending':True}

def _ytmusic():
    import sys
    if str(YTMUSICAPI_REPO) not in sys.path:sys.path.insert(0,str(YTMUSICAPI_REPO))
    if not YTMUSIC_AUTH.is_file():raise RuntimeError('Refresh YouTube Music cookie')
    from ytmusicapi import YTMusic
    return YTMusic(str(YTMUSIC_AUTH))

def _like_status(name):
    from ytmusicapi import LikeStatus
    return getattr(LikeStatus,name)

def _artists(item):
    return ', '.join(row.get('name','') for row in item.get('artists') or [] if row.get('name'))

def _valid_video_id(value):
    value=str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{6,32}',value):raise ValueError('Invalid YouTube Music video ID')
    return value

def ytmusic_report_current(data):
    if not isinstance(data,dict):raise ValueError('Current-track report must be an object')
    video_id=_valid_video_id(data.get('videoId'))
    url=str(data.get('url') or '')[:2048]
    if 'music.youtube.com/' not in url:raise ValueError('Current track must come from YouTube Music')
    title=str(data.get('title') or '')[:300]
    token=str(data.get('quickPickToken') or '')
    with YTMUSIC_LOCK:
        liked=video_id in _cached_liked_ids()
        YTMUSIC_CURRENT.update(videoId=video_id,title=title,url=url,reported=time.time(),quickPickToken=token,liked=liked)
        return {'videoId':video_id,'title':title,'liked':liked,'reported':True}

def _current_track():
    row=dict(YTMUSIC_CURRENT)
    if not row.get('videoId'):
        raise RuntimeError('Glide has not reported the current YouTube Music track. Reload the YouTube Music tab and retry.')
    return row

def _auth_call(call,*args,**kwargs):
    try:return call(*args,**kwargs)
    except Exception as error:
        text=str(error).lower()
        if any(token in text for token in ('401','403','unauthorized','forbidden','authentication','authorization','cookie','visitor data')):
            raise RuntimeError('Refresh YouTube Music cookie') from None
        raise

def _liked_ids(yt,limit=5000):
    data=_auth_call(yt.get_liked_songs,limit=limit)
    if not isinstance(data,dict) or not isinstance(data.get('tracks'),list):
        raise RuntimeError('YouTube Music did not return the authenticated Liked Music tracks collection')
    return {row.get('videoId') for row in data['tracks'] if row.get('videoId')},data['tracks']

def _cached_liked_ids(max_age=15.0):
    now=time.time()
    if now-YTMUSIC_LIKED_CACHE['at']>=max_age:
        ids,_=_liked_ids(_ytmusic())
        YTMUSIC_LIKED_CACHE.update(at=now,ids=ids)
    return YTMUSIC_LIKED_CACHE['ids']

def _media_title(value):
    value=re.sub(r'\s*-\s*YouTube Music\s*$','',str(value or ''),flags=re.I)
    return re.sub(r'[^a-z0-9]+',' ',value.casefold()).strip()

def _rate(video_id,title,rating,previous=None):
    yt=_ytmusic();video_id=_valid_video_id(video_id)
    if previous is None:
        liked,_=_liked_ids(yt)
        previous='LIKE' if video_id in liked else 'INDIFFERENT'
    _auth_call(yt.rate_song,video_id,_like_status(rating))
    return {'videoId':video_id,'title':title,'rating':rating}


YTMUSIC_REFRESH={'pending':False,'started':0.0,'status':'idle','error':''}

def ytmusic_begin_cookie_refresh():
    with YTMUSIC_LOCK:
        YTMUSIC_REFRESH.update(pending=True,started=time.time(),status='waiting',error='')
    try:_open_ytmusic_url('https://music.youtube.com/?media_control_refresh_cookie=1')
    except Exception as error:
        with YTMUSIC_LOCK:YTMUSIC_REFRESH.update(pending=False,status='failed',error=str(error))
        raise
    return {'status':'waiting'}

def ytmusic_refresh_cookie_status():
    with YTMUSIC_LOCK:
        if YTMUSIC_REFRESH['pending'] and time.time()-YTMUSIC_REFRESH['started']>120:
            YTMUSIC_REFRESH.update(pending=False,status='failed',error='No signed-in YouTube Music request was captured')
        return dict(YTMUSIC_REFRESH)

def ytmusic_refresh_cookie(headers_raw):
    with YTMUSIC_LOCK:
        if not YTMUSIC_REFRESH['pending']:
            raise RuntimeError('Cookie refresh was not requested from Media Control')
        headers_raw=str(headers_raw or '').strip()
        if len(headers_raw)<40 or len(headers_raw)>65536 or not re.search(r'(?im)^cookie\s*:',headers_raw):
            raise ValueError('Captured request headers do not contain a usable Cookie header')
        import sys
        if str(YTMUSICAPI_REPO) not in sys.path:sys.path.insert(0,str(YTMUSICAPI_REPO))
        from ytmusicapi import YTMusic,setup
        YTMUSIC_AUTH.parent.mkdir(parents=True,exist_ok=True)
        temporary=YTMUSIC_AUTH.with_name('.'+YTMUSIC_AUTH.name+'.new')
        temporary.unlink(missing_ok=True)
        try:
            setup(filepath=str(temporary),headers_raw=headers_raw)
            home=YTMusic(str(temporary)).get_home(limit=1)
            if not isinstance(home,list):raise RuntimeError('YouTube Music rejected the captured cookie')
            temporary.chmod(0o600);temporary.replace(YTMUSIC_AUTH);YTMUSIC_AUTH.chmod(0o600)
            YTMUSIC_REFRESH.update(pending=False,status='saved',error='')
        except BaseException as error:
            temporary.unlink(missing_ok=True)
            YTMUSIC_REFRESH.update(pending=False,status='failed',error=str(error))
            raise
        return {'status':'saved'}

def _open_ytmusic_track(video_id,title='',playlist=''):
    global YTMUSIC_QUICK_PICK_TOKEN
    video_id=_valid_video_id(video_id)
    YTMUSIC_QUICK_PICK_TOKEN=secrets.token_urlsafe(18)
    query=['v='+video_id]
    if playlist:query.append('list='+str(playlist))
    query.extend(('mcquickpick='+YTMUSIC_QUICK_PICK_TOKEN,'mcautoplay=1'))
    url='https://music.youtube.com/watch?'+'&'.join(query)
    _open_ytmusic_url(url)
    return {'videoId':video_id,'title':str(title or video_id)[:300],'url':url}

def ytmusic_play_item(video_id,title=''):
    with YTMUSIC_LOCK:return _open_ytmusic_track(video_id,title)

def ytmusic_quick_picks():
    global YTMUSIC_QUICK_PICK_TOKEN
    with YTMUSIC_LOCK:
        yt=_ytmusic();home=yt.get_home(limit=30)
        shelf=next((row for row in home if 'quick picks' in str(row.get('title','')).casefold()),None)
        if not shelf:raise RuntimeError('Quick Picks was not returned by YouTube Music')
        choices=[row for row in shelf.get('contents',[]) if row.get('videoId')]
        if not choices:raise RuntimeError('Quick Picks contained no playable tracks')
        seed=random.choice(choices)
        watch=yt.get_watch_playlist(videoId=seed['videoId'],limit=100,radio=True)
        playlist=watch.get('playlistId')
        result=_open_ytmusic_track(seed['videoId'],seed.get('title') or '',playlist or '')
        result['artist']=_artists(seed)
        return result

def ytmusic_current_status():
    with YTMUSIC_LOCK:
        current=dict(YTMUSIC_CURRENT)
        media=system_state()
        fresh=bool(current.get('videoId')) and time.time()-float(current.get('reported') or 0)<6.0
        reported_title=_media_title(current.get('title'))
        media_title=_media_title(media.get('title'))
        same_title=fresh and bool(reported_title and media_title) and (reported_title in media_title or media_title in reported_title)
        available=bool(media.get('available') and same_title)
        if not available:
            return {'available':False,'liked':False,'title':'','reason':'Like state unavailable for current system media'}
        return {'available':True,'videoId':current['videoId'],'title':current.get('title') or current['videoId'],'liked':bool(current.get('liked'))}

def ytmusic_rate_current(rating):
    rating=str(rating or '').upper()
    with YTMUSIC_LOCK:
        status=ytmusic_current_status()
        if not status.get('available'):raise RuntimeError(status.get('reason') or 'Like state unavailable')
        current=_current_track();yt=_ytmusic();liked,_=_liked_ids(yt)
        was_liked=current['videoId'] in liked
        if rating=='TOGGLE':rating='INDIFFERENT' if was_liked else 'LIKE'
        if rating not in ('LIKE','INDIFFERENT'):raise ValueError('Current-track toggle accepts LIKE, INDIFFERENT, or TOGGLE')
        previous='LIKE' if was_liked else 'INDIFFERENT'
        result=_rate(current['videoId'],current.get('title') or current['videoId'],rating,previous=previous)
        result['liked']=rating=='LIKE'
        YTMUSIC_CURRENT['liked']=result['liked']
        if result['liked']:YTMUSIC_LIKED_CACHE['ids'].add(current['videoId'])
        else:YTMUSIC_LIKED_CACHE['ids'].discard(current['videoId'])
        return result

def ytmusic_liked():
    with YTMUSIC_LOCK:
        _,rows=_liked_ids(_ytmusic())
        return {'songs':[{'videoId':row.get('videoId',''),'title':row.get('title',''),'artist':_artists(row)} for row in rows if row.get('videoId')]}

def ytmusic_rate_item(video_id,title,rating):
    rating=str(rating or '').upper()
    if rating not in ('LIKE','DISLIKE','INDIFFERENT'):raise ValueError('Invalid YouTube Music rating')
    with YTMUSIC_LOCK:return _rate(video_id,str(title or video_id)[:300],rating,previous='LIKE')

def ytmusic_remove_items(items):
    if not isinstance(items,list) or not items:raise ValueError('Select at least one liked song')
    unique=[];seen=set()
    for item in items:
        video_id=_valid_video_id((item or {}).get('videoId'))
        if video_id not in seen:seen.add(video_id);unique.append(video_id)
    with YTMUSIC_LOCK:
        yt=_ytmusic()
        for video_id in unique:_auth_call(yt.rate_song,video_id,_like_status('INDIFFERENT'))
        if YTMUSIC_CURRENT.get('videoId') in seen:YTMUSIC_CURRENT['liked']=False
    return {'removed':len(unique)}

# One-shot bridge from the temporary extension to Glide's privileged config.
YTMUSIC_CLOSE_QUEUE=[]
def ytmusic_enqueue_tab_close(tab_id):
    try:tab_id=int(tab_id)
    except (TypeError,ValueError):raise RuntimeError('Invalid YouTube Music tab ID')
    with YTMUSIC_LOCK:
        if tab_id not in YTMUSIC_CLOSE_QUEUE:
            YTMUSIC_CLOSE_QUEUE.append(tab_id)
            del YTMUSIC_CLOSE_QUEUE[:-32]
    return {'ok':True,'queued':tab_id}

def ytmusic_next_tab_close():
    with YTMUSIC_LOCK:
        tab_id=YTMUSIC_CLOSE_QUEUE.pop(0) if YTMUSIC_CLOSE_QUEUE else None
    return {'ok':True,'tabId':tab_id}
