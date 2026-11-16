#!/usr/bin/env python3
"""Transactional Minecraft server-name migration for Joel's two-peer layout.

Run with no --apply for a read-only plan. Run on BOTH rt4817 and lt4817,
one peer at a time, while every Minecraft unit is stopped. The script never
rewrites historical tar archives: they are moved into a dated legacy area so
no checksum or restore metadata is falsified. It migrates the live stopped
snapshot/runtime, configuration, coordination and local health state.
"""
from __future__ import annotations
import argparse, datetime as dt, fcntl, json, os, re, shutil, socket, subprocess, sys, time
from pathlib import Path

HOME=Path.home()
ROOT=Path(os.environ.get("MINECRAFT_STATE_ROOT", HOME/"minecraft-server")).expanduser()
BACKUPS=Path(os.environ.get("MINECRAFT_BACKUP_ROOT", HOME/"minecraft-backups")).expanduser()
RUNTIME=Path(os.environ.get("MINECRAFT_RUNTIME_ROOT", "/srv/minecraft"))
LOCAL=HOME/".local/state/minecraft-server"
COORD=ROOT/"coordination"
NIXOS=Path(os.environ.get("NIXOS_DIR", HOME/"nixos")).expanduser()
MINECRAFT_NIX=NIXOS/"rt4817/minecraft.nix"
MANAGER=HOME/".config/sway/scripts/servers/minecraft-server.py"
PEERS=HOME/".config/minecraft-server/peers"
LOCK=LOCAL/"rename.lock"

def run(cmd, check=True, capture=False):
    print("$", " ".join(map(str,cmd)), flush=True)
    return subprocess.run(list(map(str,cmd)), check=check, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None)

def valid(n): return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*",n or ""))
def active(name): return run(["systemctl","is-active","--quiet",f"minecraft-server-{name}.service"],False).returncode==0

def peers():
    out=[]
    if not PEERS.is_file(): return out
    for raw in PEERS.read_text().splitlines():
        raw=raw.split("#",1)[0].strip()
        if not raw: continue
        for x in re.split(r"[\s,]+",raw):
            if x and x not in out: out.append(x)
    return out

def remote_stopped(old,new):
    failures=[]
    for peer in peers():
        q=f"systemctl is-active --quiet minecraft-server-{old}.service || systemctl is-active --quiet minecraft-server-{new}.service"
        r=run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=8",peer,q],False,capture=True)
        # shell returns 0 if either is active, 3 when both inactive
        if r.returncode==0: failures.append(f"{peer}: old or new service is active")
        elif r.returncode not in (3,4): failures.append(f"{peer}: cannot prove both services stopped ({r.returncode}): {(r.stdout or '').strip()}")
    if failures: raise RuntimeError("; ".join(failures))

def exact_replace(path,old,new):
    text=path.read_text()
    before=text
    # Only semantic quoted server identifiers, never broad substring replacement.
    text=text.replace(f'"{old}"',f'"{new}"').replace(f"'{old}'",f"'{new}'")
    if text==before: raise RuntimeError(f"No exact quoted {old!r} found in {path}")
    path.write_text(text)

def json_replace(path,old,new):
    try: data=json.loads(path.read_text())
    except Exception: return
    def walk(x):
        if isinstance(x,dict): return {k:walk(v) for k,v in x.items()}
        if isinstance(x,list): return [walk(v) for v in x]
        return new if x==old else x
    tmp=path.with_suffix(path.suffix+".rename-tmp")
    tmp.write_text(json.dumps(walk(data),sort_keys=True,indent=2)+"\n")
    os.replace(tmp,path)

def paths(old,new,stamp):
    node=socket.gethostname().split(".",1)[0]
    legacy=BACKUPS/node/".legacy-renamed"/f"{old}-to-{new}-{stamp}"
    return [
      (ROOT/"servers"/old, ROOT/"servers"/new, "live synchronized stopped snapshot"),
      (RUNTIME/old, RUNTIME/new, "local runtime"),
      (ROOT/"generations"/old, legacy/"generations", "checksum-bound historical generations"),
      (BACKUPS/node/old, legacy/"backups", "checksum-bound historical backups"),
    ],legacy

def preflight(old,new,moves):
    if old==new or not valid(old) or not valid(new): raise RuntimeError("Names must differ and match [a-z0-9][a-z0-9-]*")
    for f in (MINECRAFT_NIX,MANAGER):
        if not f.is_file(): raise RuntimeError(f"Missing {f}")
    if active(new): raise RuntimeError("The destination server service is already active locally")
    for src,dst,label in moves:
        if dst.exists(): raise RuntimeError(f"Destination already exists for {label}: {dst}")
    if not (ROOT/"servers"/old/"snapshot").is_dir(): raise RuntimeError(f"Missing stopped snapshot: {ROOT/'servers'/old/'snapshot'}")
    trans=[p for p in ROOT.rglob("*") if p.name.endswith((".incoming",".old",".restore-incoming",".restore-old"))]
    if trans: raise RuntimeError("Unfinished transaction paths exist: "+", ".join(map(str,trans[:8])))
    if f'"{new}"' in MINECRAFT_NIX.read_text(): raise RuntimeError(f"New name already exists in {MINECRAFT_NIX}")
    if f'"{old}"' not in MINECRAFT_NIX.read_text(): raise RuntimeError(f"Old name is not declared in {MINECRAFT_NIX}")

def stop_selected_for_rename(name,new):
    manager=HOME/".config/sway/scripts/servers/minecraft-server.py"
    print(f"\nStopping {name} cleanly if it is running on either peer...")
    # The lifecycle manager discovers the current owner, executes the normal
    # RCON save/stop, backup, generation, handover and peer verification path.
    run([sys.executable,"-B",str(manager),"stop",name])
    if active(name) or active(new):
        raise RuntimeError("Old or destination service remains active locally after stop")
    remote_stopped(name,new)

def rebuild_nixos():
    host=socket.gethostname().split(".",1)[0]
    flake=NIXOS
    if not (flake/"flake.nix").is_file():
        raise RuntimeError(f"Missing flake: {flake/'flake.nix'}")
    print(f"\nRebuilding NixOS for {host} from {flake} with --impure...")
    run(["sudo","nixos-rebuild","switch","--impure","--flake",f"{flake}#{host}"])

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("old", nargs="?"); ap.add_argument("new", nargs="?"); ap.add_argument("--apply",action="store_true")
    a=ap.parse_args()
    if (a.old is None) != (a.new is None):
        ap.error("provide both old and new names, or neither for interactive mode")
    if a.old is None:
        manager=HOME/".config/sway/scripts/servers/minecraft-server.py"
        rows=subprocess.run([str(manager),"list"],check=True,text=True,capture_output=True).stdout.splitlines()
        if not rows: raise RuntimeError("No configured Minecraft servers found")
        print("Select the server to rename:")
        for i,name in enumerate(rows,1): print(f"  [{i}] {name}")
        raw=input("Server: ").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= len(rows)): raise RuntimeError("Invalid server selection")
        a.old=rows[int(raw)-1]
        a.new=input(f"New name for {a.old}: ").strip()
        print("\nA safe dry-run will run first. No files have changed yet.")
    stamp=dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    moves,legacy=paths(a.old,a.new,stamp)
    LOCAL.mkdir(parents=True,exist_ok=True)
    with LOCK.open("a+") as lh:
      fcntl.flock(lh,fcntl.LOCK_EX|fcntl.LOCK_NB)
      preflight(a.old,a.new,moves)
      print("\nPLAN")
      for src,dst,label in moves:
          print(f"- {label}: {src} -> {dst}" if src.exists() else f"- {label}: absent, skip {src}")
      print(f"- patch exact quoted identifier in {MINECRAFT_NIX}")
      print(f"- patch exact quoted identifier in {MANAGER} (includes first-start port fallback)")
      print(f"- migrate {a.old}.* local state files to {a.new}.* and update exact JSON values")
      print(f"- preserve checksum-bound history under {legacy}; archives are NOT rewritten")
      print(f"- cleanly stop {a.old} through the lifecycle manager if running on either peer")
      print("- stop/restart Syncthing around atomic moves")
      print(f"- run: sudo nixos-rebuild switch --impure --flake {NIXOS}#{socket.gethostname().split('.',1)[0]}")
      if not a.apply:
          answer=input("\nApply this rename on this peer now? Type RENAME to continue: ").strip()
          if answer != "RENAME":
              print("Cancelled without changes.")
              return
          a.apply=True
      stop_selected_for_rename(a.old,a.new)
      tx=LOCAL/"rename-transactions"/f"{a.old}-to-{a.new}-{stamp}"
      tx.mkdir(parents=True)
      shutil.copy2(MINECRAFT_NIX,tx/"minecraft.nix.before")
      shutil.copy2(MANAGER,tx/"minecraft-server.py.before")
      journal={"schema":1,"old":a.old,"new":a.new,"host":socket.gethostname(),"created":dt.datetime.now(dt.timezone.utc).isoformat(),"moves":[]}
      (tx/"transaction.json").write_text(json.dumps(journal,indent=2)+"\n")
      sync_was_active=run(["systemctl","--user","is-active","--quiet","syncthing.service"],False).returncode==0
      completed=[]
      try:
        if sync_was_active: run(["systemctl","--user","stop","syncthing.service"])
        exact_replace(MINECRAFT_NIX,a.old,a.new)
        exact_replace(MANAGER,a.old,a.new)
        for src,dst,label in moves:
            if not src.exists(): continue
            dst.parent.mkdir(parents=True,exist_ok=True)
            os.rename(src,dst); completed.append((src,dst,label)); journal["moves"].append({"from":str(src),"to":str(dst),"label":label})
            (tx/"transaction.json").write_text(json.dumps(journal,indent=2)+"\n")
        for p in sorted(LOCAL.glob(a.old+".*")):
            dst=p.with_name(a.new+p.name[len(a.old):])
            if dst.exists(): raise RuntimeError(f"Local-state destination exists: {dst}")
            os.rename(p,dst); completed.append((p,dst,"local state"))
            if dst.suffix==".json": json_replace(dst,a.old,a.new)
        # Coordination metadata is not checksum-bound once servers are stopped.
        for p in sorted(COORD.glob(a.old+".*")):
            dst=p.with_name(a.new+p.name[len(a.old):])
            if dst.exists(): raise RuntimeError(f"Coordination destination exists: {dst}")
            os.rename(p,dst); completed.append((p,dst,"coordination"))
            if dst.suffix==".json": json_replace(dst,a.old,a.new)
        marker=legacy/"RENAME.json"; marker.parent.mkdir(parents=True,exist_ok=True)
        marker.write_text(json.dumps({"old":a.old,"new":a.new,"host":socket.gethostname(),"note":"Historical archives retained byte-for-byte; do not rename archive members or alter manifests."},indent=2)+"\n")
        run([sys.executable,"-m","py_compile",str(MANAGER)])
        if shutil.which("nix-instantiate"): run(["nix-instantiate","--parse",str(MINECRAFT_NIX)])
        rebuild_nixos()
        journal["rebuild_complete"]=True
        journal["complete"]=True; (tx/"transaction.json").write_text(json.dumps(journal,indent=2)+"\n")
      except Exception:
        for src,dst,label in reversed(completed):
            if dst.exists() and not src.exists(): src.parent.mkdir(parents=True,exist_ok=True); os.rename(dst,src)
        shutil.copy2(tx/"minecraft.nix.before",MINECRAFT_NIX); shutil.copy2(tx/"minecraft-server.py.before",MANAGER)
        journal["rolled_back"]=True
        (tx/"transaction.json").write_text(json.dumps(journal,indent=2)+"\n")
        print("Rename failed; source and filesystem moves were rolled back.", file=sys.stderr)
        print("Restoring the previously declared NixOS configuration...", file=sys.stderr)
        try: rebuild_nixos()
        except Exception as rollback_error: print(f"WARNING: rollback rebuild also failed: {rollback_error}",file=sys.stderr)
        raise
      finally:
        if sync_was_active: run(["systemctl","--user","start","syncthing.service"],False)
      print("\nRENAMED AND REBUILT LOCALLY WITHOUT REWRITING ANY CHECKSUM-BOUND ARCHIVE.")
      print("Run the same rename on the other peer before starting Minecraft.")
      print(f"After both peers complete successfully, run: minecraft-server start {a.new}")
      print(f"Legacy backup/generation history remains at: {legacy}")
      print(f"Rollback record: {tx}")
if __name__=="__main__":
    try: main()
    except BlockingIOError: raise SystemExit("Another rename transaction is active")
    except Exception as e: raise SystemExit(f"ERROR: {e}")
