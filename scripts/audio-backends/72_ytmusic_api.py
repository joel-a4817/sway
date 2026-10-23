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
        if not YTMUSIC_QUICK_PICK_TOKEN or token!=YTMUSIC_QUICK_PICK_TOKEN:
            return {'ignored':True}
        liked=video_id in _liked_ids(_ytmusic())[0]
        YTMUSIC_CURRENT.update(videoId=video_id,title=title,url=url,reported=time.time(),quickPickToken=token,liked=liked)
        return {'videoId':video_id,'title':title,'liked':liked}

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
    opener=shutil.which('xdg-open')
    if not opener:raise RuntimeError('xdg-open is unavailable')
    opened=subprocess.run([opener,'https://music.youtube.com/?media_control_refresh_cookie=1'],text=True,capture_output=True,timeout=15)
    if opened.returncode:
        with YTMUSIC_LOCK:YTMUSIC_REFRESH.update(pending=False,status='failed',error=opened.stderr.strip() or 'Could not open YouTube Music')
        raise RuntimeError(YTMUSIC_REFRESH['error'])
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
    opener=shutil.which('xdg-open')
    if not opener:raise RuntimeError('xdg-open is unavailable')
    opened=subprocess.run([opener,url],text=True,capture_output=True,timeout=15)
    if opened.returncode:raise RuntimeError(opened.stderr.strip() or 'Could not open YouTube Music')
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
        current=_current_track()
        return {'videoId':current['videoId'],'title':current.get('title') or current['videoId'],'liked':bool(current.get('liked'))}

def ytmusic_rate_current(rating):
    rating=str(rating or '').upper()
    with YTMUSIC_LOCK:
        current=_current_track();yt=_ytmusic();liked,_=_liked_ids(yt)
        was_liked=current['videoId'] in liked
        if rating=='TOGGLE':rating='INDIFFERENT' if was_liked else 'LIKE'
        if rating not in ('LIKE','INDIFFERENT'):raise ValueError('Current-track toggle accepts LIKE, INDIFFERENT, or TOGGLE')
        previous='LIKE' if was_liked else 'INDIFFERENT'
        result=_rate(current['videoId'],current.get('title') or current['videoId'],rating,previous=previous)
        result['liked']=rating=='LIKE'
        YTMUSIC_CURRENT['liked']=result['liked']
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
