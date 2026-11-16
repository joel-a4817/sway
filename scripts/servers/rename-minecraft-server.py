#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, fcntl, json, os, re, socket, subprocess, sys, uuid
from pathlib import Path
H=Path.home(); STATE=Path(os.environ.get('MINECRAFT_STATE_ROOT',H/'minecraft-server')); BACK=Path(os.environ.get('MINECRAFT_BACKUP_ROOT',H/'minecraft-backups')); RUN=Path(os.environ.get('MINECRAFT_RUNTIME_ROOT','/srv/minecraft')); LOCAL=H/'.local/state/minecraft-server'; COORD=STATE/'coordination'; NIX_CANDIDATES=sorted((H/'nixos').glob('*/minecraft.nix')); NIX=NIX_CANDIDATES[0] if len(NIX_CANDIDATES)==1 else H/'nixos/rt4817/minecraft.nix'; MANAGER=H/'.config/sway/scripts/servers/minecraft-server.py'; LOCK=LOCAL/'rename.lock'
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def cmd(a,check=True): print('$',' '.join(map(str,a)),flush=True); return subprocess.run(list(map(str,a)),check=check,text=True)
def readj(p,default):
 try:return json.loads(p.read_text())
 except:return default
def writej(p,d): p.parent.mkdir(parents=True,exist_ok=True); q=p.with_suffix(p.suffix+'.tmp'); q.write_text(json.dumps(d,indent=2,sort_keys=True)+'\n'); os.replace(q,p)
def declared():return re.findall(r'\bname\s*=\s*"([a-z0-9][a-z0-9-]*)"\s*;',NIX.read_text())
def replace_decl(old,new):
 t=NIX.read_text(); pat=re.compile(r'(\bname\s*=\s*")'+re.escape(old)+r'("\s*;)')
 n,c=pat.subn(r'\g<1>'+new+r'\2',t,count=1)
 if c!=1: raise RuntimeError(f'Could not replace exactly one server declaration {old}')
 # Update every remaining literal occurrence in minecraft.nix. Generated unit,
 # runtime and policy names already derive from server.name; this catches human-
 # readable/static labels such as minecraft-port-forwarding.txt.
 n=n.replace(old,new)
 q=NIX.with_suffix(NIX.suffix+'.tmp');q.write_text(n);os.replace(q,NIX)
def stop(old): cmd([sys.executable,'-B',str(MANAGER),'stop',old])
def host():return socket.gethostname().split('.',1)[0]
def move_plan(old,new):
 return [(STATE/'servers'/old,STATE/'servers'/new,'synchronized snapshot'),(RUN/old,RUN/new,'runtime'),(STATE/'generations'/old,STATE/'generations'/new,'generations'),(BACK/host()/old,BACK/host()/new,'peer backup directory')]
def local_file_moves(old,new):
 out=[]
 for base in (LOCAL,COORD):
  if base.is_dir():
   for p in sorted(base.glob(old+'.*')):out.append((p,p.with_name(new+p.name[len(old):]),'state'))
 return out
def transaction(old,new,tid):return LOCAL/'rename-transactions'/tid/'transaction.json'
def ensure_intent(old,new,tid):
 t=NIX.read_text(); marker=f"# minecraft-rename-intent: {tid} {old} {new}"
 if marker not in t:
  pat=re.compile(r'(\bname\s*=\s*"'+re.escape(new)+r'"\s*;)')
  t,c=pat.subn(r'\1\n        '+marker,t,count=1)
  if c!=1:raise RuntimeError('Could not attach rename intent to minecraft.nix')
  NIX.write_text(t)
def intents():
 return [dict(id=i,old=o,new=n,state='pending') for i,o,n in re.findall(r'# minecraft-rename-intent:\s+([0-9a-f-]+)\s+([a-z0-9-]+)\s+([a-z0-9-]+)',NIX.read_text())]
def remove_intent(tid):
 t=NIX.read_text();NIX.write_text(re.sub(r'^[ \t]*# minecraft-rename-intent:\s+'+re.escape(tid)+r'.*\n?','',t,flags=re.M))
def update_exact_json(path,old,new):
 d=readj(path,None)
 if d is None:return
 def w(x):
  if isinstance(x,dict):return {k:w(v) for k,v in x.items()}
  if isinstance(x,list):return [w(v) for v in x]
  return new if x==old else x
 writej(path,w(d))

def same_path(a,b):
 try:return a.resolve()==b.resolve()
 except:return os.path.abspath(a)==os.path.abspath(b)
def merge_path(src,dst,label):
 """Idempotently move or merge src into dst without overwriting different data."""
 if same_path(src,dst) or not src.exists():return False
 dst.parent.mkdir(parents=True,exist_ok=True)
 if not dst.exists():os.rename(src,dst);return True
 if src.is_dir() and dst.is_dir():
  for child in list(src.iterdir()):merge_path(child,dst/child,label)
  try:src.rmdir()
  except OSError:pass
  return True
 if src.is_file() and dst.is_file() and src.read_bytes()==dst.read_bytes():src.unlink();return True
 raise RuntimeError(f'Conflicting old/new {label}; refusing to overwrite: {src} / {dst}')
def known_old_names(new):
 out=set()
 aliases=readj(STATE/'rename-aliases.json',{'aliases':{}}).get('aliases',{})
 out.update(aliases.get(new,[]))
 for r in intents():
  if r['new']==new:out.add(r['old'])
 root=LOCAL/'rename-transactions'
 if root.is_dir():
  for f in root.glob('*/transaction.json'):
   t=readj(f,{})
   if t.get('new')==new and t.get('old'):out.add(t['old'])
 return sorted(x for x in out if x and x!=new)
def repair_name(new,interactive=True):
 olds=known_old_names(new); pending=[]
 for old in olds:
  pending.extend((src,dst,label,old) for src,dst,label in move_plan(old,new)+local_file_moves(old,new) if src.exists() and not same_path(src,dst))
 if not pending:return False
 print(f'Incomplete Minecraft rename data found for {new}:')
 for src,dst,label,old in pending:print(f'  {label}: {src} -> {dst}')
 if interactive and input("Complete this peer's unfinished rename now? [Y/n]: ").strip().lower() not in ('','y','yes'):return False
 sync=subprocess.run(['systemctl','--user','is-active','--quiet','syncthing.service']).returncode==0
 try:
  if sync:cmd(['systemctl','--user','stop','syncthing.service'])
  for src,dst,label,old in pending:
   merge_path(src,dst,label)
   if dst.is_dir():
    for jf in dst.rglob('*.json'):update_exact_json(jf,old,new)
   elif dst.suffix=='.json':update_exact_json(dst,old,new)
 finally:
  if sync:cmd(['systemctl','--user','start','syncthing.service'],False)
 print(f'Completed unfinished local rename data for {new}.')
 return True
def repair_all(interactive=True):
 changed=False
 for name in declared():changed=repair_name(name,interactive) or changed
 return changed

def apply(old,new,tid,edit_nix=False):
 LOCAL.mkdir(parents=True,exist_ok=True)
 with LOCK.open('a+') as f:
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  txp=transaction(old,new,tid); tx=readj(txp,{'schema':2,'id':tid,'old':old,'new':new,'peer':host(),'phases':{},'moves':[]})
  def phase(k):tx['phases'][k]=True;writej(txp,tx)
  # The initiating peer must cleanly stop before changing the declaration,
  # otherwise the lifecycle manager can no longer resolve the old service.
  if not tx['phases'].get('stopped'):
   if edit_nix:
    stop(old)
   else:
    # The origin peer already performed the cluster-wide clean stop. Refuse if
    # this peer somehow still has the old unit active rather than stopping it
    # outside the lifecycle manager.
    if subprocess.run(['systemctl','is-active','--quiet',f'minecraft-server-{old}.service']).returncode==0:
     raise RuntimeError(f'{old} is still active locally; stop it before adopting the propagated rename')
   phase('stopped')
  if edit_nix and not tx['phases'].get('intent'):
   if old not in declared():raise RuntimeError(f'{old} not declared in minecraft.nix')
   replace_decl(old,new);ensure_intent(old,new,tid);phase('intent')
  sync=subprocess.run(['systemctl','--user','is-active','--quiet','syncthing.service']).returncode==0
  try:
   if sync:cmd(['systemctl','--user','stop','syncthing.service'])
   for src,dst,label in move_plan(old,new)+local_file_moves(old,new):
    key='move:'+str(dst)
    if tx['phases'].get(key):continue
    if same_path(src,dst):phase(key);continue
    if src.exists():
     merge_path(src,dst,label);tx['moves'].append({'from':str(src),'to':str(dst),'label':label})
    elif not dst.exists() and label in ('synchronized snapshot','runtime'):raise RuntimeError(f'Neither old nor new {label} exists')
    if dst.is_dir():
     for jf in dst.rglob('*.json'):update_exact_json(jf,old,new)
    elif dst.suffix=='.json':update_exact_json(dst,old,new)
    phase(key)
   aliases=readj(STATE/'rename-aliases.json',{'schema':1,'aliases':{}});aliases['aliases'][new]=sorted(set(aliases['aliases'].get(new,[])+[old]));writej(STATE/'rename-aliases.json',aliases);phase('aliases')
   tx['complete']=True;tx['rebuild_required']=True;writej(txp,tx)
  finally:
   if sync:cmd(['systemctl','--user','start','syncthing.service'],False)
  writej(LOCAL/'accepted-server-names.json',{'schema':1,'names':declared(),'updated_at':now()}); print(f"Rename {old} -> {new} is complete on {host()}. Rebuild this peer manually after all peers adopt it.")
def pending():return intents()
def local_names():
 out=set()
 for base in (STATE/'servers',STATE/'generations',RUN,BACK/host()):
  if base.is_dir():
   out.update(p.name for p in base.iterdir() if p.is_dir() and re.fullmatch(r'[a-z0-9][a-z0-9-]*',p.name))
 return out
def synthesize_missing_intent():
 if intents():return
 configured=set(declared()); existing=local_names(); missing=sorted(existing-configured); added=sorted(configured-existing)
 if len(missing)==1 and len(added)==1 and missing[0]!=added[0]:
  old,new=missing[0],added[0]; tid=str(uuid.uuid4()); ensure_intent(old,new,tid)
  print(f'Detected minecraft.nix server-name change: {old} -> {new}.')
def reconcile(interactive=True):
 # Invalid self-renames can only be stale metadata from an interrupted/older
 # implementation. Remove them silently; recovery is derived from real old/new paths.
 for r in list(intents()):
  if r['old']==r['new']:remove_intent(r['id'])
 synthesize_missing_intent()
 for r in pending():
  old,new,tid=r['old'],r['new'],r['id'];tx=readj(transaction(old,new,tid),{})
  if tx.get('complete'):continue
  ans=input(f'Adopt Minecraft rename {old} -> {new} on {host()}? [y/N]: ').strip().lower() if interactive else 'y'
  if ans in ('y','yes'):apply(old,new,tid,False)
  else:
   if new in declared() and old not in declared():replace_decl(new,old)
   tx.update({'complete':True,'rejected':True,'rejected_at':now(),'host':host()});writej(transaction(old,new,tid),tx);writej(LOCAL/'accepted-server-names.json',{'schema':1,'names':declared(),'updated_at':now()});print('Rename rejected locally; minecraft.nix was reverted. The shared intent is retained so other peers can make their own decision. Commit/push the revert if this is the cluster decision.')
 repair_all(interactive)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('old',nargs='?');ap.add_argument('new',nargs='?');ap.add_argument('--apply',action='store_true');ap.add_argument('--reconcile',action='store_true');a=ap.parse_args()
 if a.reconcile:return reconcile()
 if not a.old:
  names=declared();print('Select server to rename:');[print(f' [{i}] {n}') for i,n in enumerate(names,1)];i=int(input('Server: '));a.old=names[i-1];a.new=input('New name: ').strip()
 if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',a.new or ''):raise SystemExit('Invalid new name')
 if a.old==a.new:
  # Same name means no rename. Do not create an intent, transaction, prompt,
  # warning, or repair action.
  return
 tid=str(uuid.uuid4());print(f'Plan: stop {a.old}; declare {a.new}; rename snapshot, runtime, generations, local backup directory and all local state; preserve archive bytes and record alias metadata.')
 if not a.apply and input('Type RENAME to apply: ').strip()!='RENAME':return
 apply(a.old,a.new,tid,True)
if __name__=='__main__':
 try:main()
 except Exception as e:raise SystemExit(f'ERROR: {e}')
