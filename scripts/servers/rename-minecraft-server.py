#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, fcntl, json, os, re, subprocess, sys, uuid, urllib.request, urllib.parse, xml.etree.ElementTree as ET
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
 n=re.sub(r'(?<![a-z0-9-])'+re.escape(old)+r'(?![a-z0-9-])',new,n)
 q=NIX.with_suffix(NIX.suffix+'.tmp');q.write_text(n);os.replace(q,NIX)
def host():
 r=subprocess.run(['tailscale','status','--json'],text=True,capture_output=True,timeout=10)
 if r.returncode!=0:raise RuntimeError('Cannot determine Tailscale MagicDNS identity: '+(r.stderr.strip() or f'exit {r.returncode}'))
 data=json.loads(r.stdout or '{}');dns=((data.get('Self') or {}).get('DNSName') or '').rstrip('.')
 if not dns:raise RuntimeError('This device has no Tailscale MagicDNS identity')
 return dns.split('.',1)[0]
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
def syncthing_folders():
 cfg=H/'.local/state/syncthing/config.xml'
 if not cfg.exists():return []
 root=ET.parse(cfg).getroot();key=root.findtext('gui/apikey') or ''
 out=[]
 for f in root.findall('folder'):
  raw=os.path.expandvars(os.path.expanduser(f.get('path','')))
  if not raw:continue
  out.append({'id':f.get('id'),'path':Path(raw).resolve(),'type':(f.get('type') or 'sendreceive').lower(),'paused':f.get('paused','false').lower()=='true','key':key})
 return out
def folder_for(path):
 p=path.resolve();matches=[]
 for f in syncthing_folders():
  try:p.relative_to(f['path']);matches.append(f)
  except ValueError:pass
 return max(matches,key=lambda f:len(f['path'].parts)) if matches else None
def peer_owned_backup(path):
 try:Path(path).resolve().relative_to((BACK/host()).resolve());return True
 except ValueError:return False
def writable_folder(path):
 f=folder_for(path)
 # MagicDNS peer ownership is authoritative for ~/minecraft-backups/<peer>.
 # The matching peer edits its own subtree; all other peer subtrees are excluded.
 return f if peer_owned_backup(path) or not (f and f['type']=='receiveonly') else None
def path_writable(path):
 if peer_owned_backup(path):return True
 f=folder_for(path)
 return not (f and f['type']=='receiveonly')
def set_folder_paused(folder,paused):
 url='http://127.0.0.1:8384/rest/config/folders/'+urllib.parse.quote(folder['id'],safe='')
 data=json.dumps({'paused':bool(paused)}).encode();headers={'X-API-Key':folder['key'],'Content-Type':'application/json'}
 urllib.request.urlopen(urllib.request.Request(url,data=data,method='PATCH',headers=headers),timeout=30).read()
def pause_affected():
 unique={}
 for path in (STATE,BACK/host()):
  f=writable_folder(path)
  if f:unique[f['id']]=f
 changed=[]
 try:
  for f in unique.values():
   if not f['paused']:set_folder_paused(f,True);changed.append(f)
  return changed
 except Exception:
  for f in reversed(changed):
   try:set_folder_paused(f,False)
   except Exception:pass
  raise
def resume_folders(folders):
 errors=[]
 for f in reversed(folders):
  try:set_folder_paused(f,False)
  except Exception as e:errors.append(f'{f["id"]}: {e}')
 if errors:raise RuntimeError('Rename completed but failed to resume Syncthing folder(s): '+'; '.join(errors))
def managed_roots():
 # Complete rename scope. The shared backup roots belonging to other peers are
 # deliberately excluded: each peer may modify only its own backup subtree.
 return [p for p in (H/'nixos',H/'.config/sway',STATE,RUN,BACK/host(),LOCAL) if path_writable(p)]
def token_pattern(old):return re.compile(r'(?<![A-Za-z0-9-])'+re.escape(old)+r'(?![A-Za-z0-9-])')
def text_rewrite(p,old,new):
 try:data=p.read_bytes()
 except (OSError,PermissionError):return False
 if b'\0' in data:return False
 try:text=data.decode('utf-8')
 except UnicodeDecodeError:return False
 changed,n=token_pattern(old).subn(new,text)
 if not n:return False
 st=p.stat();q=p.with_name(p.name+'.minecraft-rename-tmp')
 q.write_bytes(changed.encode('utf-8'));os.chmod(q,st.st_mode & 0o7777);os.replace(q,p)
 return True
def exhaustive_plan(old,new):
 files=[];paths=[]
 for root in managed_roots():
  if not root.exists():continue
  for base,dirs,names in os.walk(root,topdown=True,followlinks=False):
   bp=Path(base);dirs[:]=[d for d in dirs if d not in ('.git','__pycache__')]
   for name in names:
    p=bp/name
    if not p.is_symlink() and p.is_file():files.append(p)
   for name in dirs:
    p=bp/name
    if old in name:paths.append(p)
   for name in names:
    p=bp/name
    if old in name:paths.append(p)
 return files,sorted(set(paths),key=lambda p:len(p.parts),reverse=True)
def exhaustive_replace(old,new):
 if old==new:return False
 files,paths=exhaustive_plan(old,new);changed=False
 # Rewrite contents before renaming their paths. Binary/archive bytes are never
 # modified; all UTF-8 text is processed atomically with exact token boundaries.
 for p in files:
  if p.exists() and text_rewrite(p,old,new):changed=True
 for src in paths:
  if not src.exists() or src.is_symlink():continue
  dst=src.with_name(src.name.replace(old,new))
  if same_path(src,dst):continue
  merge_path(src,dst,'managed path');changed=True
 return changed
def stale_owned_names():
 configured=set(declared());out=set()
 # Peer-owned backup subtree is authoritative for that peer. Also inspect local
 # runtime and generations, but never another peer's backup subtree.
 for base in (BACK/host(),RUN,STATE/'generations'):
  if base.is_dir() and path_writable(base):
   out.update(p.name for p in base.iterdir() if p.is_dir() and re.fullmatch(r'[a-z0-9][a-z0-9-]*',p.name) and p.name not in configured)
 return sorted(out)
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
def repair_name(new,interactive=True,announce=True,extra_olds=None):
 olds=sorted(set(known_old_names(new)+(extra_olds or [])));pending=[]
 for old in olds:
  pending.extend((src,dst,label,old) for src,dst,label in move_plan(old,new)+local_file_moves(old,new) if src.exists() and not same_path(src,dst) and path_writable(src) and path_writable(dst))
 if not pending and not olds:return False
 if announce and pending:
  print(f'Incomplete Minecraft rename data found for {new}:')
  for src,dst,label,old in pending:print(f'  {label}: {src} -> {dst}')
 if interactive and pending and input("Complete this peer's unfinished rename now? [Y/n]: ").strip().lower() not in ('','y','yes'):return False
 # Always pass through the manager. If running, this performs its verified stop
 # generation/backup and handover publication before any path mutation.
 cmd([sys.executable,'-B',str(MANAGER),'stop',new])
 paused=pause_affected();changed=False
 try:
  for src,dst,label,old in pending:
   merge_path(src,dst,label);changed=True
   if dst.is_dir():
    for jf in dst.rglob('*.json'):update_exact_json(jf,old,new)
   elif dst.suffix=='.json':update_exact_json(dst,old,new)
  for old in olds:changed=exhaustive_replace(old,new) or changed
 finally:resume_folders(paused)
 if announce and changed:print(f'Completed unfinished local rename data for {new}.')
 return changed
def repair_all(interactive=True):
 changed=False
 for name in declared():changed=repair_name(name,interactive) or changed
 return changed

def apply(old,new,tid,edit_nix=False):
 LOCAL.mkdir(parents=True,exist_ok=True);lock=LOCK.open('a+');fcntl.flock(lock,fcntl.LOCK_EX)
 txp=transaction(old,new,tid);tx=readj(txp,{'schema':1,'id':tid,'old':old,'new':new,'host':host(),'phases':{},'moves':[],'complete':False})
 def phase(k):tx['phases'][k]=now();writej(txp,tx)
 # The manager owns clean stop, final verified runtime generation, ownership
 # release, and peer handover state. Never mutate names before this succeeds.
 cmd([sys.executable,'-B',str(MANAGER),'stop',old]);phase('stopped-and-backed-up')
 paused=pause_affected()
 try:
  if edit_nix and not tx['phases'].get('nix'):
   replace_decl(old,new);ensure_intent(old,new,tid);phase('nix')
  for src,dst,label in move_plan(old,new)+local_file_moves(old,new):
   if not path_writable(src) or not path_writable(dst):continue
   key='move:'+str(src)
   if tx['phases'].get(key):continue
   if same_path(src,dst):phase(key);continue
   if src.exists():merge_path(src,dst,label);tx['moves'].append({'from':str(src),'to':str(dst),'label':label})
   if dst.is_dir():
    for p in dst.rglob('*.json'):update_exact_json(p,old,new)
   elif dst.suffix=='.json':update_exact_json(dst,old,new)
   phase(key)
  exhaustive_replace(old,new);phase('exhaustive')
  aliases=readj(STATE/'rename-aliases.json',{'schema':1,'aliases':{}});aliases['aliases'][new]=sorted(set(aliases['aliases'].get(new,[])+[old]));writej(STATE/'rename-aliases.json',aliases);phase('aliases')
  tx['complete']=True;tx['rebuild_required']=True;writej(txp,tx)
 finally:resume_folders(paused)
 writej(LOCAL/'accepted-server-names.json',{'schema':1,'names':declared(),'updated_at':now()})
 print(f'Rename {old} -> {new} is complete on {host()}. Rebuild this peer before starting the renamed server.')
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
  stale=stale_owned_names();known=known_old_names(a.new)
  unknown=[x for x in stale if x not in known]
  selected=[]
  if len(unknown)==1:selected=unknown
  elif len(unknown)>1:
   print(f'Select previous name(s) belonging to {a.new} on peer {host()}:')
   [print(f' [{i}] {n}') for i,n in enumerate(unknown,1)]
   raw=input('Comma-separated numbers, or Enter for none: ').strip()
   if raw:selected=[unknown[int(x)-1] for x in raw.split(',')]
  changed=repair_name(a.new,False,False,selected)
  if changed:print(f'Current name {a.new} applied across peer {host()} writable paths.')
  else:print(f'No previous-name paths for {a.new} were found in peer {host()} writable scope.')
  return
 tid=str(uuid.uuid4());print(f'Plan: stop {a.old}; declare {a.new}; rename snapshot, runtime, generations, local backup directory and all local state; preserve archive bytes and record alias metadata.')
 if not a.apply and input('Type RENAME to apply: ').strip()!='RENAME':return
 apply(a.old,a.new,tid,True)
if __name__=='__main__':
 try:main()
 except Exception as e:raise SystemExit(f'ERROR: {e}')
