#!/usr/bin/env python3
"""Per-container floating geometry store used by smart-floating-toggle.sh."""
import argparse, fcntl, json, os, signal, subprocess, time
from pathlib import Path
D=Path(os.environ.get("XDG_STATE_HOME",Path.home()/".local/state"))/"sway/windows"
D.mkdir(parents=True,exist_ok=True,mode=0o700)
STATE=D/"floating-geometry.json";LOCK=D/"floating-geometry.lock";PID=D/"floating-geometry.pid"
def run_json(args):return json.loads(subprocess.check_output(["swaymsg","-r",*args],text=True,stderr=subprocess.DEVNULL))
def walk(n):
 if isinstance(n,dict):
  if n.get("type") in ("con","floating_con") and (n.get("app_id") or n.get("window")):yield n
  for field in ("nodes","floating_nodes"):
   for child in n.get(field,[]):yield from walk(child)
def valid_geom(n):
 try:r=n["rect"];g={k:int(r[k]) for k in ("x","y","width","height")}
 except (KeyError,TypeError,ValueError):return None
 return g if 100<=g["width"]<=16384 and 80<=g["height"]<=16384 else None
def floating(n):return n.get("floating") in ("user_on","auto_on")
def load():
 try:v=json.loads(STATE.read_text());return v if isinstance(v,dict) else {}
 except (OSError,json.JSONDecodeError):return {}
def save(v):
 t=STATE.with_name("."+STATE.name+".tmp");t.write_text(json.dumps(v,indent=2,sort_keys=True)+"\n");os.chmod(t,0o600);os.replace(t,STATE)
def ours(pid):
 try:return "float-state-daemon.py" in Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0",b" ").decode(errors="ignore")
 except (OSError,ValueError):return False
def replace_old():
 try:pid=int(PID.read_text().strip())
 except (OSError,ValueError):return
 if not ours(pid):return
 try:os.kill(pid,signal.SIGTERM)
 except ProcessLookupError:return
 for _ in range(100):
  if not ours(pid):return
  time.sleep(.02)
 try:os.kill(pid,signal.SIGKILL)
 except ProcessLookupError:pass
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--replace",action="store_true");a=ap.parse_args()
 if a.replace:replace_old()
 with LOCK.open("a+") as lock:
  deadline=time.monotonic()+5
  while True:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
   except BlockingIOError:
    if time.monotonic()>=deadline:raise SystemExit("floating-state daemon lock remained busy")
    time.sleep(.05)
  PID.write_text(f"{os.getpid()}\n");os.chmod(PID,0o600);state=load();previous={};running=True
  def stop(_s,_f):
   nonlocal running;running=False
  for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,stop)
  try:
   while running and os.environ.get("SWAYSOCK") and Path(os.environ["SWAYSOCK"]).exists():
    try:nodes=list(walk(run_json(["-t","get_tree"])))
    except (OSError,subprocess.SubprocessError,json.JSONDecodeError):time.sleep(.15);continue
    dirty=False;live=set()
    for n in nodes:
     cid=str(n["id"]);live.add(cid);now=floating(n);was=previous.get(cid)
     # Container IDs distinguish simultaneous windows of the same application.
     if now:
      if was is False and cid in state:
       g=state[cid]
       command=(f'[con_id={cid}] resize set width {g["width"]} px height {g["height"]} px, '
                f'move absolute position {g["x"]} px {g["y"]} px')
       subprocess.run(["swaymsg","--",command],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
      else:
       g=valid_geom(n)
       if g and state.get(cid)!=g:state[cid]=g;dirty=True
     previous[cid]=now
    previous={cid:value for cid,value in previous.items() if cid in live}
    if dirty:save(state)
    time.sleep(.10)
  finally:
   try:
    if PID.read_text().strip()==str(os.getpid()):PID.unlink()
   except OSError:pass
if __name__=="__main__":main()
