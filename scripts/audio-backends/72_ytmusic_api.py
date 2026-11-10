import random
import os
import re
import shutil
import subprocess
import threading
import time
import secrets
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

YTMUSICAPI_REPO=HOME/'ytmusicapi'
YTMUSIC_AUTH=HOME/'.config/ytmusicapi/browser.json'
YTMUSIC_LOCK=threading.RLock()
YTMUSIC_CURRENT={'videoId':'','title':'','url':'','reported':0.0,'quickPickToken':'','liked':False,'disliked':False,'ratingKnown':False}
YTMUSIC_QUICK_PICK_TOKEN=''
YTMUSIC_LIKED_CACHE={'at':0.0,'ids':set()}
YTMUSIC_GLIDE_COMMANDS=[]
YTMUSIC_GLIDE_INFLIGHT={}
YTMUSIC_GLIDE_LAUNCHER=SCRIPTS/'audio-backends/ytmusic-extension/glide-with-ytmusic-temp-extension'
YTMUSIC_GLIDE_LAUNCHED_AT=0.0
YTMUSIC_EXTENSION_READY_AT=0.0
YTMUSIC_RATING_BY_VIDEO={}

def _glide_running():
    # Glide's process name can be wrapped by Nix, so /proc cmdline is more
    # reliable than pgrep -x glide. Match only this user's executable argv[0].
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:
            if entry.stat().st_uid!=os.getuid():continue
            argv0=(entry/'cmdline').read_bytes().split(b'\0',1)[0]
            name=Path(os.fsdecode(argv0)).name.casefold()
            if name=='glide' or name.startswith('glide-') or name.endswith('-glide'):
                return True
        except (OSError,ValueError):continue
    return False

def _queue_glide_command(kind,**payload):
    command={'id':secrets.token_urlsafe(18),'type':kind,**payload}
    with YTMUSIC_LOCK:
        YTMUSIC_GLIDE_COMMANDS.append(command)
        del YTMUSIC_GLIDE_COMMANDS[:-32]
    return command

def ytmusic_extension_ready():
    global YTMUSIC_EXTENSION_READY_AT
    with YTMUSIC_LOCK:YTMUSIC_EXTENSION_READY_AT=time.monotonic()
    return {'ready':True}

def ytmusic_next_glide_command():
    with YTMUSIC_LOCK:
        if time.monotonic()-YTMUSIC_EXTENSION_READY_AT>5:return {'command':None}
        if not YTMUSIC_GLIDE_COMMANDS:return {'command':None}
        command=YTMUSIC_GLIDE_COMMANDS.pop(0)
        YTMUSIC_GLIDE_INFLIGHT[command['id']]=command
        if len(YTMUSIC_GLIDE_INFLIGHT)>32:
            del YTMUSIC_GLIDE_INFLIGHT[next(iter(YTMUSIC_GLIDE_INFLIGHT))]
        return {'command':command}

def ytmusic_ack_glide_command(command_id):
    command_id=str(command_id or '')
    with YTMUSIC_LOCK:
        if command_id not in YTMUSIC_GLIDE_INFLIGHT:raise ValueError('Unknown Glide command')
        YTMUSIC_GLIDE_INFLIGHT.pop(command_id,None)
    return {'acknowledged':True}

def _open_required_music_url(url):
    global YTMUSIC_GLIDE_LAUNCHED_AT
    parsed=urlparse(str(url or ''))
    if parsed.scheme!='https' or parsed.netloc!='music.youtube.com':
        raise ValueError('Only YouTube Music URLs can be opened through Glide')
    command=_queue_glide_command('open',url=url)
    if _glide_running() or time.monotonic()-YTMUSIC_GLIDE_LAUNCHED_AT<10:return command
    if not YTMUSIC_GLIDE_LAUNCHER.is_file():
        with YTMUSIC_LOCK:YTMUSIC_GLIDE_COMMANDS.remove(command)
        raise RuntimeError('Glide YouTube Music launcher is unavailable')
    subprocess.Popen([str(YTMUSIC_GLIDE_LAUNCHER)],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    YTMUSIC_GLIDE_LAUNCHED_AT=time.monotonic()
    return {'launched':True,'command':command}

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
        same=YTMUSIC_CURRENT.get('videoId')==video_id
        cached=YTMUSIC_RATING_BY_VIDEO.get(video_id)
        liked=cached=='LIKE' or (bool(YTMUSIC_CURRENT.get('liked')) if same else False)
        disliked=cached=='DISLIKE' or (bool(YTMUSIC_CURRENT.get('disliked')) if same else False)
        rating_known=cached in ('LIKE','DISLIKE','INDIFFERENT') or (bool(YTMUSIC_CURRENT.get('ratingKnown')) if same else False)
        if not rating_known:
            try:
                song=_auth_call(_ytmusic().get_song,video_id)
                rating=str((song or {}).get('likeStatus') or '').upper()
                disliked=rating=='DISLIKE'
                liked=rating=='LIKE' or video_id in _cached_liked_ids()
                if liked:disliked=False
                rating_known=rating in ('LIKE','DISLIKE') or liked
                if rating_known:YTMUSIC_RATING_BY_VIDEO[video_id]='LIKE' if liked else 'DISLIKE'
            except Exception:
                pass
        YTMUSIC_CURRENT.update(videoId=video_id,title=title,url=url,reported=time.time(),quickPickToken=token,liked=liked,disliked=disliked,ratingKnown=rating_known)
        return {'videoId':video_id,'title':title,'liked':liked,'disliked':disliked,'reported':True}

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
    try:_open_required_music_url('https://music.youtube.com/?media_control_refresh_cookie=1')
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

def _open_ytmusic_track(video_id,title='',playlist='',replace_music_tabs=False):
    global YTMUSIC_QUICK_PICK_TOKEN
    video_id=_valid_video_id(video_id)
    YTMUSIC_QUICK_PICK_TOKEN=secrets.token_urlsafe(18)
    query=['v='+video_id]
    if playlist:query.append('list='+str(playlist))
    query.extend(('mcquickpick='+YTMUSIC_QUICK_PICK_TOKEN,'mcautoplay=1'));
    if replace_music_tabs:query.append('mcreplacemusictabs=1')
    url='https://music.youtube.com/watch?'+'&'.join(query)
    _open_required_music_url(url)
    return {'videoId':video_id,'title':str(title or video_id)[:300],'url':url}

def ytmusic_play_item(video_id,title=''):
    with YTMUSIC_LOCK:return _open_ytmusic_track(video_id,title,replace_music_tabs=True)

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
        result=_open_ytmusic_track(seed['videoId'],seed.get('title') or '',playlist or '',replace_music_tabs=True)
        result['artist']=_artists(seed)
        return result

def ytmusic_current_status():
    with YTMUSIC_LOCK:
        current=dict(YTMUSIC_CURRENT)
        media=system_state()
        fresh=bool(current.get('videoId')) and time.time()-float(current.get('reported') or 0)<30.0
        reported_title=_media_title(current.get('title'))
        media_title=_media_title(media.get('title'))
        reported_words=set(reported_title.split());media_words=set(media_title.split())
        overlap=len(reported_words & media_words)/max(1,min(len(reported_words),len(media_words)))
        media_video_id=''
        try:
            media_url=urlparse(str(media.get('url') or ''))
            if media_url.netloc=='music.youtube.com':media_video_id=dict(parse_qsl(media_url.query)).get('v','')
        except (TypeError,ValueError):pass
        exact_video=bool(media_video_id and media_video_id==current.get('videoId'))
        same_title=bool(reported_title and media_title) and (reported_title in media_title or media_title in reported_title or overlap>=0.5)
        active=media.get('status') in ('Playing','Paused')
        available=bool(fresh and media.get('available') and active and (exact_video or same_title))
        if not available:
            return {'available':False,'liked':False,'disliked':False,'title':'','reason':'Rating state unavailable for current system media'}
        cached=YTMUSIC_RATING_BY_VIDEO.get(current['videoId'])
        if cached in ('LIKE','DISLIKE','INDIFFERENT'):
            current['liked']=cached=='LIKE';current['disliked']=cached=='DISLIKE';current['ratingKnown']=True
            YTMUSIC_CURRENT.update(liked=current['liked'],disliked=current['disliked'],ratingKnown=True)
        elif not current.get('ratingKnown'):
            try:
                song=_auth_call(_ytmusic().get_song,current['videoId'])
                rating=str((song or {}).get('likeStatus') or '').upper()
                if rating in ('LIKE','DISLIKE'):
                    YTMUSIC_RATING_BY_VIDEO[current['videoId']]=rating
                    current['liked']=rating=='LIKE';current['disliked']=rating=='DISLIKE';current['ratingKnown']=True
                    YTMUSIC_CURRENT.update(liked=current['liked'],disliked=current['disliked'],ratingKnown=True)
                elif current['videoId'] in _cached_liked_ids():
                    YTMUSIC_RATING_BY_VIDEO[current['videoId']]='LIKE'
                    current['liked']=True;current['disliked']=False;current['ratingKnown']=True
                    YTMUSIC_CURRENT.update(liked=True,disliked=False,ratingKnown=True)
                # Empty likeStatus is eventually consistent and is not authoritative.
            except Exception:
                pass
        return {'available':True,'videoId':current['videoId'],'title':current.get('title') or current['videoId'],'liked':bool(current.get('liked')),'disliked':bool(current.get('disliked'))}

def _rating_response_text(response):
    for action in (response or {}).get('actions',[]):
        renderer=((action.get('addToToastAction') or {}).get('item') or {}).get('notificationActionRenderer') or {}
        runs=(renderer.get('responseText') or {}).get('runs') or []
        text=''.join(str(run.get('text') or '') for run in runs).strip()
        if text:return text
    return ''

def ytmusic_rate_current(rating):
    rating=str(rating or '').upper()
    with YTMUSIC_LOCK:
        status=ytmusic_current_status()
        if not status.get('available'):raise RuntimeError(status.get('reason') or 'Like state unavailable')
        video_id=status['videoId'];was_liked=bool(status.get('liked'));was_disliked=bool(status.get('disliked'))
        if rating=='TOGGLE':rating='INDIFFERENT' if was_liked else 'LIKE'
        elif rating=='DISLIKE_TOGGLE':rating='INDIFFERENT' if was_disliked else 'DISLIKE'
        if rating not in ('LIKE','DISLIKE','INDIFFERENT'):raise ValueError('Current-track rating accepts LIKE, DISLIKE, INDIFFERENT, TOGGLE, or DISLIKE_TOGGLE')
        expected=rating=='LIKE';disliked=rating=='DISLIKE'
        response=_auth_call(_ytmusic().rate_song,video_id,_like_status(rating))
        response_text=_rating_response_text(response)
        # The live API returns a responseContext/action immediately while library
        # reads are eventually consistent. Accept only a real API response, then
        # pin local state until the normal cache window expires.
        if not isinstance(response,dict) or not response.get('responseContext'):
            raise RuntimeError('YouTube Music did not acknowledge the rating request')
        YTMUSIC_RATING_BY_VIDEO[video_id]=rating
        YTMUSIC_CURRENT.update(liked=expected,disliked=disliked,ratingKnown=True,ratingChangedAt=time.time())
        if expected:YTMUSIC_LIKED_CACHE['ids'].add(video_id)
        else:YTMUSIC_LIKED_CACHE['ids'].discard(video_id)
        YTMUSIC_LIKED_CACHE['at']=time.time()
        result={'videoId':video_id,'title':status.get('title') or video_id,'rating':rating,'liked':expected,'disliked':disliked,'confirmed':True,'source':'ytmusicapi','message':response_text,'skipped':False}
        if rating=='DISLIKE':
            system_media('next')
            result['skipped']=True
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
        for video_id in unique:
            _auth_call(yt.rate_song,video_id,_like_status('INDIFFERENT'))
            YTMUSIC_RATING_BY_VIDEO[video_id]='INDIFFERENT'
            YTMUSIC_LIKED_CACHE['ids'].discard(video_id)
        YTMUSIC_LIKED_CACHE['at']=time.time()
        if YTMUSIC_CURRENT.get('videoId') in seen:YTMUSIC_CURRENT.update(liked=False,disliked=False,ratingKnown=True)
    return {'removed':len(unique)}
