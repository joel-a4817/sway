#!/usr/bin/env python3
import fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'sway/windows'
ROOT.mkdir(parents=True,exist_ok=True); DB=ROOT/'float-windows.json'; LOCK=ROOT/'float-windows.lock'
def tree():return json.loads(subprocess.check_output(['swaymsg','-r','-t','get_tree'],text=True))
def walk(n):
 if isinstance(n,dict):
  if n.get('type') in ('con','floating_con') and (n.get('app_id') or n.get('window')):yield n
  for k in ('nodes','floating_nodes'):
   for c in n.get(k,[]):yield from walk(c)
def focused():return next((n for n in walk(tree()) if n.get('focused')),None)
def load():
 try:
  x=json.loads(DB.read_text());return x if isinstance(x,dict) else {}
 except Exception:return {}
def save(x):
 q=DB.with_suffix('.tmp');q.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');os.chmod(q,0o600);os.replace(q,DB)
def geom(n):
 r=n.get('rect') or {};g={k:int(r.get(k,0)) for k in ('x','y','width','height')}
 return g if 100<=g['width']<=16384 and 80<=g['height']<=16384 else None
def sway(cid,text):return subprocess.run(['swaymsg','--',f'[con_id={cid}] {text}'],check=True,stdout=subprocess.DEVNULL)
def toggle():
 n=focused()
 if not n:return
 cid=str(n['id']); floating=n.get('floating')=='user_on'
 with LOCK.open('a+') as lk:
  fcntl.flock(lk,fcntl.LOCK_EX); state=load(); rec=state.setdefault(cid,{})
  if floating:
   g=geom(n)
   if g:rec['geometry']=g
   rec['floating']=False;save(state);sway(cid,'floating disable')
  else:
   rec['floating']=True;save(state);sway(cid,'floating enable');time.sleep(.06)
   g=rec.get('geometry')
   if g:sway(cid,f"resize set width {g['width']} px height {g['height']} px, move position {g['x']} px {g['y']} px")
def replace_previous():
 me=os.getpid()
 for proc in Path('/proc').glob('[0-9]*'):
  try:
   pid=int(proc.name); cmd=(proc/'cmdline').read_bytes().replace(b'\0',b' ')
   if pid!=me and b'float-state-daemon.py' in cmd and b'--toggle' not in cmd:os.kill(pid,signal.SIGTERM)
  except (OSError,ValueError):pass
 time.sleep(.1)
def daemon():
 replace_previous();state=load();last={};dirty=False;lastsave=0.0
 while True:
  try:nodes=list(walk(tree()))
  except Exception:time.sleep(.2);continue
  present=set()
  for n in nodes:
   cid=str(n['id']);present.add(cid);floating=n.get('floating')=='user_on';g=geom(n);rec=state.setdefault(cid,{})
   # Observe only. Never force floating/tiled state. This keeps the key binding a real toggle.
   if rec.get('floating')!=floating:rec['floating']=floating;dirty=True
   if floating and not n.get('fullscreen_mode') and g and rec.get('geometry')!=g:rec['geometry']=g;dirty=True
   rec['app_id']=n.get('app_id');rec['title']=n.get('name');last[cid]=time.time()
  # Keep closed-window records briefly for compositor refresh/reparent events, then prune.
  cutoff=time.time()-300
  for cid in list(state):
   if cid not in present and last.get(cid,0)<cutoff:state.pop(cid,None);dirty=True
  now=time.monotonic()
  if dirty and now-lastsave>=.12:
   with LOCK.open('a+') as lk:fcntl.flock(lk,fcntl.LOCK_EX);save(state)
   dirty=False;lastsave=now
  time.sleep(.08)
if __name__=='__main__':
 if '--toggle' in sys.argv:toggle()
 else:daemon()
